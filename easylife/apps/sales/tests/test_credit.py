"""Selling on a customer's account (دين), customer payments towards it, the customer file and the customer's own
account pages."""

from decimal import Decimal

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, User
from apps.core.models import AuditLog
from apps.core.testing import client_for, make_admin, make_customer, make_staff
from apps.sales.models import DebtAllocation, Sale
from apps.sales.tests.test_pos import B, _setup, acct_balance, admin, cashier, cashier_user, item, sale, shift, wh  # noqa: F401

D = Decimal
C = "/api/v1/admin/customers/"
A = "/api/v1/account/"
RECEIVABLE, CASH, BANK = "1130", "1110", "1120"


@pytest.fixture
def customer():
    return make_customer()


def credit_sale(client, variant, customer, qty="1", paid=None):
    body = {"lines": [{"variant": str(variant.uuid), "quantity": qty}],
            "payments": [{"method": "cash", "amount": paid}] if paid else [],
            "customer": str(customer.uuid), "on_credit": True}
    return client.post(B + "sales/", body, format="json")


# ------------------------------------------------------------ selling on credit


def test_the_unpaid_part_goes_on_the_customer_account_and_the_books(cashier, shift, item, customer):
    r = credit_sale(cashier, item, customer, qty="2", paid="5")  # total 20, 5 paid now
    assert r.status_code == 201, r.json()
    body = r.json()
    assert body["credit_amount"] == "15.00" and body["balance_due"] == "15.00" and body["change_due"] == "0.00"
    assert body["payments"] == [{"method": "cash", "amount": "5.00", "reference": ""}]
    assert acct_balance(RECEIVABLE) == D("15") and acct_balance(CASH) == D("5")
    # the till only expects the cash really taken
    rep = cashier.get(B + "shift/").json()["report"]
    assert rep["expected_cash"] == "105.00" and rep["credit_total"] == "15.00"
    audit = AuditLog.objects.filter(action="pos.sale_completed").order_by("-id").first()
    assert audit.data["credit"] == "15.00"


def test_the_whole_sale_can_go_on_credit_without_any_payment(cashier, shift, item, customer):
    r = credit_sale(cashier, item, customer)
    assert r.status_code == 201 and r.json()["credit_amount"] == "10.00" and r.json()["payments"] == []
    assert acct_balance(RECEIVABLE) == D("10")


def test_paying_in_full_with_on_credit_leaves_no_debt(cashier, shift, item, customer):
    r = credit_sale(cashier, item, customer, paid="20")
    assert r.json()["credit_amount"] == "0.00" and r.json()["change_due"] == "10.00"


def test_credit_rules(cashier, shift, item, customer, wh):
    # a debt needs a customer
    r = cashier.post(B + "sales/", {"lines": [{"variant": str(item.uuid), "quantity": "1"}], "payments": [], "on_credit": True}, format="json")
    assert r.status_code == 400 and r.json()["code"] == "customer_required"
    # without on_credit a shortfall is still an error
    r = sale(cashier, item, payments=[{"method": "cash", "amount": "3"}], customer=str(customer.uuid))
    assert r.json()["code"] == "payment_insufficient"
    assert Sale.objects.count() == 0
    # selling on credit is its own permission
    plain = client_for(make_staff(perms=["sales.pos"]))
    plain.post(B + "shift/open/", {"warehouse": str(wh.uuid), "opening_cash": "0"}, format="json")
    r = credit_sale(plain, item, customer)
    assert r.status_code == 403 and r.json()["code"] == "credit_not_allowed"
    assert plain.get(B + "config/").json()["can_credit"] is False
    assert cashier.get(B + "config/").json()["can_credit"] is True  # the cashier role includes sales.credit


def test_refund_of_a_credit_sale_cancels_the_debt_unless_part_was_paid(cashier, shift, item, customer, admin):
    first = credit_sale(cashier, item, customer, paid="4").json()
    r = cashier.post(f"{B}sales/{first['uuid']}/refund/", {"reason": "returned"}, format="json")
    assert r.status_code == 403  # cashiers may not refund
    admin.post(B + "shift/open/", {"warehouse": shift["warehouse"]["uuid"], "opening_cash": "0"}, format="json")
    r = admin.post(f"{B}sales/{first['uuid']}/refund/", {"reason": "returned"}, format="json")
    assert r.status_code == 200 and r.json()["balance_due"] == "0.00"
    assert acct_balance(RECEIVABLE) == D("0")

    second = credit_sale(cashier, item, customer).json()
    admin.post(f"{C}{customer.uuid}/payments/", {"amount": "3", "method": "cash"}, format="json")
    r = admin.post(f"{B}sales/{second['uuid']}/refund/", {"reason": "returned"}, format="json")
    assert r.status_code == 400 and r.json()["code"] == "sale_has_debt_payments"


