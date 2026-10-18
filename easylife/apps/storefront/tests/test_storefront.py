import pytest
from rest_framework.test import APIClient

from apps.catalog.models import Brand, Category, PriceList, ProductVariant
from apps.catalog.tests.helpers import _stock_warehouse as _stock_wh, price_list, product as _product, warehouse
from apps.core.testing import bootstrap, client_for, make_admin
from apps.currencies.models import Currency
from apps.inventory import services
from apps.inventory.models import MovementType

S = "/api/v1/store/"
A = "/api/v1/admin/"


def product(*args, stock=5, **kwargs):
    """Customers only see products with stock, so these tests stock theirs unless they say `stock=0`."""
    return _product(*args, stock=stock, **kwargs)


@pytest.fixture(autouse=True)
def _setup(db):
    bootstrap()


@pytest.fixture
def api():
    return APIClient()


def names(resp):
    return [p["name"] for p in resp.data["results"]]


def test_only_active_visible_priced_products_are_listed(api):
    product("Shown", price=10)
    product("Hidden flag", price=10, visible=False)
    product("Draft", price=10, status="draft")
    product("Archived", price=10, status="archived")
    product("No price")
    deleted = product("Deleted", price=10)
    deleted.deleted_at = deleted.created_at
    deleted.save()
    inactive_only = product("Inactive variant", price=10)
    ProductVariant.objects.filter(product=inactive_only).update(is_active=False)
    product("Out of stock", price=10, stock=0)
    r = api.get(S + "products/")
    assert r.status_code == 200 and names(r) == ["Shown"]
    assert r.data["count"] == 1


def test_hiding_from_admin_removes_it_immediately_and_showing_restores_it(api):
    admin = client_for(make_admin())
    p = product("Toggle me", price=5)
    assert names(api.get(S + "products/")) == ["Toggle me"]
    admin.post(f"{A}products/{p.uuid}/hide/")
    assert names(api.get(S + "products/")) == []
    assert api.get(f"{S}products/{p.slug}/").status_code == 404
    admin.post(f"{A}products/{p.uuid}/show/")
    assert names(api.get(S + "products/")) == ["Toggle me"]
    admin.post(f"{A}products/{p.uuid}/archive/")
    assert names(api.get(S + "products/")) == []


def test_detail_response_has_no_private_data(api):
    p = product("Secret item", price=20, sku="SC-1", stock=0)
    v = p.test_variants[0]
    services.move_stock(variant=v, warehouse=warehouse(), quantity=7, type=MovementType.PURCHASE_IN,
                        unit_cost=11, update_cost=True)
    d = api.get(f"{S}products/{p.slug}/").data
    assert set(d) == {"uuid", "name", "slug", "brand", "category", "currency", "price_from", "in_stock",
                      "image", "description", "variants", "images"}
    assert set(d["variants"][0]) == {"uuid", "sku", "name", "attributes", "price", "compare_at_price", "in_stock"}
    assert d["variants"][0]["price"] == "20.0000" and d["currency"] == "ILS" and d["in_stock"] is True
    flat = str(d)
    assert "cost" not in flat and "on_hand" not in flat and "reserved" not in flat and "7.000" not in flat


def test_products_without_sellable_stock_are_hidden_from_customers(api):
    empty = product("Empty", price=1, stock=0)
    full = product("Full", price=1)
    digital = product("Digital", price=1, track=False, stock=0)
    reserved_all = product("Reserved", price=1, stock=0)
    multi = product("Multi", price=1, variants=2, stock=0)
    wh = warehouse()
    services.move_stock(variant=reserved_all.test_variants[0], warehouse=wh, quantity=2, type=MovementType.PURCHASE_IN)
    services.reserve(variant=reserved_all.test_variants[0], warehouse=wh, quantity=2)
    services.move_stock(variant=multi.test_variants[1], warehouse=wh, quantity=1, type=MovementType.PURCHASE_IN)
    got = {p["name"]: p["in_stock"] for p in api.get(S + "products/").data["results"]}
    assert got == {"Full": True, "Multi": True}  # one stocked variant is enough; untracked ones hold no stock, so are hidden
    assert api.get(f"{S}products/{empty.slug}/").status_code == 404
    assert api.get(f"{S}products/{reserved_all.slug}/").status_code == 404
    assert api.get(f"{S}products/{digital.slug}/").status_code == 404
    assert api.get(f"{S}products/{full.slug}/").status_code == 200


