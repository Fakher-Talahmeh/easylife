"""Effective-permission calculation and the DRF permission class.

effective = role permissions + user allow overrides - user deny overrides.
A superadmin bypasses everything. Customers never hold permissions.
"""

from django.core.cache import cache
from django.utils import timezone
from rest_framework.permissions import BasePermission

from .models import Permission, UserPermissionOverride, UserType

CACHE_TTL = 300


def _key(user_id):
    return f"perms:{user_id}"


def invalidate(user):
    cache.delete(_key(user.pk))


def all_codes():
    return set(Permission.objects.values_list("code", flat=True))


def effective_permissions(user):
    if not user.is_active or user.user_type != UserType.STAFF:
        return set()
    if user.is_superadmin:
        return all_codes()

    cached = cache.get(_key(user.pk))
    if cached is not None:
        return set(cached)

    granted = set(
        Permission.objects.filter(roles__user_roles__user=user).values_list("code", flat=True)
    )
    now = timezone.now()
    overrides = UserPermissionOverride.objects.filter(user=user).select_related("permission")
    for o in overrides:
        if o.expires_at and o.expires_at <= now:
            continue
        if o.effect == UserPermissionOverride.Effect.ALLOW:
            granted.add(o.permission.code)
    for o in overrides:
        if o.effect == UserPermissionOverride.Effect.DENY and not (o.expires_at and o.expires_at <= now):
            granted.discard(o.permission.code)

    cache.set(_key(user.pk), sorted(granted), CACHE_TTL)
    return granted


def has_permission(user, code):
    return code in effective_permissions(user)


class HasPermission(BasePermission):
    """Views declare `required_permission = "module.action"` (str) or a per-action dict
    `required_permission = {"list": "users.view", "create": "users.create", ...}`.
    A tuple/list value means "any of these permissions".
    A view with no declaration is denied (fail closed)."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and user.is_active):
            return False
        if user.user_type != UserType.STAFF:
            return False
        required = getattr(view, "required_permission", None)
        if isinstance(required, dict):
            action = getattr(view, "action", None)
            # "action:METHOD" keys let one routed action (e.g. GET+POST variants) need different permissions.
            required = required.get(f"{action}:{request.method}") or required.get(action)
        if not required:
            return False
        if isinstance(required, (tuple, list)):  # any-of
            return any(has_permission(user, code) for code in required)
        return has_permission(user, required)
