from datetime import date
from decimal import Decimal

import pytest
from django.core.management import call_command

from apps.accounting.models import Account, JournalEntry, TaxRate
from apps.catalog.tests.helpers import product, warehouse
from apps.core.models import AuditLog
from apps.core.testing import bootstrap, client_for, make_admin, make_customer, make_staff
from apps.inventory import services as stock
from apps.inventory.models import MovementType, StockLevel
from apps.sales.models import Sale, Shift

B = "/api/v1/admin/pos/"
D = Decimal


@pytest.fixture(autouse=True)
def _setup(db):
    bootstrap()
    call_command("seed_accounting", year=date.today().year, verbosity=0)


@pytest.fixture
def wh():
    return warehouse("MAIN", is_default=True)


@pytest.fixture
def item(wh):
    """A priced, stocked item: 20 in stock at average cost 4, retail price 10."""
    p = product("Dish Soap", sku="DS-1", price="10.00")
    v = p.test_variants[0]
    stock.move_stock(variant=v, warehouse=wh, quantity=20, type=MovementType.PURCHASE_IN, unit_cost=4, update_cost=True)
    return v


@pytest.fixture
def cashier_user():
    return make_staff(roles=["cashier"])


@pytest.fixture
def cashier(cashier_user):
    return client_for(cashier_user)


@pytest.fixture
def admin():
    return client_for(make_admin())


@pytest.fixture
def shift(cashier, wh):
    r = cashier.post(B + "shift/open/", {"warehouse": str(wh.uuid), "opening_cash": "100"}, format="json")
    assert r.status_code == 201, r.json()
    return r.json()


def sale(client, variant, qty="1", payments=None, **extra):
    body = {"lines": [{"variant": str(variant.uuid), "quantity": qty}],
            "payments": payments or [{"method": "cash", "amount": "100"}], **extra}
    return client.post(B + "sales/", body, format="json")


def level(v, wh):
    return StockLevel.objects.get(variant=v, warehouse=wh).on_hand


def acct_balance(code):
    from apps.accounting import reports
    rows = reports.trial_balance(from_date=date(date.today().year, 1, 1), to_date=date.today(), lang="en")["rows"]
    row = next((r for r in rows if r["account"]["code"] == code), None)
    if row is None:
        return D("0")
    c = row["closing"]
    return D(c["debit"]) - D(c["credit"])


# ------------------------------------------------------------ permissions


def test_permissions_are_enforced(item, shift, cashier):
    customer = client_for(make_customer())
    no_perm = client_for(make_staff())
    for path in ("config/", "products/", "shift/"):
        assert customer.get(B + path).status_code == 403
        assert no_perm.get(B + path).status_code == 403
    assert no_perm.post(B + "sales/", {}, format="json").status_code == 403
    # a cashier cannot see shift history, refund, or hand out discounts
    assert cashier.get(B + "shifts/").status_code == 403
    r = sale(cashier, item, "1")
    assert r.status_code == 201
    assert cashier.post(B + f"sales/{r.json()['uuid']}/refund/", {"reason": "x"}, format="json").status_code == 403
    r = cashier.post(B + "sales/", {"lines": [{"variant": str(item.uuid), "quantity": "1", "discount": "1"}],
                                   "payments": [{"method": "cash", "amount": "10"}]}, format="json")
    assert r.status_code == 403 and r.json()["code"] == "discount_not_allowed"


def test_config_lists_what_the_till_needs(cashier, wh):
    r = cashier.get(B + "config/")
    d = r.json()
    assert r.status_code == 200
    assert d["currency"] == "ILS" and d["shift"] is None
    assert d["default_warehouse"] == str(wh.uuid)
    assert d["can_discount"] is False and d["can_refund"] is False


# ------------------------------------------------------------ shifts


def test_one_open_shift_per_cashier_and_sale_needs_a_shift(cashier, wh, item):
    assert sale(cashier, item).json()["code"] == "shift_required"
    r = cashier.post(B + "shift/open/", {"warehouse": str(wh.uuid), "opening_cash": "50"}, format="json")
    assert r.status_code == 201 and r.json()["number"].startswith("SH-")
    again = cashier.post(B + "shift/open/", {"warehouse": str(wh.uuid)}, format="json")
    assert again.status_code == 400 and again.json()["code"] == "shift_already_open"
    cur = cashier.get(B + "shift/").json()
    assert cur["shift"]["status"] == "open" and cur["report"]["sales_count"] == 0


