import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image

from apps.catalog.models import Brand, Category, PriceList, Product, ProductImage, ProductVariant
from apps.catalog.tests.helpers import price_list, product, warehouse
from apps.core.models import AuditLog
from apps.core.testing import bootstrap, client_for, make_admin, make_customer, make_staff
from apps.inventory import services as stock
from apps.inventory.models import MovementType

B = "/api/v1/admin/"


@pytest.fixture(autouse=True)
def _setup(db, tmp_path, settings):
    bootstrap()
    settings.MEDIA_ROOT = str(tmp_path)


@pytest.fixture
def admin():
    return client_for(make_admin())


@pytest.fixture
def pl():
    return price_list("ILS")


def png(size=(4, 4), fmt="PNG", name="a.png"):
    buf = io.BytesIO()
    Image.new("RGB", size, "red").save(buf, fmt)
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def new_product_payload(pl, **extra):
    return {
        "name": "Blue Shirt", "description": "Cotton", "brand": str(Brand.objects.get_or_create(name="Acme")[0].uuid),
        "variants": [
            {"sku": "SH-BL-L", "name": "Blue / L", "attributes": {"color": "blue", "size": "L"},
             "reorder_level": "5", "prices": [{"price_list": str(pl.uuid), "price": "25.00"}]},
            {"sku": "SH-BL-M", "name": "Blue / M", "attributes": {"color": "blue", "size": "M"}},
        ], **extra}


# ------------------------------------------------------------ permissions


def test_permissions_matrix(pl):
    assert client_for(make_customer()).get(B + "products/").status_code == 403
    assert client_for(make_staff()).get(B + "products/").status_code == 403
    viewer = client_for(make_staff(["products.view"]))
    assert viewer.get(B + "products/").status_code == 200
    assert viewer.post(B + "products/", new_product_payload(pl), format="json").status_code == 403
    creator = client_for(make_staff(["products.create", "products.view"]))
    assert creator.post(B + "products/", new_product_payload(pl), format="json").status_code == 201


# ------------------------------------------------------------ create / read


def test_create_product_with_variants_and_prices(admin, pl):
    r = admin.post(B + "products/", new_product_payload(pl), format="json")
    assert r.status_code == 201, r.data
    d = r.data
    assert d["status"] == "draft" and d["is_visible_on_store"] is False and d["slug"] == "blue-shirt"
    assert [v["sku"] for v in d["variants"]] == ["SH-BL-L", "SH-BL-M"]
    assert d["variants"][0]["prices"][0]["price"] == "25.0000"
    assert d["variants"][0]["prices"][0]["currency"] == "ILS"
    assert d["variants"][0]["stock"] == {"on_hand": "0.000", "reserved": "0.000", "available": "0.000"}
    assert d["variants"][0]["cost_price"] == "0.0000"  # admin may see cost
    assert AuditLog.objects.filter(action="product.created").exists()
    # same name -> distinct slug
    assert admin.post(B + "products/", {**new_product_payload(pl), "variants": []}, format="json").data["slug"] == "blue-shirt-2"


def test_cost_price_is_hidden_without_view_cost(admin, pl):
    uuid = admin.post(B + "products/", new_product_payload(pl), format="json").data["uuid"]
    plain = client_for(make_staff(["products.view"]))
    assert plain.get(f"{B}products/{uuid}/").data["variants"][0]["cost_price"] is None
    costly = client_for(make_staff(["products.view", "inventory.view_cost"]))
    assert costly.get(f"{B}products/{uuid}/").data["variants"][0]["cost_price"] == "0.0000"


def test_sku_and_barcode_must_be_unique_case_insensitively(admin, pl):
    admin.post(B + "products/", new_product_payload(pl), format="json")
    dup = {"name": "Other", "variants": [{"sku": "sh-bl-l"}]}
    r = admin.post(B + "products/", dup, format="json")
    assert r.status_code == 400 and r.data["code"] == "sku_taken"
    assert not Product.objects.filter(name="Other").exists()  # whole create rolled back
    twice = {"name": "Twice", "variants": [{"sku": "X1"}, {"sku": "x1"}]}
    assert admin.post(B + "products/", twice, format="json").data["code"] == "sku_taken"
    bc = {"name": "B1", "variants": [{"sku": "B1", "barcode": "123"}]}
    assert admin.post(B + "products/", bc, format="json").status_code == 201
    bc2 = {"name": "B2", "variants": [{"sku": "B2", "barcode": "123"}]}
    assert admin.post(B + "products/", bc2, format="json").data["code"] == "barcode_taken"


