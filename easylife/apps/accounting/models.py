from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import TimeStampedModel, UUIDModel


def _user_fk():
    return models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )


class AccountType(models.TextChoices):
    ASSET = "asset", "Asset"
    LIABILITY = "liability", "Liability"
    EQUITY = "equity", "Equity"
    REVENUE = "revenue", "Revenue"
    EXPENSE = "expense", "Expense"


DEBIT_NORMAL = {AccountType.ASSET, AccountType.EXPENSE}


class Account(UUIDModel, TimeStampedModel):
    code = models.CharField(max_length=20, unique=True)
    name_ar = models.CharField(max_length=150)
    name_en = models.CharField(max_length=150)
    name_he = models.CharField(max_length=150)
    type = models.CharField(max_length=10, choices=AccountType.choices, db_index=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    is_postable = models.BooleanField(default=True, help_text="Only leaf accounts can receive journal lines.")
    currency = models.ForeignKey(
        "currencies.Currency", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Restrict the account to one currency; empty = any.",
    )
    is_system = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name_en}"

    @property
    def debit_normal(self):
        return self.type in DEBIT_NORMAL

    def name_for(self, lang="ar"):
        return getattr(self, f"name_{lang}", None) or self.name_ar


class PeriodStatus(models.TextChoices):
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"


class FiscalPeriod(UUIDModel, TimeStampedModel):
    name = models.CharField(max_length=7, unique=True)  # YYYY-MM
    start_date = models.DateField(unique=True)
    end_date = models.DateField(unique=True)
    status = models.CharField(max_length=6, choices=PeriodStatus.choices, default=PeriodStatus.OPEN, db_index=True)
    closed_by = _user_fk()
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["start_date"]
        constraints = [models.CheckConstraint(condition=Q(end_date__gte=models.F("start_date")), name="period_end_after_start")]

    def __str__(self):
        return self.name


class EntryStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    POSTED = "posted", "Posted"
    REVERSED = "reversed", "Reversed"


class EntryKind(models.TextChoices):
    JOURNAL = "journal", "Journal"
    OPENING = "opening", "Opening balances"
    RECEIPT = "receipt", "Receipt voucher"
    PAYMENT = "payment", "Payment voucher"


# Only these fields may still change on an entry after it has been posted (reversal bookkeeping).
_POSTED_MUTABLE = {"status", "reversed_at", "reverse_reason", "updated_at"}


class JournalEntry(UUIDModel, TimeStampedModel):
    number = models.CharField(max_length=32, unique=True)
    date = models.DateField(db_index=True)
    period = models.ForeignKey(FiscalPeriod, on_delete=models.PROTECT, related_name="entries")
    memo = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=10, choices=EntryKind.choices, default=EntryKind.JOURNAL)
    status = models.CharField(max_length=10, choices=EntryStatus.choices, default=EntryStatus.DRAFT, db_index=True)
    source_type = models.CharField(max_length=32, blank=True)  # "manual" entries leave it blank
    source_id = models.CharField(max_length=64, blank=True)
    reversal_of = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by_entry")
    reverse_reason = models.CharField(max_length=255, blank=True)
    created_by = _user_fk()
    posted_by = _user_fk()
    posted_at = models.DateTimeField(null=True, blank=True)
    reversed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["status", "-date"], name="je_status_date_idx"),
            models.Index(fields=["source_type", "source_id"], name="je_source_idx"),
        ]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if not self._state.adding:
            old = type(self).objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if old in (EntryStatus.POSTED, EntryStatus.REVERSED):
                fields = kwargs.get("update_fields")
                if fields is None or not set(fields) <= _POSTED_MUTABLE:
                    raise PermissionError("A posted journal entry is immutable; reverse it instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != EntryStatus.DRAFT:
            raise PermissionError("Only draft entries can be deleted.")
        return super().delete(*args, **kwargs)


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveSmallIntegerField(default=1)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    description = models.CharField(max_length=255, blank=True)
    # original currency amount + the rate used; debit/credit are always in the BASE currency
    currency = models.ForeignKey("currencies.Currency", on_delete=models.PROTECT, related_name="+")
    amount_currency = models.DecimalField(max_digits=18, decimal_places=4)
    rate = models.DecimalField(max_digits=18, decimal_places=8)
    debit = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    credit = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    party_type = models.CharField(max_length=32, blank=True)
    party_id = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["entry_id", "line_no"]
        indexes = [models.Index(fields=["account", "entry"], name="jl_account_entry_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(debit__gte=0) & Q(credit__gte=0), name="line_amounts_non_negative"),
            # exactly one side carries the amount
            models.CheckConstraint(
                condition=(Q(debit__gt=0) & Q(credit=0)) | (Q(debit=0) & Q(credit__gt=0)),
                name="line_single_side",
            ),
            models.UniqueConstraint(fields=["entry", "line_no"], name="uniq_line_no_per_entry"),
        ]

    def save(self, *args, **kwargs):
        if self.entry.status != EntryStatus.DRAFT:
            raise PermissionError("Lines of a posted entry are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.entry.status != EntryStatus.DRAFT:
            raise PermissionError("Lines of a posted entry are immutable.")
        return super().delete(*args, **kwargs)


class TaxRate(UUIDModel, TimeStampedModel):
    name = models.CharField(max_length=80, unique=True)
    rate = models.DecimalField(max_digits=7, decimal_places=4)  # percent, e.g. 16.0000
    collected_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")  # output tax (sales)
    paid_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")  # input tax (purchases)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(rate__gte=0) & Q(rate__lte=100), name="tax_rate_percent")]


class MappingKey(models.TextChoices):
    SALES = "sales", "Sales revenue"
    COGS = "cogs", "Cost of goods sold"
    INVENTORY = "inventory", "Inventory"
    RECEIVABLE = "receivable", "Accounts receivable"
    PAYABLE = "payable", "Accounts payable"
    CASH = "cash", "Cash"
    BANK = "bank", "Bank"
    TAX_COLLECTED = "tax_collected", "Tax collected (output)"
    TAX_PAID = "tax_paid", "Tax paid (input)"
    INVENTORY_ADJUSTMENT = "inventory_adjustment", "Inventory differences"
    FX_GAIN = "fx_gain", "FX gain"
    FX_LOSS = "fx_loss", "FX loss"


# Expected account type per mapping key (validated when saving the mapping).
MAPPING_TYPES = {
    "sales": AccountType.REVENUE, "cogs": AccountType.EXPENSE, "inventory": AccountType.ASSET,
    "receivable": AccountType.ASSET, "payable": AccountType.LIABILITY, "cash": AccountType.ASSET,
    "bank": AccountType.ASSET, "tax_collected": AccountType.LIABILITY, "tax_paid": AccountType.ASSET,
    "inventory_adjustment": AccountType.EXPENSE, "fx_gain": AccountType.REVENUE, "fx_loss": AccountType.EXPENSE,
}


class AccountMapping(models.Model):
    key = models.CharField(max_length=24, choices=MappingKey.choices, unique=True)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)
