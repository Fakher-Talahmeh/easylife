"""Session, staff-management and authorization tests (login itself is OTP-only,
see test_whatsapp.py for the OTP flow)."""

import re

import pytest
from django.core.cache import cache
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounts import whatsapp
from apps.accounts.models import Permission, Role, User, UserPermissionOverride
from apps.core.models import AuditLog

AUTH = "/api/v1/auth/"
ADMIN = "/api/v1/admin/"
_counter = iter(range(10_000))


def new_phone():
    return "97059%07d" % next(_counter)


@pytest.fixture(autouse=True)
def _setup(db):
    cache.clear()
    whatsapp.outbox.clear()
    call_command("sync_permissions")


@pytest.fixture
def api():
    return APIClient()


def otp_login(phone):
    """Full WhatsApp login for an existing account; returns the verify response."""
    cache.clear()  # skip resend cooldown / hourly counters between helper calls
    c = APIClient()
    assert c.post(AUTH + "whatsapp/send-code", {"phone": phone}, format="json").status_code == 200
    code = re.search(r"\b(\d{6})\b", whatsapp.outbox[-1]["text"]).group(1)
    return c.post(AUTH + "whatsapp/verify", {"phone": phone, "code": code}, format="json")


def client_for(user):
    r = otp_login(user.phone)
    assert r.status_code in (200, 201), r.data
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
    c.tokens = r.data
    return c


def make_customer():
    return User.objects.create_customer(new_phone(), full_name="Cust")


def staff_with(perms=(), roles=()):
    u = User.objects.create_staff(new_phone(), full_name="Staff")
    for r in roles:
        u.user_roles.create(role=Role.objects.get(name=r))
    for p in perms:
        UserPermissionOverride.objects.create(
            user=u, permission=Permission.objects.get(code=p), effect="allow")
    return u


@pytest.fixture
def admin_user():
    return User.objects.create_superadmin(new_phone(), full_name="Boss")


@pytest.fixture
def admin_client(admin_user):
    return client_for(admin_user)


# ------------------------------------------------------------ one login for everyone


def test_same_login_returns_user_type_for_each_kind(admin_user):
    cust = otp_login(make_customer().phone).data
    staff = otp_login(staff_with(roles=["sales"]).phone).data
    admin = otp_login(admin_user.phone).data
    assert (cust["user_type"], cust["is_superadmin"]) == ("customer", False)
    assert (staff["user_type"], staff["is_superadmin"]) == ("staff", False)
    assert (admin["user_type"], admin["is_superadmin"]) == ("staff", True)
    assert "orders.view" in staff["user"]["permissions"]


def test_email_and_password_endpoints_are_gone(api):
    for path in ["login", "register", "change-password", "password/reset", "password/set"]:
        assert api.post(AUTH + path, {}, format="json").status_code == 404


def test_inactive_staff_cannot_login():
    u = staff_with(roles=["sales"])
    User.objects.filter(pk=u.pk).update(is_active=False)
    assert otp_login(u.phone).status_code == 400


# ------------------------------------------------------------ session


def test_me_requires_auth_and_blocks_mass_assignment(api):
    assert api.get(AUTH + "me").status_code == 401
    cust = make_customer()
    c = client_for(cust)
    r = c.patch(AUTH + "me", {"full_name": "New", "user_type": "staff", "is_superadmin": True,
                              "phone": "970500000000"}, format="json")
    assert r.status_code == 200
    u = User.objects.get(pk=cust.pk)
    assert u.full_name == "New" and u.user_type == "customer" and not u.is_superadmin
    assert u.phone == cust.phone


def test_refresh_rotates_and_logout_blacklists(api):
    c = client_for(make_customer())
    old = c.tokens["refresh"]
    r = api.post(AUTH + "refresh", {"refresh": old}, format="json")
    assert r.status_code == 200 and r.data["refresh"] != old
    assert api.post(AUTH + "refresh", {"refresh": old}, format="json").status_code == 401
    assert c.post(AUTH + "logout", {"refresh": r.data["refresh"]}, format="json").status_code == 204
    assert api.post(AUTH + "refresh", {"refresh": r.data["refresh"]}, format="json").status_code == 401


# ------------------------------------------------------------ authorization


def test_customer_cannot_use_admin_api():
    c = client_for(make_customer())
    assert c.get(ADMIN + "users/").status_code == 403
    assert c.post(ADMIN + "users/", {"phone": new_phone(), "full_name": "N"}, format="json").status_code == 403


def test_staff_without_permission_denied_default_deny():
    assert client_for(staff_with()).get(ADMIN + "users/").status_code == 403


