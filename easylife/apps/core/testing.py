"""Shared helpers for API tests (not collected by pytest: the module name has no test_ prefix)."""

from itertools import count

from django.core.cache import cache
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, User, UserPermissionOverride
from apps.accounts.services import issue_tokens

_phones = count(1)


def new_phone():
    return "97058%07d" % next(_phones)


def bootstrap():
    cache.clear()
    call_command("sync_permissions")
    call_command("seed_currencies", verbosity=0)


def client_for(user):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION="Bearer " + issue_tokens(user)["access"])
    return c


def make_admin():
    return User.objects.create_superadmin(new_phone(), full_name="Admin")


def make_customer():
    return User.objects.create_customer(new_phone(), full_name="Customer")


def make_staff(perms=(), roles=()):
    u = User.objects.create_staff(new_phone(), full_name="Staff")
    for r in roles:
        u.user_roles.create(role=Role.objects.get(name=r))
    for code in perms:
        UserPermissionOverride.objects.create(
            user=u, permission=Permission.objects.get(code=code), effect="allow"
        )
    return u