def test_closing_a_shift_reconciles_cash(cashier, shift, item):
    sale(cashier, item, "2", [{"method": "cash", "amount": "20"}])  # +20 cash
    sale(cashier, item, "1", [{"method": "card", "amount": "10"}])  # card: not in the drawer
    r = cashier.post(B + "shift/close/", {"counted_cash": "115"}, format="json")
    assert r.status_code == 200, r.json()
    s = r.json()["shift"]
    assert s["status"] == "closed" and s["expected_cash"] == "120.00" and s["difference"] == "-5.00"
    rep = r.json()["report"]
    assert rep["sales_count"] == 2 and rep["by_method"]["cash"] == "20.00" and rep["by_method"]["card"] == "10.00"
    assert cashier.post(B + "shift/close/", {"counted_cash": "0"}, format="json").json()["code"] == "shift_not_open"
    assert sale(cashier, item).json()["code"] == "shift_required"  # closed: cannot sell


# ------------------------------------------------------------ selling


def test_cash_sale_with_change_moves_stock_and_posts_the_books(cashier, shift, item, wh):
    r = sale(cashier, item, "3", [{"method": "cash", "amount": "50"}])
    assert r.status_code == 201, r.json()
    d = r.json()
    assert d["number"].startswith("POS-") and d["total"] == "30.00" and d["status"] == "completed"
    assert d["cash_tendered"] == "50.00" and d["change_due"] == "20.00"
    assert d["payments"] == [{"method": "cash", "amount": "30.00", "reference": ""}]  # change is not a payment
    assert d["lines"][0]["unit_price"] == "10.00" and d["lines"][0]["line_total"] == "30.00"
    assert "cost" not in str(d)
    assert level(item, wh) == 17

    entry = JournalEntry.objects.get(number=d["journal_entry"])
    assert entry.status == "posted" and entry.source_type == "pos_sale" and entry.source_id == d["number"]
    totals = {}
    for ln in entry.lines.select_related("account"):
        totals[ln.account.code] = totals.get(ln.account.code, D(0)) + ln.debit - ln.credit
    assert totals["1110"] == D("30")       # cash in the drawer (no change)
    assert totals["4100"] == D("-30")      # revenue
    assert totals["5100"] == D("12") and totals["1140"] == D("-12")  # 3 x cost 4
    assert sum(ln.debit for ln in entry.lines.all()) == sum(ln.credit for ln in entry.lines.all())
    assert AuditLog.objects.filter(action="pos.sale_completed").count() == 1


def test_tax_inclusive_pricing_splits_revenue_and_tax(cashier, shift, item):
    rate = TaxRate.objects.create(name="VAT", rate=D("16"), is_active=True,
                                  collected_account=Account.objects.get(code="2120"),
                                  paid_account=Account.objects.get(code="1150"))
    r = sale(cashier, item, "10", [{"method": "card", "amount": "100"}], tax_rate=str(rate.uuid))
    d = r.json()
    assert r.status_code == 201, d
    assert d["total"] == "100.00" and d["tax"] == {"name": "VAT", "rate": "16.0000", "amount": "13.79"}
    assert d["net_total"] == "86.21"
    entry = JournalEntry.objects.get(number=d["journal_entry"])
    by = {ln.account.code: ln.debit - ln.credit for ln in entry.lines.select_related("account")}
    assert by["1120"] == D("100") and by["4100"] == D("-86.21") and by["2120"] == D("-13.79")


def test_mixed_payment_and_split_between_cash_and_bank(cashier, shift, item):
    r = sale(cashier, item, "5", [{"method": "cash", "amount": "20"}, {"method": "card", "amount": "30", "reference": "A1"}])
    d = r.json()
    assert r.status_code == 201 and d["change_due"] == "0.00"
    assert [p["method"] for p in d["payments"]] == ["cash", "card"] and d["payments"][1]["reference"] == "A1"


def test_payment_rules(cashier, shift, item):
    short = sale(cashier, item, "2", [{"method": "cash", "amount": "10"}])
    assert short.status_code == 400 and short.json()["code"] == "payment_insufficient"
    assert short.json()["fields"]["total"] == ["20.00"]
    # change may only come out of cash
    over_card = sale(cashier, item, "1", [{"method": "card", "amount": "15"}])
    assert over_card.status_code == 400 and over_card.json()["code"] == "change_exceeds_cash"
    assert Sale.objects.count() == 0


