from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalog.tests.helpers import product, warehouse
from apps.core.exceptions import ServiceError
from apps.core.models import AuditLog
from apps.core.testing import bootstrap, client_for, make_admin, make_customer, make_staff
from apps.inventory import services
from apps.inventory.models import (
    MovementType, ReservationStatus, StockLevel, StockMovement, StockReservation,
)

B = "/api/v1/admin/"
D = Decimal


@pytest.fixture(autouse=True)
def _setup(db):
    bootstrap()


@pytest.fixture
def admin():
    return client_for(make_admin())


@pytest.fixture
def wh():
    return warehouse("MAIN", is_default=True)


@pytest.fixture
def v():
    return product("Widget", sku="W-1").test_variants[0]


def receive(variant, wh, qty, cost=None):
    return services.move_stock(variant=variant, warehouse=wh, quantity=qty, type=MovementType.PURCHASE_IN,
                               unit_cost=cost, update_cost=cost is not None)


def level(variant, wh):
    return StockLevel.objects.get(variant=variant, warehouse=wh)


# ------------------------------------------------------------ ledger core


def test_move_stock_updates_level_and_appends_ledger(v, wh):
    m = receive(v, wh, 10, 5)
    assert m.balance_after == 10 and m.unit_cost == D("5") and m.quantity == 10
    out = services.move_stock(variant=v, warehouse=wh, quantity=-4, type=MovementType.SALE_OUT)
    assert out.balance_after == 6 and out.unit_cost == D("5")  # leaves at the current average cost
    assert level(v, wh).on_hand == 6
    assert StockMovement.objects.count() == 2


def test_stock_can_never_go_negative(v, wh):
    receive(v, wh, 3)
    with pytest.raises(ServiceError) as e:
        services.move_stock(variant=v, warehouse=wh, quantity=-4, type=MovementType.SALE_OUT)
    assert e.value.code == "insufficient_stock" and e.value.fields["available"] == ["3.000"]
    assert level(v, wh).on_hand == 3 and StockMovement.objects.count() == 1
    with pytest.raises(ServiceError):
        services.move_stock(variant=v, warehouse=wh, quantity=0, type=MovementType.ADJUSTMENT)


def test_database_itself_rejects_negative_and_overreserved_levels(v, wh):
    receive(v, wh, 5)
    for bad in ({"on_hand": D("-1")}, {"reserved": D("6")}):
        with pytest.raises(IntegrityError), transaction.atomic():
            StockLevel.objects.filter(variant=v, warehouse=wh).update(**bad)


def test_ledger_is_append_only(v, wh):
    m = receive(v, wh, 2)
    m.note = "tampered"
    with pytest.raises(PermissionError):
        m.save()
    with pytest.raises(PermissionError):
        m.delete()


def test_weighted_average_cost(v, wh):
    receive(v, wh, 10, 5)
    receive(v, wh, 10, 7)
    v.refresh_from_db()
    assert v.cost_price == D("6.0000")
    receive(v, wh, 20, 9)  # (20*6 + 20*9) / 40 = 7.5
    v.refresh_from_db()
    assert v.cost_price == D("7.5000")
    other = warehouse("B2")  # average is over ALL warehouses
    receive(v, other, 40, 10)  # (40*7.5 + 40*10) / 80 = 8.75
    v.refresh_from_db()
    assert v.cost_price == D("8.7500")


def test_outgoing_and_transfers_do_not_change_cost(v, wh):
    receive(v, wh, 10, 5)
    services.move_stock(variant=v, warehouse=wh, quantity=-2, type=MovementType.SALE_OUT)
    v.refresh_from_db()
    assert v.cost_price == D("5.0000")


def test_untracked_or_inactive_or_deleted_cannot_move(wh):
    untracked = product(track=False).test_variants[0]
    with pytest.raises(ServiceError) as e:
        receive(untracked, wh, 1)
    assert e.value.code == "not_tracked"
    off = warehouse("OFF", is_active=False)
    with pytest.raises(ServiceError) as e:
        receive(product().test_variants[0], off, 1)
    assert e.value.code == "warehouse_inactive"
    gone = product().test_variants[0]
    gone.deleted_at = timezone.now()
    gone.save()
    with pytest.raises(ServiceError) as e:
        receive(gone, wh, 1)
    assert e.value.code == "variant_deleted"