def test_admin_creates_staff_who_logs_in_with_whatsapp(admin_client, django_capture_on_commit_callbacks):
    role = Role.objects.get(name="warehouse")
    phone = new_phone()
    with django_capture_on_commit_callbacks(execute=True):
        r = admin_client.post(ADMIN + "users/", {"phone": "+" + phone, "full_name": "Wally",
                                                 "role_ids": [role.id]}, format="json")
    assert r.status_code == 201, r.data
    assert r.data["roles"] == ["warehouse"] and r.data["phone"] == phone
    assert "email" not in r.data
    u = User.objects.get(phone=phone)
    assert u.user_type == "staff" and not u.is_superadmin and not u.has_usable_password()
    # heads-up message sent to the new staff member
    assert r.data["whatsapp_notified"] is True
    assert any(m["phone"] == phone for m in whatsapp.outbox)

    login = otp_login(phone)
    assert login.status_code == 200 and login.data["user_type"] == "staff"
    perms = login.data["user"]["permissions"]
    assert "inventory.adjust" in perms and "users.create" not in perms
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    assert c.get(ADMIN + "users/").status_code == 403
    assert AuditLog.objects.filter(action="staff.created").exists()


def test_cannot_create_duplicate_or_invalid_phone(admin_client):
    existing = make_customer()
    r = admin_client.post(ADMIN + "users/", {"phone": existing.phone, "full_name": "X"}, format="json")
    assert r.status_code == 400 and r.data["code"] == "phone_taken"
    r = admin_client.post(ADMIN + "users/", {"phone": "12", "full_name": "X"}, format="json")
    assert r.status_code == 400 and r.data["code"] == "invalid_phone"


def test_non_admin_cannot_create_admin_or_escalate():
    c = client_for(staff_with(perms=["users.create", "users.manage_roles", "users.view"]))
    assert c.post(ADMIN + "users/", {"phone": new_phone(), "full_name": "A", "is_superadmin": True},
                  format="json").status_code == 403
    role = Role.objects.get(name="warehouse")  # holds permissions the manager lacks
    r = c.post(ADMIN + "users/", {"phone": new_phone(), "full_name": "W", "role_ids": [role.id]},
               format="json")
    assert r.status_code == 403 and r.data["code"] == "cannot_grant"
    assert c.post(ADMIN + "users/", {"phone": new_phone(), "full_name": "N"}, format="json").status_code == 201


def test_override_deny_beats_role():
    u = staff_with(roles=["warehouse"])
    UserPermissionOverride.objects.create(
        user=u, permission=Permission.objects.get(code="inventory.adjust"), effect="deny")
    perms = client_for(u).get(AUTH + "me").data["permissions"]
    assert "inventory.adjust" not in perms and "inventory.view" in perms


def test_deactivate_revokes_access_and_protects_last_admin(admin_client, admin_user, api):
    victim = staff_with(roles=["sales"])
    c = client_for(victim)
    r = admin_client.post(f"{ADMIN}users/{victim.uuid}/deactivate/")
    assert r.status_code == 200
    assert c.get(AUTH + "me").status_code == 401
    assert api.post(AUTH + "refresh", {"refresh": c.tokens["refresh"]}, format="json").status_code == 401
    r = admin_client.post(f"{ADMIN}users/{admin_user.uuid}/deactivate/")
    assert r.status_code == 400 and r.data["code"] == "self_lockout"


def test_role_change_applies_immediately_and_is_audited(admin_client):
    u = staff_with(roles=["sales"])
    c = client_for(u)
    assert "orders.view" in c.get(AUTH + "me").data["permissions"]
    r = admin_client.put(f"{ADMIN}users/{u.uuid}/roles/", {"role_ids": []}, format="json")
    assert r.status_code == 200
    assert c.get(AUTH + "me").data["permissions"] == []
    assert AuditLog.objects.filter(action="staff.roles_changed").exists()


def test_create_admin_command_is_idempotent_and_promotes():
    phone = new_phone()
    call_command("create_admin", phone="0" + phone[3:], name="Root")
    call_command("create_admin", phone=phone, name="Root")
    u = User.objects.get(phone=phone)
    assert u.is_superadmin and u.user_type == "staff"
    assert otp_login(phone).data["is_superadmin"] is True


def test_audit_log_is_append_only(admin_user):
    log = AuditLog.objects.create(action="x")
    with pytest.raises(PermissionError):
        log.delete()
    log.action = "tampered"
    with pytest.raises(PermissionError):
        log.save()


def test_every_permission_guarded_view_declares_its_permission():
    from django.urls import get_resolver

    def walk(patterns):
        for p in patterns:
            if hasattr(p, "url_patterns"):
                yield from walk(p.url_patterns)
            else:
                yield p

    checked = 0
    for p in walk(get_resolver().url_patterns):
        cls = getattr(p.callback, "cls", None) or getattr(p.callback, "view_class", None)
        if cls and "HasPermission" in [c.__name__ for c in getattr(cls, "permission_classes", [])]:
            assert getattr(cls, "required_permission", None), str(p.pattern)
            checked += 1
    assert checked > 0


# ------------------------------------------------------------ promote customer -> staff