def test_insufficient_stock_leaves_no_trace(cashier, shift, item, wh):
    r = sale(cashier, item, "25", [{"method": "cash", "amount": "250"}])
    assert r.status_code == 400 and r.json()["code"] == "insufficient_stock"
    assert level(item, wh) == 20 and Sale.objects.count() == 0
    assert JournalEntry.objects.filter(source_type="pos_sale").count() == 0


def test_closed_period_blocks_the_whole_sale(cashier, shift, item, wh):
    from apps.accounting.models import FiscalPeriod
    FiscalPeriod.objects.filter(start_date__lte=date.today(), end_date__gte=date.today()).update(status="closed")
    r = sale(cashier, item)
    assert r.status_code == 400 and r.json()["code"] == "period_closed"
    assert level(item, wh) == 20 and Sale.objects.count() == 0


def test_unpriced_inactive_and_untracked_items(cashier, shift, wh):
    no_price = product("Unpriced").test_variants[0]
    r = sale(cashier, no_price)
    assert r.status_code == 400 and r.json()["code"] == "no_price"
    draft = product("Draft", status="draft", price="5").test_variants[0]
    assert sale(cashier, draft).json()["code"] == "not_sellable"
    service = product("Gift wrapping", track=False, price="5").test_variants[0]
    r = sale(cashier, service, "1", [{"method": "cash", "amount": "5"}])
    assert r.status_code == 201  # no stock movement, no cost lines
    assert not JournalEntry.objects.get(number=r.json()["journal_entry"]).lines.filter(account__code="5100").exists()


def test_discounts_need_permission_and_stay_within_the_line(item, shift, cashier_user, wh):
    boss = client_for(make_staff(["sales.pos", "sales.discount"]))
    boss.post(B + "shift/open/", {"warehouse": str(wh.uuid)}, format="json")
    ok = boss.post(B + "sales/", {"lines": [{"variant": str(item.uuid), "quantity": "2", "discount": "5"}],
                                  "payments": [{"method": "cash", "amount": "15"}]}, format="json")
    assert ok.status_code == 201 and ok.json()["total"] == "15.00" and ok.json()["discount_total"] == "5.00"
    too_much = boss.post(B + "sales/", {"lines": [{"variant": str(item.uuid), "quantity": "1", "discount": "11"}],
                                        "payments": [{"method": "cash", "amount": "10"}]}, format="json")
    assert too_much.status_code == 400 and too_much.json()["code"] == "discount_too_large"
    free = boss.post(B + "sales/", {"lines": [{"variant": str(item.uuid), "quantity": "1", "discount": "10"}],
                                    "payments": [{"method": "cash", "amount": "1"}]}, format="json")
    assert free.json()["code"] == "empty_sale"


def test_sale_can_be_linked_to_a_customer(cashier, shift, item):
    cust = make_customer()
    r = sale(cashier, item, customer=str(cust.uuid))
    assert r.json()["customer"]["phone"] == cust.phone
    assert sale(cashier, item, customer="00000000-0000-0000-0000-000000000000").status_code == 404


# ------------------------------------------------------------ refund


def test_refund_restores_stock_reverses_the_books_and_pays_back_cash(cashier_user, wh, item):
    boss_user = make_staff(["sales.pos", "sales.refund", "sales.view"])
    boss = client_for(boss_user)
    boss.post(B + "shift/open/", {"warehouse": str(wh.uuid), "opening_cash": "0"}, format="json")
    s = sale(boss, item, "4", [{"method": "cash", "amount": "40"}]).json()
    assert level(item, wh) == 16 and acct_balance("4100") == D("-40")

    r = boss.post(B + f"sales/{s['uuid']}/refund/", {"reason": "wrong item"}, format="json")
    assert r.status_code == 200, r.json()
    d = r.json()
    assert d["status"] == "refunded" and d["refund"]["reason"] == "wrong item"
    assert level(item, wh) == 20
    assert acct_balance("4100") == D("0") and acct_balance("1110") == D("0") and acct_balance("1140") == D("0")
    assert boss.post(B + f"sales/{s['uuid']}/refund/", {"reason": "again"}, format="json").json()["code"] == "sale_already_refunded"
    assert boss.post(B + f"sales/{s['uuid']}/refund/", {"reason": " "}, format="json").status_code == 400

    # the refunded cash leaves the drawer: sold 40, paid back 40
    closed = boss.post(B + "shift/close/", {"counted_cash": "0"}, format="json").json()
    assert closed["shift"]["expected_cash"] == "0.00" and closed["shift"]["difference"] == "0.00"
    assert closed["report"]["refunds_count"] == 1 and closed["report"]["refunds_by_method"]["cash"] == "40.00"