# ------------------------------------------------------------ reservations


def test_reserve_release_and_fulfil(v, wh):
    receive(v, wh, 10, 4)
    r = services.reserve(variant=v, warehouse=wh, quantity=6, ref_type="order", ref_id="1")
    assert (level(v, wh).on_hand, level(v, wh).reserved, level(v, wh).available) == (10, 6, 4)
    with pytest.raises(ServiceError) as e:
        services.reserve(variant=v, warehouse=wh, quantity=5)
    assert e.value.code == "insufficient_stock"
    # reserved stock is protected from other outgoing movements
    with pytest.raises(ServiceError):
        services.move_stock(variant=v, warehouse=wh, quantity=-5, type=MovementType.SALE_OUT)
    services.move_stock(variant=v, warehouse=wh, quantity=-4, type=MovementType.SALE_OUT)  # the free part is fine

    sale = services.fulfill_reservation(r, note="order 1")
    assert sale.type == "sale_out" and sale.quantity == -6 and sale.ref_id == "1"
    lv = level(v, wh)
    assert (lv.on_hand, lv.reserved) == (0, 0)
    with pytest.raises(ServiceError) as e:
        services.fulfill_reservation(r)
    assert e.value.code == "reservation_not_active"


def test_release_and_expiry(v, wh):
    receive(v, wh, 10)
    keep = services.reserve(variant=v, warehouse=wh, quantity=2)
    soon = services.reserve(variant=v, warehouse=wh, quantity=3, expires_at=timezone.now() - timezone.timedelta(minutes=1))
    assert services.expire_reservations() == 1
    soon.refresh_from_db()
    assert soon.status == ReservationStatus.EXPIRED and level(v, wh).reserved == 2
    services.release_reservation(keep)
    assert level(v, wh).reserved == 0
    assert StockReservation.objects.filter(status="active").count() == 0
    with pytest.raises(ServiceError):
        services.reserve(variant=v, warehouse=wh, quantity=0)


# ------------------------------------------------------------ adjustments


def adj_payload(wh, variant, counted, reason="count", **extra):
    line = {"variant": str(variant.uuid), "counted_qty": str(counted), **extra}
    return {"warehouse": str(wh.uuid), "reason": reason, "lines": [line]}


def test_opening_balance_requires_cost_and_sets_average(admin, v, wh):
    r = admin.post(B + "stock/adjustments/", {**adj_payload(wh, v, 10, "opening"), "post": True}, format="json")
    assert r.status_code == 400 and r.data["code"] == "unit_cost_required"
    assert StockMovement.objects.count() == 0
    r = admin.post(B + "stock/adjustments/", {**adj_payload(wh, v, 10, "opening", unit_cost="5.5"), "post": True}, format="json")
    assert r.status_code == 201, r.data
    assert r.data["number"] == "ADJ-000002" or r.data["number"].startswith("ADJ-")
    assert r.data["status"] == "posted" and r.data["lines"][0]["diff"] == "10.000"
    v.refresh_from_db()
    assert v.cost_price == D("5.5000") and level(v, wh).on_hand == 10
    assert StockMovement.objects.get().type == "opening"


def test_stock_count_books_only_the_difference(admin, v, wh):
    receive(v, wh, 10, 2)
    draft = admin.post(B + "stock/adjustments/", adj_payload(wh, v, 7, note="annual count"), format="json")
    assert draft.status_code == 201 and draft.data["status"] == "draft"
    assert level(v, wh).on_hand == 10  # a draft changes nothing
    done = admin.post(f"{B}stock/adjustments/{draft.data['uuid']}/post/")
    assert done.status_code == 200 and done.data["status"] == "posted"
    line = done.data["lines"][0]
    assert (line["system_qty"], line["counted_qty"], line["diff"]) == ("10.000", "7.000", "-3.000")
    mv = StockMovement.objects.filter(type="adjustment").get()
    assert mv.quantity == -3 and mv.balance_after == 7 and mv.ref_type == "adjustment"
    assert admin.post(f"{B}stock/adjustments/{draft.data['uuid']}/post/").data["code"] == "already_posted"
    # counting the same number again books nothing
    again = admin.post(B + "stock/adjustments/", {**adj_payload(wh, v, 7), "post": True}, format="json")
    assert again.data["lines"][0]["diff"] == "0.000" and StockMovement.objects.filter(type="adjustment").count() == 1
    assert AuditLog.objects.filter(action="stock.adjustment_posted").count() == 2


