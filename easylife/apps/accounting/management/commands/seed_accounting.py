from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounting import services
from apps.accounting.models import FiscalPeriod


class Command(BaseCommand):
    help = "Seed the default chart of accounts (ar/en/he), the default account mappings and the 12 monthly periods of a year."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, default=date.today().year)

    @transaction.atomic
    def handle(self, *args, year, **options):
        created = services.seed_chart()
        self.stdout.write(f"chart: {created} account(s) created")
        if FiscalPeriod.objects.filter(start_date__year=year).exists():
            self.stdout.write(f"periods {year}: already exist")
        else:
            services.generate_periods(actor=None, year=year)
            self.stdout.write(f"periods {year}: 12 created")
