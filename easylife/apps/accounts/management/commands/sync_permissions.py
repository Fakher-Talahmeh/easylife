from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Permission, Role, RolePermission
from apps.accounts.permissions_catalog import ALL_CODES, ROLE_TEMPLATES


class Command(BaseCommand):
    help = "Upsert the permission catalog and create missing starter roles (never overwrites edited roles)."

    @transaction.atomic
    def handle(self, *args, **options):
        new_codes = set()
        for code in ALL_CODES:
            _, created = Permission.objects.get_or_create(code=code, defaults={"module": code.split(".")[0]})
            if created:
                new_codes.add(code)
        # Codes removed from the catalog are dropped so stale permissions can't linger.
        Permission.objects.exclude(code__in=ALL_CODES).delete()

        for name, template in ROLE_TEMPLATES.items():
            role, created = Role.objects.get_or_create(
                name=name, defaults={"description": template["description"], "is_system": True}
            )
            # A new role gets its whole template. An existing system role only gets permissions that did not exist
            # until now (nobody can have removed those on purpose), so edits made by admins are never undone.
            codes = template["permissions"] if created else (new_codes & set(template["permissions"]) if role.is_system else ())
            perms = Permission.objects.filter(code__in=codes)
            RolePermission.objects.bulk_create(
                (RolePermission(role=role, permission=p) for p in perms), ignore_conflicts=True)
        self.stdout.write(self.style.SUCCESS(f"{len(ALL_CODES)} permissions, {len(ROLE_TEMPLATES)} role templates synced."))