def test_refund_needs_an_open_shift(wh, item):
    boss = client_for(make_staff(["sales.pos", "sales.refund"]))
    boss.post(B + "shift/open/", {"warehouse": str(wh.uuid)}, format="json")
    s = sale(boss, item).json()
    boss.post(B + "shift/close/", {"counted_cash": "100"}, format="json")
    r = boss.post(B + f"sales/{s['uuid']}/refund/", {"reason": "x"}, format="json")
    assert r.status_code == 400 and r.json()["code"] == "shift_required"


# ------------------------------------------------------------ catalog search + history


def test_product_search_for_the_till(cashier, shift, item, wh):
    other = product("Glass Cleaner", sku="GC-1", price="7.50").test_variants[0]
    other.barcode = "7290001234567"
    other.save()
    hidden = product("Hidden by price")  # no price: not sellable
    r = cashier.get(B + "products/?q=soap").json()
    assert r["count"] == 1 and r["results"][0]["sku"] == "DS-1"
    assert r["results"][0]["price"] == "10.00" and r["results"][0]["available"] == "20.000"
    assert cashier.get(B + "products/?barcode=7290001234567").json()["results"][0]["sku"] == "GC-1"
    assert cashier.get(B + "products/").json()["count"] == 2
    assert hidden.test_variants[0].sku not in str(cashier.get(B + "products/").json())


def test_cashier_sees_only_own_sales_while_managers_see_all(cashier, shift, item, wh):
    mine = sale(cashier, item).json()
    other = client_for(make_staff(["sales.pos"]))
    other.post(B + "shift/open/", {"warehouse": str(wh.uuid)}, format="json")
    theirs = sale(other, item).json()
    assert [x["number"] for x in cashier.get(B + "sales/").json()["results"]] == [mine["number"]]
    assert cashier.get(B + f"sales/{theirs['uuid']}/").status_code == 404
    manager = client_for(make_staff(["sales.view"]))
    assert manager.get(B + "sales/").json()["count"] == 2
    assert manager.get(B + "shifts/").json()["count"] == 2
    detail = manager.get(B + f"shifts/{Shift.objects.first().uuid}/").json()
    assert "report" in detail and detail["report"]["sales_count"] == 1


def test_document_numbers_are_sequential(cashier, shift, item):
    a, b = sale(cashier, item).json(), sale(cashier, item).json()
    assert a["number"] == "POS-000001" and b["number"] == "POS-000002"


# ------------------------------------------------------------ sales report

R = "/api/v1/admin/reports/sales/"


@pytest.fixture
def vat():
    return TaxRate.objects.create(name="VAT", rate=D("16"), is_active=True,
                                  collected_account=Account.objects.get(code="2120"),
                                  paid_account=Account.objects.get(code="1150"))


@pytest.fixture
def day_of_sales(wh, item, vat):
    """Cashier sells 3 (tax 16%) and 2 (no tax, 1 discounted by a manager), then the second is refunded."""
    boss = client_for(make_staff(["sales.pos", "sales.discount", "sales.refund"]))
    boss.post(B + "shift/open/", {"warehouse": str(wh.uuid), "opening_cash": "0"}, format="json")
    a = sale(boss, item, "3", [{"method": "cash", "amount": "30"}], tax_rate=str(vat.uuid)).json()
    b = sale(boss, item, "2", [{"method": "card", "amount": "20"}]).json()
    c = boss.post(B + "sales/", {"lines": [{"variant": str(item.uuid), "quantity": "1", "discount": "2"}],
                                 "payments": [{"method": "cash", "amount": "8"}]}, format="json").json()
    boss.post(B + f"sales/{b['uuid']}/refund/", {"reason": "x"}, format="json")
    return a, b, c