def test_selling_the_last_unit_hides_the_product_and_restocking_shows_it_again(api):
    p = product("Last one", price=3, stock=1)
    v, wh = p.test_variants[0], warehouse()
    assert names(api.get(S + "products/")) == ["Last one"]
    services.move_stock(variant=v, warehouse=_stock_wh(), quantity=-1, type=MovementType.SALE_OUT)
    assert names(api.get(S + "products/")) == []
    services.move_stock(variant=v, warehouse=wh, quantity=2, type=MovementType.PURCHASE_IN)
    assert names(api.get(S + "products/")) == ["Last one"]


def test_stock_in_inactive_warehouse_does_not_count(api):
    p = product("Warehoused", price=1, stock=0)
    wh = warehouse()
    services.move_stock(variant=p.test_variants[0], warehouse=wh, quantity=5, type=MovementType.PURCHASE_IN)
    assert api.get(S + "products/").data["results"][0]["in_stock"] is True
    wh.is_active = False
    wh.save()
    assert api.get(S + "products/").data["count"] == 0


def test_currency_selection(api):
    p = product("Dual", price=100, currency="ILS")
    v = p.test_variants[0]
    usd = price_list("USD")
    from apps.catalog.models import ProductPrice

    ProductPrice.objects.create(variant=v, price_list=usd, price=27, compare_at_price=30)
    ils = api.get(S + "products/").data["results"][0]
    assert (ils["currency"], ils["price_from"]) == ("ILS", "100.0000")
    r = api.get(S + "products/?currency=usd")
    assert (r.data["results"][0]["currency"], r.data["results"][0]["price_from"]) == ("USD", "27.0000")
    d = api.get(f"{S}products/{p.slug}/?currency=USD").data
    assert d["variants"][0]["price"] == "27.0000" and d["variants"][0]["compare_at_price"] == "30.0000"
    # a currency with no default price list shows nothing; unknown currency is an error
    assert api.get(S + "products/?currency=JOD").data["count"] == 0
    r = api.get(S + "products/?currency=XXX")
    assert r.status_code == 400 and r.data["code"] == "invalid_currency"
    Currency.objects.filter(code="USD").update(is_active=False)
    assert api.get(S + "products/?currency=USD").status_code == 400


def test_inactive_or_non_default_price_lists_are_ignored(api):
    p = product("Lists", price=10)
    PriceList.objects.filter(currency__code="ILS").update(is_active=False)
    assert api.get(S + "products/").data["count"] == 0
    PriceList.objects.filter(currency__code="ILS").update(is_active=True, is_default=False)
    assert api.get(S + "products/").data["count"] == 0
    assert p


def test_search_ordering_and_category_filter(api):
    root = Category.objects.create(name="Clothes", slug="clothes")
    child = Category.objects.create(name="Shirts", slug="shirts", parent=root)
    other = Category.objects.create(name="Toys", slug="toys")
    product("Cheap tee", price=5, category=child, brand=Brand.objects.create(name="Acme"))
    product("Pricey coat", price=90, category=root)
    product("Robot", price=40, category=other)
    assert names(api.get(S + "products/?ordering=price")) == ["Cheap tee", "Robot", "Pricey coat"]
    assert names(api.get(S + "products/?ordering=-price")) == ["Pricey coat", "Robot", "Cheap tee"]
    assert names(api.get(S + "products/?ordering=name")) == ["Cheap tee", "Pricey coat", "Robot"]
    assert sorted(names(api.get(S + "products/?category=clothes"))) == ["Cheap tee", "Pricey coat"]  # includes sub-categories
    assert names(api.get(S + "products/?category=shirts")) == ["Cheap tee"]
    assert names(api.get(S + "products/?category=nope")) == []
    assert names(api.get(S + "products/?search=acme")) == ["Cheap tee"]
    assert names(api.get(S + "products/?search=ROB")) == ["Robot"]