def test_count_below_reserved_is_refused(admin, v, wh):
    receive(v, wh, 10)
    services.reserve(variant=v, warehouse=wh, quantity=6)
    r = admin.post(B + "stock/adjustments/", {**adj_payload(wh, v, 4), "post": True}, format="json")
    assert r.status_code == 400 and r.data["code"] == "below_reserved"
    assert level(v, wh).on_hand == 10


def test_adjustment_validation(admin, v, wh):
    assert admin.post(B + "stock/adjustments/", {"warehouse": str(wh.uuid), "reason": "count", "lines": []}, format="json").status_code == 400
    dup = {"warehouse": str(wh.uuid), "reason": "count", "lines": [
        {"variant": str(v.uuid), "counted_qty": "1"}, {"variant": str(v.uuid), "counted_qty": "2"}]}
    assert admin.post(B + "stock/adjustments/", dup, format="json").data["code"] == "duplicate_line"
    assert admin.post(B + "stock/adjustments/", adj_payload(wh, v, -1), format="json").status_code == 400
    assert admin.post(B + "stock/adjustments/", adj_payload(wh, v, 1, reason="nonsense"), format="json").status_code == 400
    untracked = product(track=False).test_variants[0]
    assert admin.post(B + "stock/adjustments/", adj_payload(wh, untracked, 1), format="json").data["code"] == "not_tracked"


def test_document_numbers_are_sequential(admin, v, wh):
    nums = [admin.post(B + "stock/adjustments/", adj_payload(wh, v, i), format="json").data["number"] for i in range(3)]
    assert nums == ["ADJ-000001", "ADJ-000002", "ADJ-000003"]


def test_adjustment_list_filters_and_permissions(admin, v, wh):
    a = admin.post(B + "stock/adjustments/", adj_payload(wh, v, 1), format="json").data
    admin.post(f"{B}stock/adjustments/{a['uuid']}/post/")
    admin.post(B + "stock/adjustments/", adj_payload(wh, v, 2), format="json")
    assert admin.get(B + "stock/adjustments/?status=draft").data["count"] == 1
    assert admin.get(f"{B}stock/adjustments/{a['uuid']}/").data["number"] == a["number"]
    viewer = client_for(make_staff(["inventory.view"]))
    assert viewer.get(B + "stock/adjustments/").status_code == 200
    assert viewer.post(B + "stock/adjustments/", adj_payload(wh, v, 5), format="json").status_code == 403
    assert viewer.post(f"{B}stock/adjustments/{a['uuid']}/post/").status_code == 403
    assert client_for(make_customer()).get(B + "stock/adjustments/").status_code == 403


# ------------------------------------------------------------ transfers


def test_transfer_lifecycle(admin, v):
    a, b = warehouse("A"), warehouse("B")
    receive(v, a, 10, 3)
    r = admin.post(B + "stock/transfers/", {"from_warehouse": str(a.uuid), "to_warehouse": str(b.uuid),
                   "lines": [{"variant": str(v.uuid), "quantity": "4"}]}, format="json")
    assert r.status_code == 201 and r.data["status"] == "in_transit" and r.data["number"] == "TRF-000001"
    assert level(v, a).on_hand == 6 and not StockLevel.objects.filter(variant=v, warehouse=b).exists()  # in transit
    done = admin.post(f"{B}stock/transfers/{r.data['uuid']}/receive/")
    assert done.data["status"] == "received" and level(v, b).on_hand == 4
    v.refresh_from_db()
    assert v.cost_price == D("3.0000")
    assert admin.post(f"{B}stock/transfers/{r.data['uuid']}/receive/").data["code"] == "invalid_transfer_state"
    assert admin.post(f"{B}stock/transfers/{r.data['uuid']}/cancel/").data["code"] == "invalid_transfer_state"
    types = sorted(StockMovement.objects.values_list("type", flat=True))
    assert types == ["purchase_in", "transfer_in", "transfer_out"]


