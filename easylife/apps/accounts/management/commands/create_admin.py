from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import User, UserType
from apps.accounts.whatsapp import normalize_phone


class Command(BaseCommand):
    help = "Create (or promote) the first admin. The admin signs in with a WhatsApp code like everyone else."

    def add_arguments(self, parser):
        parser.add_argument("--phone", required=True)
        parser.add_argument("--name", required=True)

    @transaction.atomic
    def handle(self, *args, phone, name, **options):
        normalized = normalize_phone(phone)
        if not normalized:
            raise CommandError("Invalid phone number.")
        user = User.objects.filter(phone=normalized).first()
        if user is None:
            user = User.objects.create_superadmin(normalized, full_name=name)
            self.stdout.write(self.style.SUCCESS(f"Admin created for {normalized}."))
        else:
            user.user_type = UserType.STAFF
            user.is_superadmin = True
            user.is_active = True
            user.save(update_fields=["user_type", "is_superadmin", "is_active", "updated_at"])
            self.stdout.write(self.style.SUCCESS(f"{normalized} promoted to admin."))