def test_price_filter_keeps_products_whose_from_price_is_inside_the_range(api):
    product("Five", price=5)
    product("Twenty", price=20)
    product("Ninety", price=90)
    assert sorted(names(api.get(S + "products/?min_price=10"))) == ["Ninety", "Twenty"]
    assert sorted(names(api.get(S + "products/?max_price=20"))) == ["Five", "Twenty"]  # the bounds are inclusive
    assert names(api.get(S + "products/?min_price=6&max_price=89.99")) == ["Twenty"]
    assert names(api.get(S + "products/?min_price=100")) == []
    assert names(api.get(S + "products/?min_price=50&max_price=10")) == []  # an empty range is just empty
    assert len(names(api.get(S + "products/?min_price=&max_price="))) == 3  # blank means "no limit"
    # it combines with ordering and the other filters
    assert names(api.get(S + "products/?min_price=10&ordering=-price")) == ["Ninety", "Twenty"]
    assert names(api.get(S + "products/?min_price=10&search=ninety")) == ["Ninety"]


def test_price_filter_uses_the_cheapest_variant(api):
    from apps.catalog.models import ProductPrice

    p = product("Multi", price=50, variants=2)
    ProductPrice.objects.filter(variant=p.test_variants[1]).update(price=8)  # from-price becomes 8
    assert names(api.get(S + "products/?max_price=10")) == ["Multi"]
    assert names(api.get(S + "products/?min_price=20")) == []


def test_price_filter_rejects_bad_numbers(api):
    product("Any", price=5)
    for q in ("min_price=abc", "max_price=-1", "min_price=NaN", "max_price=Infinity"):
        r = api.get(S + "products/?" + q)
        assert r.status_code == 400 and r.data["code"] == "invalid_price", q
    assert api.get(S + "products/price-range/?min_price=abc").status_code == 200  # the range ignores price filters


def test_price_range_gives_the_slider_its_ends_and_follows_the_other_filters(api):
    root = Category.objects.create(name="Clothes", slug="clothes")
    other = Category.objects.create(name="Toys", slug="toys")
    product("Tee", price=5, category=root)
    product("Coat", price=90.5, category=root)
    product("Robot", price=40, category=other)
    r = api.get(S + "products/price-range/")
    assert r.status_code == 200 and r.data == {"currency": "ILS", "min": "5.0000", "max": "90.5000"}
    assert api.get(S + "products/price-range/?category=toys").data["max"] == "40.0000"
    assert api.get(S + "products/price-range/?category=clothes").data["min"] == "5.0000"
    assert api.get(S + "products/price-range/?search=robot").data == {"currency": "ILS", "min": "40.0000", "max": "40.0000"}
    # nothing to show: both ends are null, never an error
    assert api.get(S + "products/price-range/?category=nope").data == {"currency": "ILS", "min": None, "max": None}


def test_price_range_follows_the_currency_and_is_public_read_only(api):
    product("Tee", price=5)
    assert api.get(S + "products/price-range/?currency=XXX").status_code == 400
    assert api.get(S + "products/price-range/?currency=XXX").data["code"] == "invalid_currency"
    # a stray token must not break browsing, and the endpoint is read-only
    api.credentials(HTTP_AUTHORIZATION="Bearer junk")
    assert api.get(S + "products/price-range/").status_code == 200
    assert api.post(S + "products/price-range/").status_code == 405


def test_price_from_is_the_cheapest_variant_and_pagination(api):
    p = product("Multi", price=50, variants=3)
    from apps.catalog.models import ProductPrice

    ProductPrice.objects.filter(variant=p.test_variants[1]).update(price=30)
    assert api.get(S + "products/").data["results"][0]["price_from"] == "30.0000"
    for i in range(30):
        product(f"P{i}", price=1)
    page1 = api.get(S + "products/").data
    assert page1["count"] == 31 and len(page1["results"]) == 25 and page1["next"]
    assert len(api.get(S + "products/?page=2").data["results"]) == 6