def test_transfer_cancel_returns_stock(admin, v):
    a, b = warehouse("A"), warehouse("B")
    receive(v, a, 5)
    t = admin.post(B + "stock/transfers/", {"from_warehouse": str(a.uuid), "to_warehouse": str(b.uuid),
                   "lines": [{"variant": str(v.uuid), "quantity": "5"}]}, format="json").data
    assert level(v, a).on_hand == 0
    assert admin.post(f"{B}stock/transfers/{t['uuid']}/cancel/").data["status"] == "cancelled"
    assert level(v, a).on_hand == 5


def test_transfer_validation_and_atomicity(admin, v):
    a, b = warehouse("A"), warehouse("B")
    other = product().test_variants[0]
    receive(v, a, 5)
    receive(other, a, 1)
    mk = lambda **kw: admin.post(B + "stock/transfers/", {"from_warehouse": str(a.uuid), "to_warehouse": str(b.uuid), **kw}, format="json")  # noqa: E731
    assert admin.post(B + "stock/transfers/", {"from_warehouse": str(a.uuid), "to_warehouse": str(a.uuid),
                      "lines": [{"variant": str(v.uuid), "quantity": "1"}]}, format="json").data["code"] == "same_warehouse"
    # second line is short: nothing moves, no number burnt
    r = mk(lines=[{"variant": str(v.uuid), "quantity": "2"}, {"variant": str(other.uuid), "quantity": "9"}])
    assert r.status_code == 400 and r.data["code"] == "insufficient_stock"
    assert level(v, a).on_hand == 5 and StockMovement.objects.filter(type="transfer_out").count() == 0
    ok = mk(lines=[{"variant": str(v.uuid), "quantity": "1"}])
    assert ok.data["number"] == "TRF-000001"
    assert mk(lines=[]).status_code == 400
    assert mk(lines=[{"variant": str(v.uuid), "quantity": "0"}]).status_code == 400


def test_transfer_permissions(admin, v):
    a, b = warehouse("A"), warehouse("B")
    receive(v, a, 5)
    body = {"from_warehouse": str(a.uuid), "to_warehouse": str(b.uuid), "lines": [{"variant": str(v.uuid), "quantity": "1"}]}
    assert client_for(make_staff(["inventory.view"])).post(B + "stock/transfers/", body, format="json").status_code == 403
    mover = client_for(make_staff(["inventory.transfer", "inventory.view"]))
    t = mover.post(B + "stock/transfers/", body, format="json")
    assert t.status_code == 201
    assert mover.get(B + "stock/transfers/?status=in_transit").data["count"] == 1
    assert mover.get(f"{B}stock/transfers/{t.data['uuid']}/").status_code == 200


# ------------------------------------------------------------ read endpoints


def test_stock_listing_filters_and_low_flag(admin, wh):
    low = product("Low", sku="LOW-1").test_variants[0]
    low.reorder_level = D("5")
    low.save()
    fine = product("Fine", sku="FINE-1").test_variants[0]
    other = warehouse("OTHER")
    receive(low, wh, 3)
    receive(fine, wh, 50)
    receive(fine, other, 1)
    product("Never stocked", sku="NONE-1")
    r = admin.get(B + "stock/")
    assert r.data["count"] == 3  # empty levels hidden by default
    row = next(x for x in r.data["results"] if x["variant"]["sku"] == "LOW-1")
    assert row["low_stock"] is True and row["available"] == "3.000" and row["variant"]["product"] == "Low"
    assert admin.get(B + "stock/?low_stock=true").data["count"] == 1
    assert admin.get(B + f"stock/?warehouse={other.uuid}").data["count"] == 1
    assert admin.get(B + "stock/?search=fine").data["count"] == 2
    assert admin.get(B + f"stock/?variant={low.uuid}").data["count"] == 1