def test_customers_list_and_search(admin_client):
    a = User.objects.create_customer(new_phone(), full_name="Layla Hassan")
    User.objects.create_customer(new_phone(), full_name="Omar Said")
    User.objects.create_staff(new_phone(), full_name="Layla Staff")  # not a customer
    r = admin_client.get(ADMIN + "customers/?search=layla")
    assert r.status_code == 200
    assert [c["uuid"] for c in r.data["results"]] == [str(a.uuid)]
    assert set(r.data["results"][0]) == {"uuid", "full_name", "phone", "is_active", "created_at", "balance"}
    assert admin_client.get(ADMIN + "customers/").data["count"] == 2


def test_customers_endpoint_permissions():
    assert client_for(make_customer()).get(ADMIN + "customers/").status_code == 403
    assert client_for(staff_with()).get(ADMIN + "customers/").status_code == 403
    assert client_for(staff_with(roles=["sales"])).get(ADMIN + "customers/").status_code == 200


def test_admin_promotes_customer_to_staff_with_roles(admin_client, django_capture_on_commit_callbacks):
    cust = make_customer()
    c = client_for(cust)
    assert c.get(ADMIN + "users/").status_code == 403
    role = Role.objects.get(name="sales")
    with django_capture_on_commit_callbacks(execute=True):
        r = admin_client.post(f"{ADMIN}customers/{cust.uuid}/promote/", {"role_ids": [role.id]}, format="json")
    assert r.status_code == 200, r.data
    assert r.data["roles"] == ["sales"] and r.data["phone"] == cust.phone
    assert User.objects.get(pk=cust.pk).user_type == "staff"
    assert r.data["whatsapp_notified"] is True
    notices = [m for m in whatsapp.outbox if m["phone"] == cust.phone and "sales" in m["text"]]
    assert len(notices) == 1 and "Cust" in notices[0]["text"]  # name + roles in the notice
    # appears in the staff list and no longer in the customers list
    assert str(cust.uuid) in [u["uuid"] for u in admin_client.get(ADMIN + "users/").data["results"]]
    assert str(cust.uuid) not in [u["uuid"] for u in admin_client.get(ADMIN + "customers/").data["results"]]
    # same session now reflects the new type and permissions, no new login needed
    me = c.get(AUTH + "me").data
    assert me["user_type"] == "staff" and "orders.view" in me["permissions"]
    assert AuditLog.objects.filter(action="staff.promoted").exists()


def test_promoted_customer_has_no_permissions_until_roles_assigned(admin_client):
    cust = make_customer()
    assert admin_client.post(f"{ADMIN}customers/{cust.uuid}/promote/", {}, format="json").status_code == 200
    assert client_for(cust).get(AUTH + "me").data["permissions"] == []
    r = admin_client.put(f"{ADMIN}users/{cust.uuid}/roles/", {"role_ids": [Role.objects.get(name="warehouse").id]},
                         format="json")
    assert r.status_code == 200
    assert "inventory.adjust" in client_for(cust).get(AUTH + "me").data["permissions"]


def test_promote_rules(admin_client, admin_user):
    cust = make_customer()
    url = f"{ADMIN}customers/{cust.uuid}/promote/"
    assert admin_client.post(url, {"role_ids": [9999]}, format="json").data["code"] == "invalid_role"
    assert admin_client.post(url, {}, format="json").status_code == 200
    assert admin_client.post(url, {}, format="json").status_code == 404  # now staff, no longer a customer
    inactive = User.objects.create_customer(new_phone(), full_name="Off", is_active=False)
    r = admin_client.post(f"{ADMIN}customers/{inactive.uuid}/promote/", {}, format="json")
    assert r.status_code == 400 and r.data["code"] == "inactive_account"


def test_promote_requires_users_create_and_blocks_escalation():
    cust = make_customer()
    url = f"{ADMIN}customers/{cust.uuid}/promote/"
    # a sales person can see customers but cannot promote them
    assert client_for(staff_with(roles=["sales"])).post(url, {}, format="json").status_code == 403
    # a manager who can promote cannot hand out roles holding permissions they lack
    mgr = client_for(staff_with(perms=["users.create", "users.manage_roles"]))
    role = Role.objects.get(name="warehouse")
    r = mgr.post(url, {"role_ids": [role.id]}, format="json")
    assert r.status_code == 403 and r.data["code"] == "cannot_grant"
    assert User.objects.get(pk=cust.pk).user_type == "customer"  # nothing changed


def test_promotion_succeeds_even_if_whatsapp_notice_fails(admin_client, monkeypatch):
    def boom(phone, text):
        raise whatsapp.WhatsAppDeliveryError("down")

    monkeypatch.setattr(whatsapp, "send_text", boom)
    cust = make_customer()
    r = admin_client.post(f"{ADMIN}customers/{cust.uuid}/promote/", {}, format="json")
    assert r.status_code == 200 and r.data["whatsapp_notified"] is False
    assert User.objects.get(pk=cust.pk).user_type == "staff"
    assert AuditLog.objects.filter(action="staff.notify_failed").exists()


def test_invalid_token_uses_the_standard_error_shape(api):
    api.credentials(HTTP_AUTHORIZATION="Bearer garbage")
    r = api.get(AUTH + "me")
    assert r.status_code == 401
    assert r.data == {"code": "token_not_valid", "detail": "Token is invalid or expired."}