def test_categories_endpoint_lists_active_only(api):
    root = Category.objects.create(name="A", slug="a")
    Category.objects.create(name="B", slug="b", parent=root)
    Category.objects.create(name="Off", slug="off", is_active=False)
    r = api.get(S + "categories/")
    assert r.status_code == 200
    assert r.data == [{"name": "A", "slug": "a", "image": None, "parent": None}, {"name": "B", "slug": "b", "image": None, "parent": "a"}]


def test_public_endpoints_ignore_bad_tokens_and_are_read_only(api):
    product("Open", price=1)
    api.credentials(HTTP_AUTHORIZATION="Bearer total-garbage")
    assert api.get(S + "products/").status_code == 200
    assert api.post(S + "products/", {}, format="json").status_code == 405
    assert api.delete(S + "categories/").status_code == 405


def test_brand_filter_is_by_slug_and_skips_inactive_brands(api):
    acme = Brand.objects.create(name="Acme")
    off = Brand.objects.create(name="Dormant", is_active=False)
    product("Acme soap", price=5, brand=acme)
    product("Acme mop", price=7, brand=acme)
    product("Other soap", price=5)
    product("Dormant soap", price=5, brand=off)
    assert acme.slug == "acme"
    assert sorted(names(api.get(S + "products/?brand=acme"))) == ["Acme mop", "Acme soap"]
    assert names(api.get(S + "products/?brand=Acme")) == []  # the slug, not the display name
    assert names(api.get(S + "products/?brand=acm")) == []
    assert names(api.get(S + "products/?brand=dormant")) == []
    assert names(api.get(S + "products/?brand=nope")) == []


def test_brands_endpoint_lists_only_brands_with_visible_products(api):
    acme = Brand.objects.create(name="Acme")
    zeta = Brand.objects.create(name="Zeta")
    Brand.objects.create(name="Empty")  # no products
    hidden_only = Brand.objects.create(name="HiddenOnly")
    off = Brand.objects.create(name="Dormant", is_active=False)
    unpriced = Brand.objects.create(name="Unpriced")
    product("A1", price=1, brand=acme)
    product("A2", price=1, brand=acme)
    product("A draft", price=1, brand=acme, status="draft")  # not counted
    product("Z1", price=1, brand=zeta)
    product("H1", price=1, brand=hidden_only, visible=False)
    product("D1", price=1, brand=off)
    product("U1", brand=unpriced)  # no price in the default list
    r = api.get(S + "brands/")
    assert r.status_code == 200
    assert r.data == [{"name": "Acme", "slug": "acme", "logo": None, "products_count": 2},
                      {"name": "Zeta", "slug": "zeta", "logo": None, "products_count": 1}]


def test_brands_endpoint_follows_currency_and_is_public_read_only(api):
    acme = Brand.objects.create(name="Acme")
    product("A1", price=1, brand=acme)
    assert api.get(S + "brands/?currency=USD").data == []  # no USD price list => nothing to show
    assert api.get(S + "brands/?currency=XXX").status_code == 400
    api.credentials(HTTP_AUTHORIZATION="Bearer total-garbage")
    assert api.get(S + "brands/").status_code == 200
    assert api.post(S + "brands/", {}, format="json").status_code == 405


# ------------------------------------------------------------ languages (ar / en / he)
def _translated_catalog():
    cat = Category.objects.create(name="Cleaning", name_ar="تنظيف", name_he="ניקיון", slug="cleaning")
    brand = Brand.objects.create(name="Sparkle", name_ar="سباركل", name_he="ספרקל")
    p = product("Bleach", price=9, category=cat, brand=brand)
    p.name_ar, p.name_he = "مبيض", "אקונומיקה"
    p.description, p.description_ar = "Strong bleach", "مبيض قوي"
    p.save()
    return cat, brand, p