def test_validation_errors(admin):
    assert admin.post(B + "products/", {}, format="json").status_code == 400
    r = admin.post(B + "products/", {"name": "N", "variants": [{"sku": "S", "prices": [
        {"price_list": "00000000-0000-0000-0000-000000000000", "price": "1"}]}]}, format="json")
    assert r.status_code == 400
    r = admin.post(B + "products/", {"name": "N", "variants": [{"sku": "S2", "prices": [
        {"price_list": str(price_list().uuid), "price": "-1"}]}]}, format="json")
    assert r.status_code == 400


# ------------------------------------------------------------ lifecycle & visibility


def test_publish_show_hide_archive_cycle(admin, pl):
    uuid = admin.post(B + "products/", new_product_payload(pl), format="json").data["uuid"]
    base = f"{B}products/{uuid}/"
    r = admin.post(base + "publish/")
    assert r.data["status"] == "active" and r.data["is_visible_on_store"] is False
    assert admin.post(base + "show/").data["is_visible_on_store"] is True
    assert admin.post(base + "hide/").data["is_visible_on_store"] is False
    admin.post(base + "show/")
    r = admin.post(base + "archive/")
    assert r.data["status"] == "archived" and r.data["is_visible_on_store"] is False
    assert admin.post(base + "publish/").data["status"] == "active"  # un-archive


def test_publish_requires_an_active_variant(admin):
    uuid = admin.post(B + "products/", {"name": "Empty"}, format="json").data["uuid"]
    r = admin.post(f"{B}products/{uuid}/publish/")
    assert r.status_code == 400 and r.data["code"] == "no_variants"


def test_lifecycle_permissions():
    p = product(visible=False)
    base = f"{B}products/{p.uuid}/"
    only_view = client_for(make_staff(["products.view"]))
    for action in ("publish", "archive", "show", "hide"):
        assert only_view.post(base + action + "/").status_code == 403
    assert client_for(make_staff(["products.view", "products.toggle_visibility"])).post(base + "show/").status_code == 200
    assert client_for(make_staff(["products.toggle_visibility"])).post(base + "archive/").status_code == 403


def test_bulk_actions(admin):
    ps = [product(visible=False) for _ in range(3)]
    ids = [str(p.uuid) for p in ps]
    r = admin.post(B + "products/bulk/", {"ids": ids, "action": "show"}, format="json")
    assert r.status_code == 200 and r.data == {"action": "show", "updated": 3}
    assert Product.objects.filter(uuid__in=ids, is_visible_on_store=True).count() == 3
    admin.post(B + "products/bulk/", {"ids": ids[:2], "action": "archive"}, format="json")
    assert Product.objects.filter(uuid__in=ids, status="archived").count() == 2
    r = admin.post(B + "products/bulk/", {"ids": ids + ["00000000-0000-0000-0000-000000000000"], "action": "hide"}, format="json")
    assert r.status_code == 404 and r.data["code"] == "unknown_products"
    assert admin.post(B + "products/bulk/", {"ids": ids, "action": "explode"}, format="json").status_code == 400
    toggler = client_for(make_staff(["products.toggle_visibility"]))
    assert toggler.post(B + "products/bulk/", {"ids": ids, "action": "archive"}, format="json").status_code == 403
    assert toggler.post(B + "products/bulk/", {"ids": ids, "action": "hide"}, format="json").status_code == 200


# ------------------------------------------------------------ update & delete


def test_update_product_and_name_changes_slug(admin):
    p = product(name="Old Name")
    r = admin.patch(f"{B}products/{p.uuid}/", {"name": "New Name", "brand": str(Brand.objects.create(name="B").uuid), "status": "archived",
                                               "is_visible_on_store": True}, format="json")
    assert r.status_code == 200 and r.data["name"] == "New Name" and r.data["slug"] == "new-name"
    assert r.data["status"] == "active"  # status / visibility are NOT writable here
    assert admin.put(f"{B}products/{p.uuid}/", {}, format="json").status_code in (403, 405)  # PUT unsupported


