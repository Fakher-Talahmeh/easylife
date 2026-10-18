"""Point-of-sale (physical shop): cash shifts, sales and their payments. Fields and constraints only.

All amounts are in the base currency (ILS). A sale is complete the moment it is saved: its stock movements and
its journal entry are created in the same transaction (see services.create_sale).

Selling on credit (دين): part or all of a sale can be left unpaid on a registered customer's account
(`credit_amount`, booked to accounts receivable). `balance_due` is what is still owed on that invoice; customer
payments (`DebtPayment`) reduce it, oldest invoice first (`DebtAllocation`).
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.core.models import TimeStampedModel, UUIDModel

MONEY = {"max_digits": 18, "decimal_places": 2}
QTY = {"max_digits": 14, "decimal_places": 3}
ZERO = Decimal("0")


def _user_fk(null=True):
    return models.ForeignKey(
        settings.AUTH_USER_MODEL, null=null, blank=null, on_delete=models.SET_NULL if null else models.PROTECT,
        related_name="+",
    )


class ShiftStatus(models.TextChoices):
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"


class Shift(UUIDModel, TimeStampedModel):
    """A cashier's till session. Selling requires an open shift; closing it reconciles the cash."""

    number = models.CharField(max_length=32, unique=True)
    cashier = _user_fk(null=False)
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=10, choices=ShiftStatus.choices, default=ShiftStatus.OPEN, db_index=True)
    opening_cash = models.DecimalField(**MONEY)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = _user_fk()
    counted_cash = models.DecimalField(null=True, blank=True, **MONEY)
    expected_cash = models.DecimalField(null=True, blank=True, **MONEY)
    difference = models.DecimalField(null=True, blank=True, **MONEY)  # counted - expected
    close_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-opened_at"]
        constraints = [
            models.UniqueConstraint(fields=["cashier"], condition=Q(status="open"), name="one_open_shift_per_cashier"),
            models.CheckConstraint(condition=Q(opening_cash__gte=0), name="shift_opening_cash_gte0"),
        ]

    def __str__(self):
        return self.number


class SaleStatus(models.TextChoices):
    COMPLETED = "completed", "Completed"
    REFUNDED = "refunded", "Refunded"


class PaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    CARD = "card", "Card"
    TRANSFER = "transfer", "Bank transfer"


class Sale(UUIDModel, TimeStampedModel):
    number = models.CharField(max_length=32, unique=True)
    shift = models.ForeignKey(Shift, on_delete=models.PROTECT, related_name="sales")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+")
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="purchases")
    status = models.CharField(max_length=10, choices=SaleStatus.choices, default=SaleStatus.COMPLETED, db_index=True)

    subtotal = models.DecimalField(**MONEY)  # before discounts, tax included
    discount_total = models.DecimalField(default=ZERO, **MONEY)
    total = models.DecimalField(**MONEY)  # subtotal - discount_total: what the customer pays
    tax_name = models.CharField(max_length=80, blank=True)
    tax_rate = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)  # percent, prices include it
    tax_total = models.DecimalField(default=ZERO, **MONEY)
    net_total = models.DecimalField(**MONEY)  # total - tax_total (the sales revenue)
    cost_total = models.DecimalField(default=ZERO, **MONEY)  # cost of goods sold at the time of sale

    cash_tendered = models.DecimalField(default=ZERO, **MONEY)
    change_due = models.DecimalField(default=ZERO, **MONEY)
    # on the customer's account: left unpaid at the till, and what of it is still owed today
    credit_amount = models.DecimalField(default=ZERO, **MONEY)
    balance_due = models.DecimalField(default=ZERO, **MONEY)
    note = models.CharField(max_length=255, blank=True)

    created_by = _user_fk()
    entry = models.OneToOneField(
        "accounting.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    refunded_at = models.DateTimeField(null=True, blank=True)
    refunded_by = _user_fk()
    refund_reason = models.CharField(max_length=255, blank=True)
    refund_shift = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.PROTECT, related_name="refunds")
    refund_entry = models.OneToOneField(
        "accounting.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["shift", "status"], name="sale_shift_status_idx"),
            models.Index(fields=["status", "-created_at"], name="sale_status_time_idx"),
            # a customer's open invoices (their debt), oldest first
            models.Index(fields=["customer", "created_at"], name="sale_customer_open_idx", condition=Q(balance_due__gt=0)),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(total__gt=0), name="sale_total_gt0"),
            models.CheckConstraint(condition=Q(credit_amount__gte=0) & Q(credit_amount__lte=F("total")),
                                   name="sale_credit_bounds"),
            models.CheckConstraint(condition=Q(balance_due__gte=0) & Q(balance_due__lte=F("credit_amount")),
                                   name="sale_balance_bounds"),
            # only a known customer can owe the shop money
            models.CheckConstraint(condition=Q(credit_amount=0) | Q(customer__isnull=False), name="sale_credit_needs_customer"),
            models.CheckConstraint(condition=Q(discount_total__gte=0) & Q(discount_total__lte=F("subtotal")),
                                   name="sale_discount_bounds"),
        ]

    def __str__(self):
        return self.number


class SaleLine(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField()
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    sku = models.CharField(max_length=64)  # snapshots: the receipt must not change if the catalog does
    name = models.CharField(max_length=255)
    quantity = models.DecimalField(**QTY)
    unit_price = models.DecimalField(**MONEY)
    discount = models.DecimalField(default=ZERO, **MONEY)  # amount off this line
    line_total = models.DecimalField(**MONEY)  # quantity * unit_price - discount
    unit_cost = models.DecimalField(max_digits=18, decimal_places=4, default=ZERO)
    tracked = models.BooleanField(default=True)  # False: no stock movement was made

    class Meta:
        ordering = ["line_no"]
        constraints = [
            models.UniqueConstraint(fields=["sale", "line_no"], name="saleline_unique_no"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="saleline_qty_gt0"),
            models.CheckConstraint(condition=Q(unit_price__gte=0) & Q(discount__gte=0), name="saleline_money_gte0"),
        ]


class Payment(models.Model):
    """What was applied to the sale (change given back is NOT part of `amount`)."""

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=10, choices=PaymentMethod.choices)
    amount = models.DecimalField(**MONEY)
    reference = models.CharField(max_length=64, blank=True)  # card slip / transfer number

    class Meta:
        ordering = ["id"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_gt0")]


class DebtPayment(UUIDModel, TimeStampedModel):
    """Money a customer paid towards what they owe (سند قبض). Cash taken at a till belongs to that shift."""

    number = models.CharField(max_length=32, unique=True)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="debt_payments")
    amount = models.DecimalField(**MONEY)
    method = models.CharField(max_length=10, choices=PaymentMethod.choices)
    reference = models.CharField(max_length=64, blank=True)
    note = models.CharField(max_length=255, blank=True)
    shift = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.PROTECT, related_name="debt_payments")
    created_by = _user_fk()
    entry = models.OneToOneField(
        "accounting.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="debt_payment_amount_gt0")]

    def __str__(self):
        return self.number


class DebtAllocation(models.Model):
    """Which invoice a payment settled, and by how much."""

    payment = models.ForeignKey(DebtPayment, on_delete=models.CASCADE, related_name="allocations")
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="debt_allocation_amount_gt0"),
            models.UniqueConstraint(fields=["payment", "sale"], name="debt_allocation_unique"),
        ]
