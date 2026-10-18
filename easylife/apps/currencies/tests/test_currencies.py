from datetime import date
from decimal import Decimal

import pytest

from apps.core.testing import bootstrap, client_for, make_admin, make_customer, make_staff
from apps.currencies import services
from apps.currencies.models import Currency

BASE = "/api/v1/admin/"


@pytest.fixture(autouse=True)
def _setup(db):
    bootstrap()


@pytest.fixture
def admin():
    return client_for(make_admin())


def test_seed_creates_ils_base_usd_jod_and_is_rerunnable():
    from django.core.management import call_command

    call_command("seed_currencies", verbosity=0)
    assert list(Currency.objects.order_by("code").values_list("code", "is_base", "decimals")) == [
        ("ILS", True, 2), ("JOD", False, 3), ("USD", False, 2)]


def test_only_one_base_currency_allowed():
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        Currency.objects.create(code="EUR", name="Euro", symbol="E", is_base=True)


def test_list_and_permissions(admin):
    r = admin.get(BASE + "currencies/")
    assert r.status_code == 200 and [c["code"] for c in r.data] == ["ILS", "JOD", "USD"]
    assert r.data[0]["is_base"] is True
    assert client_for(make_customer()).get(BASE + "currencies/").status_code == 403
    assert client_for(make_staff()).get(BASE + "currencies/").status_code == 403
    assert client_for(make_staff(["products.view"])).get(BASE + "currencies/").status_code == 200
    # reading is not writing
    viewer = client_for(make_staff(["products.view"]))
    assert viewer.post(BASE + "currencies/", {"code": "EUR", "name": "Euro", "symbol": "E"}, format="json").status_code == 403


def test_create_update_currency_rules(admin):
    r = admin.post(BASE + "currencies/", {"code": "eur", "name": "Euro", "symbol": "E", "decimals": 2}, format="json")
    assert r.status_code == 201 and r.data["code"] == "EUR" and r.data["is_base"] is False
    assert admin.post(BASE + "currencies/", {"code": "EUR", "name": "x", "symbol": "x"}, format="json").status_code == 400
    assert admin.post(BASE + "currencies/", {"code": "EU", "name": "x", "symbol": "x"}, format="json").status_code == 400
    # is_base cannot be set through the API
    r = admin.post(BASE + "currencies/", {"code": "GBP", "name": "Pound", "symbol": "P", "is_base": True}, format="json")
    assert r.status_code == 201 and r.data["is_base"] is False
    assert admin.patch(BASE + "currencies/eur/", {"name": "Euro!"}, format="json").data["name"] == "Euro!"
    assert admin.patch(BASE + "currencies/ILS/", {"is_active": False}, format="json").status_code == 400
    assert admin.patch(BASE + "currencies/EUR/", {"code": "XXX"}, format="json").status_code == 400


def test_exchange_rate_upsert_and_listing(admin):
    r = admin.post(BASE + "exchange-rates/", {"currency": "USD", "date": "2026-10-01", "rate_to_base": "3.70000000"}, format="json")
    assert r.status_code == 201 and r.data["currency"] == "USD"
    r = admin.post(BASE + "exchange-rates/", {"currency": "USD", "date": "2026-10-01", "rate_to_base": "3.65"}, format="json")
    assert r.status_code == 200 and Decimal(r.data["rate_to_base"]) == Decimal("3.65")
    admin.post(BASE + "exchange-rates/", {"currency": "JOD", "date": "2026-10-01", "rate_to_base": "5.2"}, format="json")
    assert admin.get(BASE + "exchange-rates/").data["count"] == 2
    assert admin.get(BASE + "exchange-rates/?currency=usd").data["count"] == 1
    assert admin.get(BASE + "exchange-rates/?from=2026-10-02").data["count"] == 0


def test_exchange_rate_validation(admin):
    post = lambda d: admin.post(BASE + "exchange-rates/", d, format="json")  # noqa: E731
    assert post({"currency": "ILS", "date": "2026-10-01", "rate_to_base": "1"}).data["code"] == "base_currency_rate"
    assert post({"currency": "USD", "date": "2026-10-01", "rate_to_base": "0"}).status_code == 400
    assert post({"currency": "XXX", "date": "2026-10-01", "rate_to_base": "3"}).status_code == 400
    assert client_for(make_staff(["accounting.view"])).post(
        BASE + "exchange-rates/", {"currency": "USD", "date": "2026-10-01", "rate_to_base": "3"}, format="json"
    ).status_code == 403


def test_convert_uses_latest_rate_not_after_date_and_rounds():
    from apps.core.exceptions import ServiceError

    ils, usd, jod = (Currency.objects.get(code=c) for c in ("ILS", "USD", "JOD"))
    admin_user = make_admin()
    services.set_exchange_rate(actor=admin_user, currency=usd, date=date(2026, 10, 1), rate_to_base=Decimal("3.70"))
    services.set_exchange_rate(actor=admin_user, currency=usd, date=date(2026, 10, 10), rate_to_base=Decimal("4.00"))
    services.set_exchange_rate(actor=admin_user, currency=jod, date=date(2026, 10, 1), rate_to_base=Decimal("5.20"))
    assert services.convert(100, usd, ils, date(2026, 10, 5)) == Decimal("370.00")
    assert services.convert(100, usd, ils, date(2026, 10, 12)) == Decimal("400.00")
    assert services.convert(10, jod, usd, date(2026, 10, 5)) == Decimal("14.05")  # 52 / 3.70
    assert services.convert(Decimal("3.70"), ils, usd, date(2026, 10, 5)) == Decimal("1.00")
    assert services.convert(5, ils, ils) == Decimal("5.00")
    with pytest.raises(ServiceError) as e:
        services.get_rate(usd, date(2026, 9, 1))
    assert e.value.code == "rate_missing"