def test_delete_is_soft_and_blocked_by_stock_history(admin):
    clean = product()
    assert admin.delete(f"{B}products/{clean.uuid}/").status_code == 204
    clean.refresh_from_db()
    assert clean.deleted_at and clean.is_visible_on_store is False
    assert admin.get(f"{B}products/{clean.uuid}/").status_code == 404
    assert admin.get(B + "products/").data["count"] == 0

    used = product()
    stock.move_stock(variant=used.test_variants[0], warehouse=warehouse(), quantity=5,
                     type=MovementType.PURCHASE_IN, unit_cost=2, update_cost=True)
    r = admin.delete(f"{B}products/{used.uuid}/")
    assert r.status_code == 400 and r.data["code"] == "product_in_use"
    used.refresh_from_db()
    assert used.deleted_at is None
    assert admin.post(f"{B}products/{used.uuid}/archive/").status_code == 200  # the suggested alternative


def test_tracking_cannot_be_disabled_after_stock_exists(admin):
    p = product()
    stock.move_stock(variant=p.test_variants[0], warehouse=warehouse(), quantity=1, type=MovementType.PURCHASE_IN)
    r = admin.patch(f"{B}products/{p.uuid}/", {"track_inventory": False}, format="json")
    assert r.status_code == 400 and r.data["code"] == "tracking_locked"


# ------------------------------------------------------------ variants & prices


def test_variant_crud_and_rules(admin, pl):
    p = product(variants=1)
    base = f"{B}products/{p.uuid}/variants/"
    r = admin.post(base, {"sku": "NEW-1", "name": "New", "attributes": {"c": "red"}, "weight": "1.5",
                          "prices": [{"price_list": str(pl.uuid), "price": "9.5", "compare_at_price": "12"}]}, format="json")
    assert r.status_code == 201 and r.data["prices"][0]["compare_at_price"] == "12.0000"
    assert len(admin.get(base).data) == 2
    vu = r.data["uuid"]
    r = admin.patch(f"{B}variants/{vu}/", {"name": "Renamed", "reorder_level": "3"}, format="json")
    assert r.status_code == 200 and r.data["name"] == "Renamed" and r.data["reorder_level"] == "3.000"
    assert admin.patch(f"{B}variants/{vu}/", {"sku": p.test_variants[0].sku}, format="json").data["code"] == "sku_taken"
    assert admin.delete(f"{B}variants/{vu}/").status_code == 204
    assert len(admin.get(base).data) == 1
    # sku never reusable even after soft delete
    assert admin.post(base, {"sku": "NEW-1"}, format="json").data["code"] == "sku_taken"
    # cannot delete the last active variant of an active product
    last = p.test_variants[0]
    r = admin.delete(f"{B}variants/{last.uuid}/")
    assert r.status_code == 400 and r.data["code"] == "last_variant"


def test_sku_locked_and_variant_undeletable_after_stock(admin):
    p = product(variants=2)
    v = p.test_variants[0]
    stock.move_stock(variant=v, warehouse=warehouse(), quantity=3, type=MovementType.PURCHASE_IN)
    assert admin.patch(f"{B}variants/{v.uuid}/", {"sku": "CHANGED"}, format="json").data["code"] == "sku_locked"
    assert admin.patch(f"{B}variants/{v.uuid}/", {"name": "ok"}, format="json").status_code == 200
    assert admin.delete(f"{B}variants/{v.uuid}/").data["code"] == "variant_in_use"