# ------------------------------------------------------------ paying the debt off


def test_payments_settle_the_oldest_invoices_first_and_post_the_books(cashier, shift, item, customer, admin):
    old = credit_sale(cashier, item, customer, paid="3").json()  # owes 7
    new = credit_sale(cashier, item, customer, qty="2").json()  # owes 20
    r = cashier.post(f"{C}{customer.uuid}/payments/", {"amount": "10", "method": "cash"}, format="json")
    assert r.status_code == 403  # recording payments is payments.record

    r = admin.post(f"{C}{customer.uuid}/payments/", {"amount": "10", "method": "card", "reference": "slip 7"},
                   format="json")
    assert r.status_code == 201, r.json()
    body = r.json()
    assert body["balance"] == "17.00" and body["open_invoices"] == 1
    assert body["payment"]["number"].startswith("RCV-") and body["payment"]["method"] == "card"
    assert [(i["number"], i["amount"]) for i in body["payment"]["invoices"]] == [(old["number"], "7.00"), (new["number"], "3.00")]
    assert Sale.objects.get(uuid=old["uuid"]).balance_due == 0
    assert Sale.objects.get(uuid=new["uuid"]).balance_due == D("17")
    assert acct_balance(RECEIVABLE) == D("17") and acct_balance(BANK) == D("10")

    detail = admin.get(f"{B}sales/{new['uuid']}/").json()
    assert detail["debt_payments"][0]["amount"] == "3.00"

    # more than owed, nothing owed, cash without a till
    r = admin.post(f"{C}{customer.uuid}/payments/", {"amount": "18", "method": "card"}, format="json")
    assert r.json()["code"] == "payment_exceeds_debt"
    r = admin.post(f"{C}{customer.uuid}/payments/", {"amount": "5", "method": "cash"}, format="json")
    assert r.status_code == 400 and r.json()["code"] == "shift_required"
    r = admin.post(f"{C}{make_customer().uuid}/payments/", {"amount": "5", "method": "card"}, format="json")
    assert r.json()["code"] == "no_debt"
    assert admin.post(f"{C}{customer.uuid}/payments/", {"amount": "0", "method": "card"}, format="json").status_code == 400


def test_cash_paid_off_at_the_till_is_counted_in_the_shift(cashier_user, shift, item, customer):
    cashier = client_for(cashier_user)
    credit_sale(cashier, item, customer)
    RolePermission.objects.create(role=Role.objects.get(name="cashier"), permission=Permission.objects.get(code="payments.record"))
    from django.core.cache import cache
    cache.clear()
    r = cashier.post(f"{C}{customer.uuid}/payments/", {"amount": "6", "method": "cash"}, format="json")
    assert r.status_code == 201
    rep = cashier.get(B + "shift/").json()["report"]
    assert rep["expected_cash"] == "106.00" and rep["debt_collected_by_method"]["cash"] == "6.00"
    assert acct_balance(CASH) == D("6") and acct_balance(RECEIVABLE) == D("4")


# ------------------------------------------------------------ customers in the back office


def test_staff_add_a_customer_by_name_and_whatsapp_number(cashier, admin):
    r = cashier.post(C, {"full_name": " Umm Ali ", "phone": "0599 123 456"}, format="json")
    assert r.status_code == 201, r.json()
    assert r.json()["full_name"] == "Umm Ali" and r.json()["phone"] == "970599123456" and r.json()["balance"] == "0.00"
    assert User.objects.get(phone="970599123456").user_type == "customer"
    assert AuditLog.objects.filter(action="customer.created").exists()

    r = cashier.post(C, {"full_name": "Again", "phone": "+970599123456"}, format="json")
    assert r.status_code == 409 and r.json()["code"] == "customer_exists"
    staff = make_staff()
    r = admin.post(C, {"full_name": "Staff", "phone": staff.phone}, format="json")
    assert r.status_code == 409 and r.json()["code"] == "phone_taken"
    assert admin.post(C, {"full_name": "X", "phone": "12"}, format="json").json()["code"] == "invalid_phone"
    assert admin.post(C, {"phone": "0599123457"}, format="json").status_code == 400

    # who may add / find customers
    assert client_for(make_staff(perms=["customers.view"])).post(C, {"full_name": "N", "phone": "0599000001"}, format="json").status_code == 403
    assert client_for(make_staff(perms=["customers.create"])).post(C, {"full_name": "N", "phone": "0599000001"}, format="json").status_code == 201
    assert client_for(make_staff(perms=["sales.pos"])).get(C).status_code == 403
    assert cashier.get(C, {"search": "umm"}).json()["results"][0]["full_name"] == "Umm Ali"


