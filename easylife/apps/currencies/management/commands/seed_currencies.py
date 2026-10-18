from django.core.management.base import BaseCommand
from django.db import transaction

from apps.currencies.models import Currency

# Books are kept in ILS; USD and JOD are transaction currencies. Exchange rates are NOT seeded:
# an admin enters real rates through the API.
DEFAULTS = [
    {"code": "ILS", "name": "Israeli New Shekel", "symbol": "\u20aa", "decimals": 2, "is_base": True},
    {"code": "USD", "name": "US Dollar", "symbol": "$", "decimals": 2, "is_base": False},
    {"code": "JOD", "name": "Jordanian Dinar", "symbol": "JD", "decimals": 3, "is_base": False},
]


class Command(BaseCommand):
    help = "Create the default currencies (ILS base, USD, JOD). Safe to re-run; never changes the base."

    @transaction.atomic
    def handle(self, *args, **options):
        has_base = Currency.objects.filter(is_base=True).exists()
        for row in DEFAULTS:
            row = dict(row)
            code = row.pop("code")
            if has_base:
                row["is_base"] = False
            obj, created = Currency.objects.get_or_create(code=code, defaults=row)
            self.stdout.write(f"{'created' if created else 'exists '} {obj.code}{' (base)' if obj.is_base else ''}")