def test_set_prices_replaces_and_validates(admin):
    ils, usd = price_list("ILS"), price_list("USD")
    v = product().test_variants[0]
    url = f"{B}variants/{v.uuid}/prices/"
    r = admin.put(url, {"prices": [{"price_list": str(ils.uuid), "price": "10"},
                                   {"price_list": str(usd.uuid), "price": "3", "compare_at_price": "4"}]}, format="json")
    assert r.status_code == 200 and {p["currency"] for p in r.data["prices"]} == {"ILS", "USD"}
    r = admin.put(url, {"prices": [{"price_list": str(usd.uuid), "price": "3.5"}]}, format="json")
    assert [p["currency"] for p in r.data["prices"]] == ["USD"]  # ILS price removed (replace semantics)
    dup = {"prices": [{"price_list": str(ils.uuid), "price": "1"}, {"price_list": str(ils.uuid), "price": "2"}]}
    assert admin.put(url, dup, format="json").data["code"] == "duplicate_price_list"
    bad = {"prices": [{"price_list": str(ils.uuid), "price": "10", "compare_at_price": "5"}]}
    assert admin.put(url, bad, format="json").data["code"] == "invalid_compare_price"
    PriceList.objects.filter(pk=ils.pk).update(is_active=False)
    assert admin.put(url, {"prices": [{"price_list": str(ils.uuid), "price": "1"}]}, format="json").data["code"] == "price_list_inactive"
    seller = client_for(make_staff(["products.update"]))
    assert seller.put(url, {"prices": []}, format="json").status_code == 403


def test_price_lists_one_default_per_currency(admin):
    a = admin.post(B + "price-lists/", {"name": "Retail ILS", "currency": "ILS", "is_default": True}, format="json")
    b = admin.post(B + "price-lists/", {"name": "Wholesale ILS", "currency": "ILS", "is_default": True}, format="json")
    assert a.status_code == b.status_code == 201
    lists = {pl["name"]: pl["is_default"] for pl in admin.get(B + "price-lists/").data}
    assert lists == {"Retail ILS": False, "Wholesale ILS": True}
    assert admin.post(B + "price-lists/", {"name": "X", "currency": "XXX"}, format="json").status_code == 400
    assert client_for(make_staff(["products.view"])).post(
        B + "price-lists/", {"name": "Y", "currency": "USD"}, format="json").status_code == 403


def test_price_list_currency_locked_once_priced(admin):
    ils = price_list("ILS")
    v = product().test_variants[0]
    admin.put(f"{B}variants/{v.uuid}/prices/", {"prices": [{"price_list": str(ils.uuid), "price": "1"}]}, format="json")
    r = admin.patch(f"{B}price-lists/{ils.uuid}/", {"currency": "USD"}, format="json")
    assert r.status_code == 400 and r.data["code"] == "currency_locked"


# ------------------------------------------------------------ listing & filters


def test_list_filters_and_search(admin):
    a = product("Alpha Shirt", sku="AL-1", status="active", visible=True)
    b = product("Beta Pants", sku="BE-1", status="draft", visible=False)
    cat = Category.objects.create(name="Clothes", slug="clothes")
    a.category = cat
    a.save()
    url = B + "products/"
    assert admin.get(url).data["count"] == 2
    assert [x["name"] for x in admin.get(url + "?status=draft").data["results"]] == ["Beta Pants"]
    assert [x["name"] for x in admin.get(url + "?visible=true").data["results"]] == ["Alpha Shirt"]
    assert [x["name"] for x in admin.get(url + f"?category={cat.uuid}").data["results"]] == ["Alpha Shirt"]
    assert [x["name"] for x in admin.get(url + "?search=be-1").data["results"]] == ["Beta Pants"]
    assert [x["name"] for x in admin.get(url + "?search=alpha").data["results"]] == ["Alpha Shirt"]
    item = admin.get(url + "?search=alpha").data["results"][0]
    assert item["category"]["name"] == "Clothes" and item["variants_count"] == 1 and item["available"] == "0.000"
    assert b.uuid


def test_low_stock_filter_and_available(admin):
    p = product("Low", variants=1)
    v = p.test_variants[0]
    ProductVariant.objects.filter(pk=v.pk).update(reorder_level=5)
    other = product("Fine")
    wh = warehouse()
    stock.move_stock(variant=v, warehouse=wh, quantity=4, type=MovementType.PURCHASE_IN)
    stock.move_stock(variant=other.test_variants[0], warehouse=wh, quantity=100, type=MovementType.PURCHASE_IN)
    r = admin.get(B + "products/?low_stock=true")
    assert [x["name"] for x in r.data["results"]] == ["Low"] and r.data["results"][0]["available"] == "4.000"
    stock.move_stock(variant=v, warehouse=wh, quantity=10, type=MovementType.PURCHASE_IN)
    assert admin.get(B + "products/?low_stock=true").data["count"] == 0