def test_count_sheet_lists_every_tracked_variant_with_its_quantity_in_one_warehouse(admin, wh):
    stocked = product("Stocked", sku="CS-1").test_variants[0]
    never = product("Never stocked", sku="CS-2").test_variants[0]
    product("Service", sku="CS-3", track=False)
    gone = product("Gone", sku="CS-4").test_variants[0]
    gone.deleted_at = timezone.now()
    gone.save()
    other = warehouse("OTHER")
    receive(stocked, wh, 10)
    receive(stocked, other, 4)
    services.reserve(variant=stocked, warehouse=wh, quantity=3)
    r = admin.get(B + f"stock/count-sheet/?warehouse={wh.uuid}")
    rows = {x["sku"]: x for x in r.data["results"]}
    assert set(rows) == {"CS-1", "CS-2"}  # untracked and deleted variants are not counted
    assert (rows["CS-1"]["on_hand"], rows["CS-1"]["reserved"], rows["CS-1"]["available"]) == ("10.000", "3.000", "7.000")
    assert (rows["CS-2"]["on_hand"], rows["CS-2"]["available"]) == ("0.000", "0.000") and never
    elsewhere = {x["sku"]: x["on_hand"] for x in admin.get(B + f"stock/count-sheet/?warehouse={other.uuid}").data["results"]}
    assert elsewhere == {"CS-1": "4.000", "CS-2": "0.000"}
    assert admin.get(B + f"stock/count-sheet/?warehouse={wh.uuid}&search=never").data["count"] == 1


def test_count_sheet_needs_a_known_warehouse_and_the_view_permission(admin, wh):
    assert admin.get(B + "stock/count-sheet/").data["code"] == "warehouse_required"
    assert admin.get(B + "stock/count-sheet/?warehouse=" + "0" * 8 + "-0000-0000-0000-" + "0" * 12).status_code == 404
    url = B + f"stock/count-sheet/?warehouse={wh.uuid}"
    assert client_for(make_staff()).get(url).status_code == 403
    assert client_for(make_staff(["inventory.view"])).get(url).status_code == 200
    assert client_for(make_customer()).get(url).status_code == 403
    assert APIClient().get(url).status_code == 401


def test_movements_listing_filters_and_cost_visibility(admin, v, wh):
    receive(v, wh, 10, 5)
    services.move_stock(variant=v, warehouse=wh, quantity=-3, type=MovementType.SALE_OUT)
    r = admin.get(B + "stock/movements/")
    assert [m["type"] for m in r.data["results"]] == ["sale_out", "purchase_in"]  # newest first
    assert r.data["results"][1]["unit_cost"] == "5.0000"
    assert admin.get(B + "stock/movements/?type=sale_out").data["count"] == 1
    assert admin.get(B + f"stock/movements/?variant={v.uuid}").data["count"] == 2
    assert admin.get(B + "stock/movements/?from=2999-01-01").data["count"] == 0
    plain = client_for(make_staff(["inventory.view"]))
    assert plain.get(B + "stock/movements/").data["results"][0]["unit_cost"] is None
    costly = client_for(make_staff(["inventory.view", "inventory.view_cost"]))
    assert costly.get(B + "stock/movements/").data["results"][1]["unit_cost"] == "5.0000"
    assert client_for(make_staff()).get(B + "stock/movements/").status_code == 403


# ------------------------------------------------------------ warehouses


def test_warehouse_crud_default_and_deactivation(admin, v):
    r = admin.post(B + "warehouses/", {"code": "MAIN", "name": "Main", "is_default": True}, format="json")
    assert r.status_code == 201 and r.data["is_default"] is True
    second = admin.post(B + "warehouses/", {"code": "SEC", "name": "Second", "is_default": True}, format="json")
    assert {w["code"]: w["is_default"] for w in admin.get(B + "warehouses/").data} == {"MAIN": False, "SEC": True}
    assert admin.post(B + "warehouses/", {"code": "MAIN", "name": "dup"}, format="json").status_code == 400
    receive(v, warehouse_obj(second.data["uuid"]), 2)
    r = admin.patch(f"{B}warehouses/{second.data['uuid']}/", {"is_active": False}, format="json")
    assert r.status_code == 400 and r.data["code"] == "warehouse_has_stock"
    assert admin.patch(f"{B}warehouses/{r.data and admin.get(B + 'warehouses/').data[0]['uuid']}/", {"name": "Renamed"}, format="json").status_code == 200
    assert client_for(make_staff(["inventory.view"])).post(B + "warehouses/", {"code": "X", "name": "x"}, format="json").status_code == 403
    assert client_for(make_staff(["inventory.view"])).get(B + "warehouses/").status_code == 200


def warehouse_obj(uuid):
    from apps.inventory.models import Warehouse

    return Warehouse.objects.get(uuid=uuid)
