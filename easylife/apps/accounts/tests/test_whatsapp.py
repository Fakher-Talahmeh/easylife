import re

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APIClient

from apps.accounts import whatsapp
from apps.accounts.models import User, WhatsAppCode
from apps.core.models import AuditLog

SEND = "/api/v1/auth/whatsapp/send-code"
VERIFY = "/api/v1/auth/whatsapp/verify"
PHONE = "0591234567"
PHONE_NORM = "970591234567"


@pytest.fixture(autouse=True)
def _setup(db):
    cache.clear()
    whatsapp.outbox.clear()
    call_command("sync_permissions")


@pytest.fixture
def api():
    return APIClient()


def last_code():
    return re.search(r"\b(\d{6})\b", whatsapp.outbox[-1]["text"]).group(1)


def send(api, phone=PHONE):
    return api.post(SEND, {"phone": phone}, format="json")


def verify(api, code, phone=PHONE, **extra):
    return api.post(VERIFY, {"phone": phone, "code": code, **extra}, format="json")


# ------------------------------------------------------------ normalisation


@pytest.mark.parametrize("raw", ["0591234567", "+970 59 123 4567", "00970591234567", "970591234567"])
def test_phone_normalisation(raw):
    assert whatsapp.normalize_phone(raw) == PHONE_NORM


@pytest.mark.parametrize("raw", ["", "abc", "123", "+0000000000000000000"])
def test_phone_invalid(raw):
    assert whatsapp.normalize_phone(raw) is None


# ------------------------------------------------------------ flow


def test_register_new_customer_via_whatsapp(api):
    assert send(api).status_code == 200
    assert whatsapp.outbox[-1]["phone"] == PHONE_NORM
    code = last_code()

    # new number without a name: asked for it, and the code stays valid
    r = verify(api, code)
    assert r.status_code == 400 and r.data["code"] == "full_name_required"

    r = verify(api, code, full_name="Sara")
    assert r.status_code == 201 and r.data["created"] is True
    assert r.data["user_type"] == "customer" and r.data["is_superadmin"] is False
    assert r.data["access"] and r.data["refresh"]
    u = User.objects.get(phone=PHONE_NORM)
    assert not u.has_usable_password() and u.user_type == "customer"
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
    assert c.get("/api/v1/auth/me").data["phone"] == PHONE_NORM


def test_login_existing_customer_via_whatsapp(api):
    User.objects.create_customer(PHONE_NORM, full_name="Old")
    send(api, "+970591234567")
    r = verify(api, last_code(), phone="00970591234567")
    assert r.status_code == 200 and r.data["created"] is False
    assert User.objects.filter(phone=PHONE_NORM).count() == 1


def test_code_is_single_use_and_stored_hashed(api):
    send(api)
    code = last_code()
    rec = WhatsAppCode.objects.get()
    assert code not in rec.code_hash and len(rec.code_hash) == 64
    assert verify(api, code, full_name="A").status_code == 201
    assert verify(api, code, full_name="A").status_code == 400  # replay rejected


def test_wrong_code_burns_after_max_attempts(api):
    send(api)
    good = last_code()
    bad = "000000" if good != "000000" else "111111"
    for _ in range(5):
        r = verify(api, bad, full_name="A")
        assert r.status_code == 400 and r.data["code"] == "invalid_code"
    assert verify(api, good, full_name="A").status_code == 400  # burned
    assert not User.objects.filter(phone=PHONE_NORM).exists()


def test_expired_code_rejected(api):
    send(api)
    WhatsAppCode.objects.update(expires_at="2000-01-01T00:00:00Z")
    assert verify(api, last_code(), full_name="A").status_code == 400


def test_new_code_invalidates_previous(api):
    send(api)
    first = last_code()
    cache.clear()  # skip resend cooldown
    send(api)
    second = last_code()
    if first != second:
        assert verify(api, first, full_name="A").status_code == 400
    assert verify(api, second, full_name="A").status_code == 201