# ------------------------------------------------------------ categories


def test_categories(admin):
    root = admin.post(B + "categories/", {"name": "Men"}, format="json")
    assert root.status_code == 201 and root.data["slug"] == "men"
    child = admin.post(B + "categories/", {"name": "Shirts", "parent": root.data["uuid"]}, format="json")
    assert str(child.data["parent"]) == str(root.data["uuid"])
    # cycle: root under its own child
    r = admin.patch(f"{B}categories/{root.data['uuid']}/", {"parent": child.data["uuid"]}, format="json")
    assert r.status_code == 400 and r.data["code"] == "category_cycle"
    # deleting a parent with children / products is blocked
    assert admin.delete(f"{B}categories/{root.data['uuid']}/").data["code"] == "category_in_use"
    assert admin.delete(f"{B}categories/{child.data['uuid']}/").status_code == 204
    assert admin.delete(f"{B}categories/{root.data['uuid']}/").status_code == 204
    assert client_for(make_staff(["categories.view"])).post(B + "categories/", {"name": "x"}, format="json").status_code == 403


# ------------------------------------------------------------ images


def test_image_upload_primary_and_delete(admin):
    p = product()
    url = f"{B}products/{p.uuid}/images/"
    r1 = admin.post(url, {"image": png(), "alt": "front"}, format="multipart")
    assert r1.status_code == 201 and r1.data["is_primary"] is True and r1.data["url"].startswith("http")
    assert r1.data["url"].endswith(".webp")  # a PNG goes in, a WebP is stored
    r2 = admin.post(url, {"image": png(name="b.png"), "is_primary": "true"}, format="multipart")
    assert r2.data["is_primary"] is True
    detail = admin.get(f"{B}products/{p.uuid}/").data
    assert [i["is_primary"] for i in detail["images"]] == [False, True]
    assert detail["images"][0]["variant"] is None
    assert admin.delete(f"{B}images/{r2.data['id']}/").status_code == 204
    assert admin.get(f"{B}products/{p.uuid}/").data["images"][0]["is_primary"] is True  # promoted


def test_image_validation(admin, settings):
    p = product()
    url = f"{B}products/{p.uuid}/images/"
    fake = SimpleUploadedFile("evil.png", b"<script>alert(1)</script>", content_type="image/png")
    assert admin.post(url, {"image": fake}, format="multipart").status_code == 400
    gif = SimpleUploadedFile("a.gif", _gif_bytes(), content_type="image/gif")
    r = admin.post(url, {"image": gif}, format="multipart")
    assert r.status_code == 400 and r.data["code"] == "invalid_image"
    settings.MAX_UPLOAD_IMAGE_BYTES = 10
    r = admin.post(url, {"image": png()}, format="multipart")
    assert r.status_code == 400 and r.data["code"] == "image_too_large"
    settings.MAX_UPLOAD_IMAGE_BYTES = 5 * 1024 * 1024
    other = product()
    r = admin.post(url, {"image": png(), "variant": str(other.test_variants[0].uuid)}, format="multipart")
    assert r.data["code"] == "variant_mismatch"
    assert client_for(make_staff(["products.view"])).post(url, {"image": png()}, format="multipart").status_code == 403


def _gif_bytes():
    buf = io.BytesIO()
    Image.new("P", (2, 2)).save(buf, "GIF")
    return buf.getvalue()


# ------------------------------------------------------------ brands