def test_report_totals_exclude_refunded_sales_and_split_the_tax(day_of_sales):
    viewer = client_for(make_staff(["reports.sales"]))
    r = viewer.get(R)
    d = r.json()
    assert r.status_code == 200 and d["currency"] == "ILS" and d["cost_visible"] is False
    s = d["summary"]
    assert s["sales_count"] == 2                      # the refunded one is not revenue
    assert s["gross"] == "40.00" and s["discounts"] == "2.00" and s["total"] == "38.00"
    assert s["tax"] == "4.14" and s["net"] == "33.86"  # 30 * 16/116 = 4.14
    assert s["items_sold"] == "4" and s["avg_ticket"] == "19.00"
    assert s["refunds_count"] == 1 and s["refunds_total"] == "20.00"
    assert s["cost"] is None and s["profit"] is None   # no inventory.view_cost
    assert len(d["hourly"]) == 24 and sum(h["sales_count"] for h in d["hourly"]) == 2
    assert d["payments"] == [{"method": "cash", "count": 2, "amount": "38.00"}]
    assert d["cashiers"][0]["sales_count"] == 2 and d["cashiers"][0]["total"] == "38.00"
    today = d["daily"][-1]
    assert today["sales_count"] == 2 and today["total"] == "38.00" and today["refunds_count"] == 1
    p = d["products"][0]
    assert p["sku"] == "DS-1" and p["quantity"] == "4" and p["orders"] == 2 and p["cost"] is None


def test_cost_and_profit_only_for_those_who_may_see_costs(day_of_sales):
    d = client_for(make_staff(["reports.sales", "inventory.view_cost"])).get(R).json()
    s = d["summary"]
    assert d["cost_visible"] is True and s["cost"] == "16.00"          # 4 items at average cost 4
    assert s["profit"] == "17.86" and s["margin_pct"] == "52.75"        # 33.86 - 16.00
    assert d["products"][0]["profit"] == "17.86" and d["categories"][0]["profit"] == "17.86"


def test_report_range_filters_and_empty_days(day_of_sales):
    viewer = client_for(make_staff(["reports.sales"]))
    past = viewer.get(R + "?from=2020-01-01&to=2020-01-03").json()
    assert past["summary"]["sales_count"] == 0 and past["summary"]["total"] == "0.00"
    assert [x["date"] for x in past["daily"]] == ["2020-01-01", "2020-01-02", "2020-01-03"]  # zeros, no holes
    assert past["products"] == [] and past["cashiers"] == []
    other_wh = warehouse("OTHER")
    assert viewer.get(R + f"?warehouse={other_wh.uuid}").json()["summary"]["sales_count"] == 0
    assert viewer.get(R + f"?cashier={make_staff().uuid}").json()["summary"]["sales_count"] == 0
    assert viewer.get(R + "?from=2026-02-01&to=2026-01-01").json()["code"] == "invalid_range"
    assert viewer.get(R + "?from=nope").json()["code"] == "invalid_date"
    assert viewer.get(R + "?from=2015-01-01&to=2026-01-01").json()["code"] == "range_too_large"


def test_report_permissions_and_csv_export(day_of_sales, cashier):
    assert cashier.get(R).status_code == 403
    assert client_for(make_customer()).get(R).status_code == 403
    no_export = client_for(make_staff(["reports.sales"]))
    assert no_export.get(R + "?export=csv").status_code == 403
    exporter = client_for(make_staff(["reports.sales", "reports.export"]))
    for section in ("daily", "products", "categories", "cashiers", "payments", "invoices"):
        r = exporter.get(R + f"?export=csv&section={section}")
        assert r.status_code == 200 and r["Content-Type"].startswith("text/csv")
        assert r.content.startswith("\ufeff".encode())
    invoices = exporter.get(R + "?export=csv&section=invoices").content.decode("utf-8-sig").splitlines()
    assert len(invoices) == 1 + 3 and invoices[0].startswith("number,date,status")  # 3 sales, refunded included
    assert exporter.get(R + "?export=csv&section=nope").status_code == 400
    assert exporter.get(R + "?export=pdf").status_code == 400


def test_report_buckets_days_and_hours_by_the_shop_clock(wh, item):
    from datetime import datetime, timezone as dt_tz

    boss = client_for(make_staff(["sales.pos"]))
    boss.post(B + "shift/open/", {"warehouse": str(wh.uuid)}, format="json")
    sale(boss, item, "1", [{"method": "cash", "amount": "10"}])
    # 22:30 UTC on 14 Jan is 00:30 on 15 Jan in Palestine (UTC+2 in winter): the shop's day is the 15th
    Sale.objects.update(created_at=datetime(2026, 1, 14, 22, 30, tzinfo=dt_tz.utc))
    d = client_for(make_staff(["reports.sales"])).get(R + "?from=2026-01-14&to=2026-01-15").json()
    by_day = {x["date"]: x["sales_count"] for x in d["daily"]}
    assert by_day == {"2026-01-14": 0, "2026-01-15": 1}
    assert d["hourly"][0]["sales_count"] == 1