def test_store_text_follows_the_requested_language_with_default_fallback(api):
    cat, brand, p = _translated_catalog()
    ar = api.get(S + "products/?lang=ar").data["results"][0]
    assert ar["name"] == "مبيض" and ar["category"]["name"] == "تنظيف"
    assert ar["brand"] == {"name": "سباركل", "slug": "sparkle"}
    he = api.get(S + "products/?lang=he").data["results"][0]
    assert he["name"] == "אקונומיקה" and he["brand"]["name"] == "ספרקל"
    en = api.get(S + "products/?lang=en").data["results"][0]  # no English text entered: the default shows
    assert en["name"] == "Bleach" and en["category"]["name"] == "Cleaning" and en["brand"]["name"] == "Sparkle"
    assert api.get(S + "products/?lang=xx").data["results"][0]["name"] == "Bleach"  # unknown language = default, no error
    assert api.get(S + "products/").data["results"][0]["name"] == "Bleach"
    detail = api.get(f"{S}products/{p.slug}/?lang=ar").data
    assert detail["name"] == "مبيض" and detail["description"] == "مبيض قوي"
    assert api.get(f"{S}products/{p.slug}/?lang=he").data["description"] == "Strong bleach"  # fallback


def test_store_categories_and_brands_are_translated_but_keep_their_slugs(api):
    _translated_catalog()
    assert api.get(S + "categories/?lang=ar").data == [{"name": "تنظيف", "slug": "cleaning", "image": None, "parent": None}]
    assert api.get(S + "categories/").data == [{"name": "Cleaning", "slug": "cleaning", "image": None, "parent": None}]
    assert api.get(S + "brands/?lang=he").data == [{"name": "ספרקל", "slug": "sparkle", "logo": None, "products_count": 1}]
    assert api.get(S + "brands/?lang=en").data[0]["name"] == "Sparkle"


def test_search_finds_a_product_by_its_name_in_any_language(api):
    _translated_catalog()
    for term in ("bleach", "مبيض", "אקונומיקה", "سباركل", "sparkle"):
        assert names(api.get(S + "products/?search=" + term)) == ["Bleach"], term
    assert names(api.get(S + "products/?search=nothing")) == []


def test_brand_slug_is_stable_and_unique():
    a = Brand.objects.create(name="Acme")
    b = Brand.objects.create(name="ACME Plus")
    c = Brand.objects.create(name="علامة")
    assert (a.slug, b.slug) == ("acme", "acme-plus") and c.slug == "علامة"
    a.name = "Acme Renamed"
    a.save()
    assert Brand.objects.get(pk=a.pk).slug == "acme"  # links keep working after a rename


def test_admin_can_enter_and_read_the_three_languages():
    admin = client_for(make_admin())
    r = admin.post(A + "brands/", {"name": "Acme", "name_ar": "أكمي", "name_he": "אקמי"}, format="json")
    assert r.status_code == 201 and r.data["name_ar"] == "أكمي" and r.data["slug"] == "acme" and r.data["name_en"] == ""
    c = admin.post(A + "categories/", {"name": "Toys", "name_en": "Toys", "name_ar": "ألعاب"}, format="json")
    assert c.status_code == 201 and c.data["name_ar"] == "ألعاب"
    p = admin.post(A + "products/", {"name": "Robot", "name_ar": "روبوت", "description_he": "רובוט", "variants": [{"sku": "R-1"}]},
                   format="json")
    assert p.status_code == 201
    d = admin.get(f"{A}products/{p.data['uuid']}/").data
    assert d["name_ar"] == "روبوت" and d["description_he"] == "רובוט" and d["name_en"] == ""
    admin.patch(f"{A}products/{p.data['uuid']}/", {"name_en": "Robot EN"}, format="json")
    assert admin.get(f"{A}products/{p.data['uuid']}/").data["name_en"] == "Robot EN"