def test_brand_crud_case_insensitive_unique_and_delete_rules(admin):
    r = admin.post(B + "brands/", {"name": "  Acme  "}, format="json")
    assert r.status_code == 201 and r.json()["name"] == "Acme" and r.json()["products_count"] == 0
    dup = admin.post(B + "brands/", {"name": "ACME"}, format="json")
    assert dup.status_code == 400 and dup.json()["code"] == "brand_taken"
    uuid = r.json()["uuid"]
    assert admin.patch(f"{B}brands/{uuid}/", {"name": "Acme Co"}, format="json").json()["name"] == "Acme Co"
    other = admin.post(B + "brands/", {"name": "Zed"}, format="json").json()
    assert admin.patch(f"{B}brands/{other['uuid']}/", {"name": "acme co"}, format="json").json()["code"] == "brand_taken"
    # a brand keeps its own name when saved again (not "taken" by itself)
    assert admin.patch(f"{B}brands/{uuid}/", {"name": "Acme Co", "is_active": False}, format="json").status_code == 200

    p = product("Soap")
    assert admin.patch(f"{B}products/{p.uuid}/", {"brand": uuid}, format="json").json()["brand"] == {"uuid": uuid, "name": "Acme Co"}
    assert admin.get(B + "brands/").json()[0]["products_count"] == 1
    blocked = admin.delete(f"{B}brands/{uuid}/")
    assert blocked.status_code == 400 and blocked.json()["code"] == "brand_in_use"
    assert admin.patch(f"{B}products/{p.uuid}/", {"brand": None}, format="json").json()["brand"] is None
    assert admin.delete(f"{B}brands/{uuid}/").status_code == 204
    assert Brand.objects.filter(name="Acme Co").count() == 0


def test_brand_list_search_filter_and_product_search_by_brand(admin):
    acme = Brand.objects.create(name="Acme")
    Brand.objects.create(name="Beta", is_active=False)
    product("Anvil", brand=acme)
    product("Plain")
    assert [b["name"] for b in admin.get(B + "brands/").json()] == ["Acme", "Beta"]
    assert [b["name"] for b in admin.get(B + "brands/?search=bet").json()] == ["Beta"]
    assert [b["name"] for b in admin.get(B + "brands/?active=true").json()] == ["Acme"]
    found = admin.get(B + "products/?search=acme").json()["results"]
    assert [p["name"] for p in found] == ["Anvil"] and found[0]["brand"]["name"] == "Acme"


def test_brand_permissions(admin):
    assert client_for(make_customer()).get(B + "brands/").status_code == 403
    assert client_for(make_staff()).get(B + "brands/").status_code == 403
    viewer = client_for(make_staff(["brands.view"]))
    assert viewer.get(B + "brands/").status_code == 200
    assert viewer.post(B + "brands/", {"name": "X"}, format="json").status_code == 403
    assert client_for(make_staff(["brands.manage"])).post(B + "brands/", {"name": "X"}, format="json").status_code == 201
    assert client_for(make_staff(roles=["store_manager"])).post(B + "brands/", {"name": "Y"}, format="json").status_code == 201


# ------------------------------------------------------------ manual cost entry


def test_cost_can_be_entered_with_manage_cost_and_is_audited(admin, pl):
    body = {"name": "Soap", "variants": [{"sku": "SO-1", "cost_price": "4.5",
                                          "prices": [{"price_list": str(pl.uuid), "price": "9"}]}]}
    r = admin.post(B + "products/", body, format="json")
    assert r.status_code == 201, r.json()
    v = r.json()["variants"][0]
    assert v["cost_price"] == "4.5000"                      # admin may also see it
    assert AuditLog.objects.filter(action="variant.cost_changed").count() == 1

    r = admin.patch(f"{B}variants/{v['uuid']}/", {"cost_price": "5.25"}, format="json")
    assert r.status_code == 200 and r.json()["cost_price"] == "5.2500"
    log = AuditLog.objects.filter(action="variant.cost_changed").order_by("-id").first()
    assert log.data["old"] == "4.5000" and log.data["new"] == "5.2500"
    same = admin.patch(f"{B}variants/{v['uuid']}/", {"cost_price": "5.25"}, format="json")
    assert same.status_code == 200 and AuditLog.objects.filter(action="variant.cost_changed").count() == 2  # unchanged: no new row

    added = admin.post(f"{B}products/{r.json() and Product.objects.get(name='Soap').uuid}/variants/",
                       {"sku": "SO-2", "cost_price": "6"}, format="json")
    assert added.status_code == 201 and added.json()["cost_price"] == "6.0000"
    assert admin.patch(f"{B}variants/{v['uuid']}/", {"cost_price": "-1"}, format="json").status_code == 400