def test_the_customer_list_shows_balances_only_to_those_who_may_see_them(cashier, shift, item, customer):
    credit_sale(cashier, item, customer)
    row = next(c for c in client_for(make_staff(perms=["customers.view_balance", "customers.view"])).get(C).json()["results"]
               if c["uuid"] == str(customer.uuid))
    assert row["balance"] == "10.00"
    row = client_for(make_staff(perms=["customers.view"])).get(f"{C}{customer.uuid}/").json()
    assert row["balance"] is None


def test_the_customer_file_lists_debts_invoices_and_payments(cashier, shift, item, customer, admin):
    paid = sale(cashier, item, customer=str(customer.uuid)).json()
    owing = credit_sale(cashier, item, customer, qty="3", paid="10").json()
    admin.post(f"{C}{customer.uuid}/payments/", {"amount": "5", "method": "transfer"}, format="json")

    acc = admin.get(f"{C}{customer.uuid}/account/").json()
    assert acc["customer"]["phone"] == customer.phone
    assert (acc["balance"], acc["open_invoices"], acc["invoices"], acc["total_purchases"], acc["total_paid_off"]) == (
        "15.00", 1, 2, "40.00", "5.00")
    all_inv = admin.get(f"{C}{customer.uuid}/invoices/").json()
    assert [i["number"] for i in all_inv["results"]] == [owing["number"], paid["number"]]
    assert all_inv["results"][0]["balance_due"] == "15.00"
    assert [i["number"] for i in admin.get(f"{C}{customer.uuid}/invoices/", {"open": "1"}).json()["results"]] == [owing["number"]]
    pays = admin.get(f"{C}{customer.uuid}/payments/").json()["results"]
    assert pays[0]["amount"] == "5.00" and pays[0]["received_by"] == "Admin"

    viewer = client_for(make_staff(perms=["customers.view"]))
    for path in ("account/", "invoices/", "payments/"):
        assert viewer.get(f"{C}{customer.uuid}/{path}").status_code == 403
    assert admin.get(f"{C}{make_staff().uuid}/account/").status_code == 404


# ------------------------------------------------------------ the customer's own account


def test_a_customer_sees_only_their_own_debts_and_invoices(cashier, shift, item, customer, admin):
    mine = credit_sale(cashier, item, customer, qty="2", paid="5").json()
    settled = sale(cashier, item, customer=str(customer.uuid)).json()
    other = credit_sale(cashier, item, make_customer()).json()
    admin.post(f"{C}{customer.uuid}/payments/", {"amount": "5", "method": "card"}, format="json")

    me = client_for(customer)
    assert me.get(A + "summary/").json() == {"balance": "10.00", "open_invoices": 1, "invoices": 2,
                                             "total_purchases": "30.00", "total_paid_off": "5.00"}
    numbers = [i["number"] for i in me.get(A + "invoices/").json()["results"]]
    assert numbers == [settled["number"], mine["number"]]
    assert [i["number"] for i in me.get(A + "invoices/", {"open": "1"}).json()["results"]] == [mine["number"]]
    inv = me.get(f"{A}invoices/{mine['uuid']}/").json()
    assert inv["balance_due"] == "10.00" and inv["lines"][0]["quantity"] == "2.000"
    assert inv["debt_payments"][0]["amount"] == "5.00"
    assert "cashier" not in inv and "journal_entry" not in inv
    assert me.get(f"{A}invoices/{other['uuid']}/").status_code == 404
    pays = me.get(A + "payments/").json()["results"]
    assert pays[0]["amount"] == "5.00" and "received_by" not in pays[0]
    assert APIClient().get(A + "summary/").status_code == 401


# ------------------------------------------------------------ permissions sync


def test_new_permissions_reach_existing_system_roles_but_removed_ones_stay_removed():
    cashier_role = Role.objects.get(name="cashier")
    RolePermission.objects.filter(role=cashier_role, permission__code="sales.credit").delete()
    call_command("sync_permissions", verbosity=0)
    assert not cashier_role.permissions.filter(code="sales.credit").exists()  # the admin removed it: respected

    Permission.objects.filter(code="sales.credit").delete()  # as if this code were new
    call_command("sync_permissions", verbosity=0)
    assert cashier_role.permissions.filter(code="sales.credit").exists()
    assert not DebtAllocation.objects.exists()