# ------------------------------------------------------------ About page (shop profile)
def test_about_is_public_translated_and_lists_only_active_social_links(api):
    admin = client_for(make_admin())
    r = admin.patch(A + "store-profile/", {
        "about": "We make home life easier.", "about_ar": "نسهّل حياتك", "address": "Main St 1",
        "address_he": "רחוב ראשי 1", "hours": "Sun-Thu 9-18", "phone": "0535746112", "whatsapp": "+972 53-574-6112",
        "email": "hi@easylife.test", "latitude": "31.903400", "longitude": "35.203400",
        "map_url": "https://maps.google.com/?q=31.9034,35.2034",
        "social_links": [
            {"platform": "instagram", "url": "https://instagram.com/easylife", "label": "@easylife"},
            {"platform": "facebook", "url": "https://facebook.com/easylife", "is_active": False},
            {"platform": "tiktok", "url": "https://tiktok.com/@easylife"},
        ]}, format="json")
    assert r.status_code == 200 and r.data["whatsapp"] == "972535746112" and len(r.data["social_links"]) == 3
    ar = api.get(S + "about/?lang=ar")
    assert ar.status_code == 200 and ar.data["about"] == "نسهّل حياتك" and ar.data["address"] == "Main St 1"  # fallback
    assert api.get(S + "about/?lang=he").data["address"] == "רחוב ראשי 1"
    assert api.get(S + "about/").data["about"] == "We make home life easier."
    d = ar.data
    assert d["latitude"] == "31.903400" and d["whatsapp"] == "972535746112" and d["email"] == "hi@easylife.test"
    assert [(x["platform"], x["label"]) for x in d["social_links"]] == [("instagram", "@easylife"), ("tiktok", "")]
    assert set(d["social_links"][0]) == {"platform", "url", "label"}  # nothing else leaks


def test_about_works_before_anything_is_entered(api):
    r = api.get(S + "about/")
    assert r.status_code == 200 and r.data["about"] == "" and r.data["social_links"] == [] and r.data["latitude"] is None
    assert api.post(S + "about/", {}, format="json").status_code == 405


def test_social_links_replace_the_whole_list_and_reject_unsafe_urls():
    admin = client_for(make_admin())
    admin.patch(A + "store-profile/", {"social_links": [{"platform": "x", "url": "https://x.com/a"}]}, format="json")
    r = admin.patch(A + "store-profile/", {"social_links": [{"platform": "youtube", "url": "https://youtube.com/c/a"}]}, format="json")
    assert [x["platform"] for x in r.data["social_links"]] == ["youtube"]
    r = admin.patch(A + "store-profile/", {"about": "kept"}, format="json")  # no social_links key = untouched
    assert r.data["about"] == "kept" and len(r.data["social_links"]) == 1
    for bad in ("javascript:alert(1)", "ftp://x.com/a", "data:text/html,hi", "not a url"):
        r = admin.patch(A + "store-profile/", {"social_links": [{"platform": "other", "url": bad}]}, format="json")
        assert r.status_code == 400, bad
    assert admin.patch(A + "store-profile/", {"map_url": "javascript:alert(1)"}, format="json").status_code == 400
    assert admin.patch(A + "store-profile/", {"social_links": [{"platform": "myspace", "url": "https://a.com"}]}, format="json").status_code == 400


def test_location_needs_both_coordinates_in_range():
    admin = client_for(make_admin())
    assert admin.patch(A + "store-profile/", {"latitude": "31.9"}, format="json").status_code == 400
    assert admin.patch(A + "store-profile/", {"latitude": "95", "longitude": "35"}, format="json").status_code == 400
    assert admin.patch(A + "store-profile/", {"latitude": "31.9", "longitude": "35.2"}, format="json").status_code == 200
    assert admin.patch(A + "store-profile/", {"latitude": None, "longitude": None}, format="json").status_code == 200


def test_store_profile_admin_permissions(api):
    from apps.core.testing import make_customer, make_staff

    url = A + "store-profile/"
    assert api.get(url).status_code == 401
    assert client_for(make_customer()).get(url).status_code == 403
    nobody = client_for(make_staff())
    assert nobody.get(url).status_code == 403 and nobody.patch(url, {"about": "x"}, format="json").status_code == 403
    viewer = client_for(make_staff(perms=["storefront.view"]))
    assert viewer.get(url).status_code == 200
    assert viewer.patch(url, {"about": "x"}, format="json").status_code == 403  # reading is not editing
    editor = client_for(make_staff(perms=["storefront.manage"]))
    assert editor.patch(url, {"about": "x"}, format="json").status_code == 200
    assert client_for(make_staff(roles=["store_manager"])).patch(url, {"about": "y"}, format="json").status_code == 200
    assert client_for(make_staff(roles=["cashier"])).get(url).status_code == 403
    from apps.core.models import AuditLog

    assert AuditLog.objects.filter(action="store_profile.updated").exists()