def test_cost_needs_its_own_permission(admin, pl):
    p = product("Cost test", price=10)
    var = p.test_variants[0]
    editor = client_for(make_staff(["products.update"]))
    denied = editor.patch(f"{B}variants/{var.uuid}/", {"cost_price": "3"}, format="json")
    assert denied.status_code == 403 and denied.json()["code"] == "forbidden"
    assert editor.patch(f"{B}variants/{var.uuid}/", {"name": "ok"}, format="json").status_code == 200  # other fields fine
    creator = client_for(make_staff(["products.create"]))
    r = creator.post(B + "products/", {"name": "X", "variants": [{"sku": "X-1", "cost_price": "2"}]}, format="json")
    assert r.status_code == 403
    assert creator.post(B + "products/", {"name": "X", "variants": [{"sku": "X-1"}]}, format="json").status_code == 201
    assert client_for(make_staff(["products.update", "products.manage_cost"])).patch(
        f"{B}variants/{var.uuid}/", {"cost_price": "3"}, format="json").status_code == 200
    # starter roles never include it
    assert client_for(make_staff(roles=["store_manager"])).patch(
        f"{B}variants/{var.uuid}/", {"cost_price": "3"}, format="json").status_code == 403


# ------------------------------------------------------------ brand logo
def test_brand_logo_upload_replace_remove_and_storefront(admin, settings):
    from rest_framework.test import APIClient
    import os

    from apps.catalog.tests.helpers import product as make_product

    brand = Brand.objects.create(name="Acme")
    make_product("Soap", price=5, brand=brand, stock=3)
    url = f"{B}brands/{brand.uuid}/logo/"
    assert admin.get(f"{B}brands/{brand.uuid}/").data["logo"] is None
    store = APIClient()
    assert store.get("/api/v1/store/brands/").data[0]["logo"] is None

    r = admin.post(url, {"image": png(name="x.png")}, format="multipart")
    assert r.status_code == 200 and r.data["logo"].startswith("http") and "/brands/acme-" in r.data["logo"]
    assert r.data["logo"].endswith(".webp")
    first = Brand.objects.get(pk=brand.pk).logo.path
    assert os.path.exists(first)
    assert store.get("/api/v1/store/brands/").data[0]["logo"] == r.data["logo"]  # customers see it

    r2 = admin.post(url, {"image": png(name="y.png")}, format="multipart")  # replace: the old file is gone
    assert r2.status_code == 200 and not os.path.exists(first)

    assert admin.delete(url).status_code == 204
    assert Brand.objects.get(pk=brand.pk).logo.name in ("", None)
    assert store.get("/api/v1/store/brands/").data[0]["logo"] is None
    assert admin.delete(url).status_code == 204  # idempotent


def test_brand_logo_rejects_bad_files_and_needs_permission(admin):
    from apps.core.testing import make_staff

    brand = Brand.objects.create(name="Acme")
    url = f"{B}brands/{brand.uuid}/logo/"
    fake = SimpleUploadedFile("evil.png", b"<script>alert(1)</script>", content_type="image/png")
    assert admin.post(url, {"image": fake}, format="multipart").status_code == 400
    gif = SimpleUploadedFile("a.gif", _gif_bytes(), content_type="image/gif")
    assert admin.post(url, {"image": gif}, format="multipart").data["code"] == "invalid_image"
    assert admin.post(url, {}, format="multipart").status_code == 400
    viewer = client_for(make_staff(perms=["brands.view"]))
    assert viewer.post(url, {"image": png()}, format="multipart").status_code == 403
    assert viewer.delete(url).status_code == 403
    assert client_for(make_staff(perms=["brands.manage"])).post(url, {"image": png()}, format="multipart").status_code == 200