def test_resend_cooldown_and_hourly_limit(api):
    assert send(api).status_code == 200
    r = send(api)
    assert r.status_code == 429 and r.data["code"] == "otp_cooldown"
    for _ in range(4):
        cache.delete(f"otp_cd:{PHONE_NORM}")
        assert send(api).status_code == 200
    cache.delete(f"otp_cd:{PHONE_NORM}")
    assert send(api).data["code"] == "otp_limit"


def test_send_response_does_not_reveal_account_existence(api):
    User.objects.create_customer(PHONE_NORM, full_name="Old")
    a = send(api)
    b = send(api, "0599999999")
    assert a.status_code == b.status_code == 200 and a.data == b.data


def test_invalid_phone_rejected(api):
    r = send(api, "12")
    assert r.status_code == 400 and r.data["code"] == "invalid_phone"


def test_staff_and_admin_login_with_whatsapp_too(api):
    User.objects.create_staff(PHONE_NORM, full_name="S")
    send(api)
    r = verify(api, last_code())
    assert r.status_code == 200 and r.data["user_type"] == "staff" and r.data["created"] is False


def test_login_records_last_login(api):
    u = User.objects.create_staff(PHONE_NORM, full_name="S")
    assert u.last_login is None
    send(api)
    assert verify(api, last_code()).status_code == 200
    u.refresh_from_db()
    assert u.last_login is not None


def test_inactive_customer_cannot_login(api):
    User.objects.create_customer(PHONE_NORM, full_name="X", is_active=False)
    send(api)
    assert verify(api, last_code(), full_name="X").status_code == 400


def test_delivery_failure_returns_502_and_leaves_no_code(api, monkeypatch):
    def boom(phone, text):
        raise whatsapp.WhatsAppDeliveryError("down")

    monkeypatch.setattr(whatsapp, "send_text", boom)
    r = send(api)
    assert r.status_code == 502 and r.data["code"] == "delivery_failed"
    assert WhatsAppCode.objects.count() == 0
    monkeypatch.undo()
    assert send(api).status_code == 200  # no cooldown was applied by the failure


def test_audit_never_contains_code(api):
    send(api)
    code = last_code()
    verify(api, code, full_name="A")
    assert AuditLog.objects.filter(action__in=["otp.sent", "auth.register"]).count() == 2
    assert all(code not in str(log.data) for log in AuditLog.objects.all())


# ------------------------------------------------------------ eksir client (HTTP mocked)


@override_settings(WHATSAPP_BACKEND="eksir", WHATSAPP_API_KEY="test-key",
                   WHATSAPP_API_URL="https://example.test/send")
def test_eksir_request_shape(monkeypatch):
    captured = {}

    class Resp:
        status_code = 200

    def fake_post(url, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        return Resp()

    monkeypatch.setattr(whatsapp.requests, "post", fake_post)
    whatsapp.send_text(PHONE_NORM, "hello")
    assert captured["json"] == {"to": f"{PHONE_NORM}@c.us", "text": "hello"}
    assert captured["headers"] == {"x-eksir-user-api-key": "test-key"}
    assert captured["timeout"] == 10


@override_settings(WHATSAPP_BACKEND="eksir", WHATSAPP_API_KEY="")
def test_eksir_requires_key():
    with pytest.raises(whatsapp.WhatsAppDeliveryError):
        whatsapp.send_text(PHONE_NORM, "x")


@override_settings(WHATSAPP_BACKEND="eksir", WHATSAPP_API_KEY="k")
def test_eksir_http_error_is_delivery_error(monkeypatch):
    class Resp:
        status_code = 500

    monkeypatch.setattr(whatsapp.requests, "post", lambda *a, **k: Resp())
    with pytest.raises(whatsapp.WhatsAppDeliveryError):
        whatsapp.send_text(PHONE_NORM, "x")
