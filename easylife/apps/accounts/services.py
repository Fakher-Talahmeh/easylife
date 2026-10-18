"""All write-side auth logic lives here; views stay thin."""

import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core import audit
from apps.core.exceptions import ServiceError

from . import rbac, whatsapp
from .models import Permission, Role, User, UserRole, UserType, WhatsAppCode

# ------------------------------------------------------------------ helpers


def revoke_all_tokens(user):
    """Blacklist every outstanding refresh token (password change, deactivation)."""
    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)


def issue_tokens(user):
    refresh = RefreshToken.for_user(user)
    refresh["user_type"] = user.user_type
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def logout(*, refresh_token):
    try:
        RefreshToken(refresh_token).blacklist()
    except Exception:  # invalid/expired/already blacklisted: nothing to revoke
        pass


# ------------------------------------------------------------------ WhatsApp OTP


def _hash_code(phone, code):
    return hmac.new(settings.SECRET_KEY.encode(), f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


def _bad_phone():
    return ServiceError("invalid_phone", "Enter a valid phone number.", fields={"phone": ["Invalid phone number."]})


def send_whatsapp_code(*, phone, request=None):
    """Send a one-time code. The response is identical whether or not an account exists."""
    phone = whatsapp.normalize_phone(phone)
    if not phone:
        raise _bad_phone()

    cooldown_key, count_key = f"otp_cd:{phone}", f"otp_cnt:{phone}"
    if cache.get(cooldown_key):
        raise ServiceError("otp_cooldown", "Please wait before requesting another code.",
                           status.HTTP_429_TOO_MANY_REQUESTS)
    if cache.get(count_key, 0) >= settings.OTP_MAX_SENDS_PER_HOUR:
        raise ServiceError("otp_limit", "Too many codes requested. Try again later.",
                           status.HTTP_429_TOO_MANY_REQUESTS)

    code = "".join(secrets.choice("0123456789") for _ in range(settings.OTP_LENGTH))
    now = timezone.now()
    minutes = settings.OTP_TTL_SECONDS // 60
    message = (
        f"رمز التحقق الخاص بك في EasyLife: {code}\n"
        f"صالح لمدة {minutes} دقائق. لا تشاركه مع أي شخص."
    )
    try:
        with transaction.atomic():
            # Only the newest code is ever valid.
            WhatsAppCode.objects.filter(phone=phone, consumed_at__isnull=True).update(consumed_at=now)
            record = WhatsAppCode.objects.create(
                phone=phone,
                code_hash=_hash_code(phone, code),
                expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
                requested_ip=audit.client_ip(request),
            )
            whatsapp.send_text(phone, message)
    except whatsapp.WhatsAppDeliveryError:
        # The atomic block rolled back, so no usable code is left behind.
        audit.log("otp.delivery_failed", request=request, phone_tail=phone[-4:])
        raise ServiceError("delivery_failed", "Could not send the code. Try again shortly.",
                           status.HTTP_502_BAD_GATEWAY)

    cache.set(cooldown_key, True, settings.OTP_RESEND_COOLDOWN_SECONDS)
    cache.set(count_key, cache.get(count_key, 0) + 1, 3600)
    audit.log("otp.sent", request=request, phone_tail=phone[-4:], record_id=record.pk)
    return {"expires_in": settings.OTP_TTL_SECONDS, "resend_in": settings.OTP_RESEND_COOLDOWN_SECONDS}


def _invalid_code():
    return ServiceError("invalid_code", "The code is incorrect or has expired.")


def verify_whatsapp_code(*, phone, code, full_name="", request=None):
    """Verify the code, then log in the existing account (customer, staff or admin) or create
    a customer account for an unknown number. This is the only way to authenticate.
    Not wrapped in one outer transaction: a wrong guess must persist its attempt
    counter even though we then raise an error.
    """
    phone = whatsapp.normalize_phone(phone)
    if not phone:
        raise _bad_phone()

    wrong_code = False
    with transaction.atomic():
        record = (
            WhatsAppCode.objects.select_for_update()
            .filter(phone=phone, consumed_at__isnull=True, expires_at__gt=timezone.now())
            .order_by("-created_at")
            .first()
        )
        if record is None or record.attempts >= settings.OTP_MAX_ATTEMPTS:
            raise _invalid_code()

        if not hmac.compare_digest(record.code_hash, _hash_code(phone, (code or "").strip())):
            record.attempts += 1
            if record.attempts >= settings.OTP_MAX_ATTEMPTS:
                record.consumed_at = timezone.now()  # too many guesses: burn the code
            record.save(update_fields=["attempts", "consumed_at"])
            wrong_code = True
        else:
            user = User.objects.filter(phone=phone).first()
            created = False
            if user is None:
                full_name = (full_name or "").strip()
                if not full_name:
                    # Valid code, new number: ask for a name without consuming the code.
                    raise ServiceError("full_name_required", "Enter your name to create the account.",
                                       fields={"full_name": ["This field is required."]})
                user = User.objects.create_customer(phone, full_name=full_name)
                created = True
            elif not user.is_active:
                raise _invalid_code()
            record.consumed_at = timezone.now()
            record.save(update_fields=["consumed_at"])

    if wrong_code:
        audit.log("otp.failed", request=request, phone_tail=phone[-4:])
        raise _invalid_code()

    user.last_login = timezone.now()  # shown in the staff list
    user.save(update_fields=["last_login"])
    audit.log("auth.register" if created else "auth.login", actor=user, request=request,
              method="whatsapp", user_type=user.user_type)
    return user, issue_tokens(user), created


# ------------------------------------------------------------------ staff management


def _assert_can_grant(actor, permission_codes):
    """Anti privilege-escalation: you can only grant what you hold yourself."""
    if actor.is_superadmin:
        return
    missing = set(permission_codes) - rbac.effective_permissions(actor)
    if missing:
        raise ServiceError(
            "cannot_grant",
            "You cannot grant permissions you do not hold.",
            status.HTTP_403_FORBIDDEN,
            fields={"permissions": sorted(missing)},
        )


def _role_codes(roles):
    return set(Permission.objects.filter(roles__in=roles).values_list("code", flat=True))


@transaction.atomic
def create_staff(*, actor, phone, full_name, role_ids=(), is_superadmin=False, request=None):
    phone = whatsapp.normalize_phone(phone)
    if not phone:
        raise _bad_phone()
    if User.objects.filter(phone=phone).exists():
        raise ServiceError("phone_taken", "This phone is already registered.",
                           fields={"phone": ["This phone is already registered."]})
    if is_superadmin and not actor.is_superadmin:
        raise ServiceError(
            "cannot_grant", "Only an admin can create admins.", status.HTTP_403_FORBIDDEN
        )

    roles = list(Role.objects.filter(pk__in=role_ids))
    if len(roles) != len(set(role_ids)):
        raise ServiceError("invalid_role", "One or more roles do not exist.",
                           fields={"role_ids": ["Unknown role."]})
    if roles:
        if not rbac.has_permission(actor, "users.manage_roles") and not actor.is_superadmin:
            raise ServiceError(
                "forbidden", "Missing users.manage_roles.", status.HTTP_403_FORBIDDEN
            )
        _assert_can_grant(actor, _role_codes(roles))

    user = User.objects.create_staff(phone, full_name=full_name, is_superadmin=is_superadmin)
    UserRole.objects.bulk_create([UserRole(user=user, role=r, assigned_by=actor) for r in roles])
    audit.log(
        "staff.created", actor=actor, request=request, obj=user,
        roles=[r.name for r in roles], is_superadmin=is_superadmin,
    )
    # Best-effort heads-up; the staff member signs in with a normal WhatsApp code.
    return user


@transaction.atomic
def create_customer(*, actor, phone, full_name, request=None):
    """Staff register a customer (e.g. at the till, to sell on credit). The customer later signs in with a WhatsApp
    code sent to this number, like any other customer."""
    phone = whatsapp.normalize_phone(phone)
    if not phone:
        raise _bad_phone()
    full_name = (full_name or "").strip()
    if not full_name:
        raise ServiceError("validation_error", "A name is required.", fields={"full_name": ["This field is required."]})
    existing = User.objects.filter(phone=phone).first()
    if existing is not None:
        code = "customer_exists" if existing.user_type == UserType.CUSTOMER else "phone_taken"
        raise ServiceError(code, "This phone is already registered.", status.HTTP_409_CONFLICT,
                           fields={"phone": ["This phone is already registered."]})
    user = User.objects.create_customer(phone, full_name=full_name[:150])
    audit.log("customer.created", actor=actor, request=request, obj=user)
    return user


@transaction.atomic
def promote_to_staff(*, actor, user, role_ids=(), request=None):
    """Admin turns an existing customer into a staff member (no permissions until roles are given)."""
    if user.user_type == UserType.STAFF:
        raise ServiceError("already_staff", "This account is already a staff member.")
    if not user.is_active:
        raise ServiceError("inactive_account", "Activate the account before promoting it.")

    roles = list(Role.objects.filter(pk__in=role_ids))
    if len(roles) != len(set(role_ids)):
        raise ServiceError("invalid_role", "One or more roles do not exist.",
                           fields={"role_ids": ["Unknown role."]})
    if roles:
        if not rbac.has_permission(actor, "users.manage_roles") and not actor.is_superadmin:
            raise ServiceError("forbidden", "Missing users.manage_roles.", status.HTTP_403_FORBIDDEN)
        _assert_can_grant(actor, _role_codes(roles))

    user.user_type = UserType.STAFF
    user.save(update_fields=["user_type", "updated_at"])
    UserRole.objects.bulk_create([UserRole(user=user, role=r, assigned_by=actor) for r in roles])
    rbac.invalidate(user)
    audit.log("staff.promoted", actor=actor, request=request, obj=user, roles=[r.name for r in roles])
    return user


def notify_staff_added(user):
    """WhatsApp heads-up to a new staff member. Call it AFTER the transaction committed.

    Returns True if the API accepted the message. A failure never undoes the promotion/creation;
    the caller reports it in the response and it is audited."""
    roles = list(user.user_roles.select_related("role").values_list("role__name", flat=True))
    roles_line = f"\nالأدوار: {', '.join(roles)}" if roles else ""
    try:
        whatsapp.send_text(
            user.phone,
            f"مرحباً {user.full_name}، تمت إضافتك كموظف في EasyLife.{roles_line}\n"
            "سجّل الدخول من صفحة الدخول برقمك هذا وسيصلك رمز تحقق على واتساب.",
        )
    except whatsapp.WhatsAppDeliveryError:
        audit.log("staff.notify_failed", obj=user)
        return False
    audit.log("staff.notified", obj=user)
    return True


def _other_active_admins(user):
    return User.objects.filter(is_superadmin=True, is_active=True).exclude(pk=user.pk).exists()


@transaction.atomic
def set_active(*, actor, user, active, request=None):
    if user.user_type != UserType.STAFF:
        raise ServiceError("not_staff", "Not a staff account.")
    if not active:
        if user.pk == actor.pk:
            raise ServiceError("self_lockout", "You cannot deactivate yourself.")
        if user.is_superadmin and not _other_active_admins(user):
            raise ServiceError("last_admin", "Cannot deactivate the last admin.")
        if user.is_superadmin and not actor.is_superadmin:
            raise ServiceError("forbidden", "Only an admin can deactivate admins.",
                               status.HTTP_403_FORBIDDEN)
    user.is_active = active
    user.save(update_fields=["is_active", "updated_at"])
    if not active:
        revoke_all_tokens(user)
    rbac.invalidate(user)
    audit.log("staff.activated" if active else "staff.deactivated", actor=actor, request=request, obj=user)
    return user


@transaction.atomic
def set_user_roles(*, actor, user, role_ids, request=None):
    if user.user_type != UserType.STAFF:
        raise ServiceError("not_staff", "Roles apply to staff accounts only.")
    roles = list(Role.objects.filter(pk__in=role_ids))
    if len(roles) != len(set(role_ids)):
        raise ServiceError("invalid_role", "One or more roles do not exist.",
                           fields={"role_ids": ["Unknown role."]})
    if user.pk == actor.pk and not actor.is_superadmin:
        raise ServiceError("forbidden", "You cannot change your own roles.", status.HTTP_403_FORBIDDEN)
    _assert_can_grant(actor, _role_codes(roles))

    before = sorted(UserRole.objects.filter(user=user).values_list("role__name", flat=True))
    UserRole.objects.filter(user=user).exclude(role__in=roles).delete()
    for role in roles:
        UserRole.objects.get_or_create(user=user, role=role, defaults={"assigned_by": actor})
    rbac.invalidate(user)
    audit.log("staff.roles_changed", actor=actor, request=request, obj=user,
              before=before, after=sorted(r.name for r in roles))
    return user