def test_brand_logos_are_stored_on_one_3_2_canvas_without_cropping_or_stretching(admin):
    from PIL import Image as PILImage

    brand = Brand.objects.create(name="Acme")
    url = f"{B}brands/{brand.uuid}/logo/"
    for size, expected in (((400, 100), (400, 267)), ((100, 400), (600, 400)), ((300, 200), (300, 200)), ((2000, 2000), (960, 640))):
        r = admin.post(url, {"image": png(size=size, name="l.png")}, format="multipart")
        assert r.status_code == 200
        with PILImage.open(Brand.objects.get(pk=brand.pk).logo.path) as stored:
            assert stored.size == expected, (size, stored.size)
            assert stored.format == "WEBP"
            assert stored.mode == "RGBA" or size == (300, 200)  # padded canvases keep their transparency
    # the original picture sits in the middle, the rest of the canvas is transparent
    r = admin.post(url, {"image": png(size=(300, 100), name="wide.png")}, format="multipart")
    with PILImage.open(Brand.objects.get(pk=brand.pk).logo.path) as stored:
        assert stored.getpixel((0, 0))[3] == 0 and stored.getpixel((150, stored.height // 2))[3] == 255


# ------------------------------------------------------------ category image
def test_category_image_upload_square_webp_replace_remove_and_storefront(admin):
    import os

    from PIL import Image as PILImage
    from rest_framework.test import APIClient

    cat = Category.objects.create(name="Cleaning", slug="cleaning")
    url = f"{B}categories/{cat.uuid}/image/"
    store = APIClient()
    assert admin.get(f"{B}categories/{cat.uuid}/").data["image"] is None
    assert store.get("/api/v1/store/categories/").data[0]["image"] is None

    r = admin.post(url, {"image": png(size=(600, 300), name="wide.png")}, format="multipart")
    assert r.status_code == 200 and r.data["image"].startswith("http") and r.data["image"].endswith(".webp")
    first = Category.objects.get(pk=cat.pk).image.path
    with PILImage.open(first) as stored:
        assert stored.size == (300, 300)  # centre-cropped square, not enlarged
    assert store.get("/api/v1/store/categories/").data[0]["image"] == r.data["image"]

    big = admin.post(url, {"image": png(size=(2000, 1000), name="big.png")}, format="multipart")
    assert big.status_code == 200 and not os.path.exists(first)  # replaced: the old file is gone
    with PILImage.open(Category.objects.get(pk=cat.pk).image.path) as stored:
        assert stored.size == (800, 800)

    assert admin.delete(url).status_code == 204
    assert not Category.objects.get(pk=cat.pk).image
    assert store.get("/api/v1/store/categories/").data[0]["image"] is None
    assert admin.delete(url).status_code == 204


def test_category_image_rejects_bad_files_and_needs_permission(admin):
    from apps.core.testing import make_staff

    cat = Category.objects.create(name="Cleaning", slug="cleaning")
    url = f"{B}categories/{cat.uuid}/image/"
    fake = SimpleUploadedFile("evil.png", b"<script>alert(1)</script>", content_type="image/png")
    assert admin.post(url, {"image": fake}, format="multipart").status_code == 400
    assert admin.post(url, {}, format="multipart").status_code == 400
    viewer = client_for(make_staff(perms=["categories.view"]))
    assert viewer.post(url, {"image": png()}, format="multipart").status_code == 403
    assert viewer.delete(url).status_code == 403
    assert client_for(make_staff(perms=["categories.manage"])).post(url, {"image": png()}, format="multipart").status_code == 200


def test_every_uploaded_picture_is_stored_as_webp(admin):
    from PIL import Image as PILImage

    cat = Category.objects.create(name="Cleaning", slug="cleaning")
    brand = Brand.objects.create(name="Acme")
    # a transparent PNG and a JPEG go in, WebP comes out (transparency kept)
    buf = io.BytesIO()
    PILImage.new("RGBA", (40, 20), (255, 0, 0, 0)).save(buf, "PNG")
    transparent = SimpleUploadedFile("logo.png", buf.getvalue(), content_type="image/png")
    r = admin.post(f"{B}brands/{brand.uuid}/logo/", {"image": transparent}, format="multipart")
    assert r.status_code == 200 and r.data["logo"].endswith(".webp")
    with PILImage.open(Brand.objects.get(pk=brand.pk).logo.path) as stored:
        assert stored.format == "WEBP" and stored.mode == "RGBA" and stored.getpixel((0, 0))[3] == 0
    r = admin.post(f"{B}categories/{cat.uuid}/image/", {"image": png(fmt="JPEG", name="c.jpg")}, format="multipart")
    assert r.data["image"].endswith(".webp")
    p = product()
    r = admin.post(f"{B}products/{p.uuid}/images/", {"image": png(fmt="JPEG", name="p.jpg")}, format="multipart")
    assert r.data["url"].endswith(".webp")
    with PILImage.open(ProductImage.objects.get(pk=r.data["id"]).image.path) as stored:
        assert stored.format == "WEBP"
