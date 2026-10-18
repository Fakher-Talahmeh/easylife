from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.core.models import TimeStampedModel, UUIDModel

QTY = {"max_digits": 14, "decimal_places": 3}
MONEY = {"max_digits": 18, "decimal_places": 4}


def _user_fk():
    return models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )


class Warehouse(UUIDModel, TimeStampedModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=120)
    address = models.CharField(max_length=255, blank=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["is_default"], condition=Q(is_default=True), name="one_default_warehouse"),
        ]

    def __str__(self):
        return self.code


class StockLevel(models.Model):
    """Current quantities of one variant in one warehouse. Changed only via inventory.services."""

    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="stock_levels")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="stock_levels")
    on_hand = models.DecimalField(default=Decimal("0"), **QTY)
    reserved = models.DecimalField(default=Decimal("0"), **QTY)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["variant", "warehouse"], name="uniq_level_per_variant_warehouse"),
            models.CheckConstraint(condition=Q(on_hand__gte=0), name="on_hand_non_negative"),
            models.CheckConstraint(condition=Q(reserved__gte=0), name="reserved_non_negative"),
            models.CheckConstraint(condition=Q(reserved__lte=F("on_hand")), name="reserved_within_on_hand"),
        ]

    @property
    def available(self):
        return self.on_hand - self.reserved


class MovementType(models.TextChoices):
    OPENING = "opening", "Opening balance"
    PURCHASE_IN = "purchase_in", "Purchase receipt"
    SALE_OUT = "sale_out", "Sale"
    RETURN_IN = "return_in", "Customer return"
    RETURN_OUT = "return_out", "Return to supplier"
    ADJUSTMENT = "adjustment", "Adjustment"
    TRANSFER_IN = "transfer_in", "Transfer in"
    TRANSFER_OUT = "transfer_out", "Transfer out"


class StockMovement(UUIDModel):
    """Append-only ledger of every quantity change."""

    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="movements")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="movements")
    type = models.CharField(max_length=16, choices=MovementType.choices)
    quantity = models.DecimalField(**QTY)  # signed: positive = in, negative = out
    unit_cost = models.DecimalField(**MONEY)  # base currency
    balance_after = models.DecimalField(**QTY)
    ref_type = models.CharField(max_length=32, blank=True)
    ref_id = models.CharField(max_length=64, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_by = _user_fk()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["variant", "warehouse", "created_at"], name="mv_variant_wh_time_idx"),
            models.Index(fields=["variant", "created_at"], name="mv_variant_time_idx"),  # one item's history
            models.Index(fields=["warehouse", "created_at"], name="mv_wh_time_idx"),
            models.Index(fields=["type", "created_at"], name="mv_type_time_idx"),
            models.Index(fields=["ref_type", "ref_id"], name="mv_ref_idx"),  # movements of an order / document
        ]
        constraints = [models.CheckConstraint(condition=~Q(quantity=0), name="movement_quantity_not_zero")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("StockMovement is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("StockMovement is append-only.")


class ReservationStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    FULFILLED = "fulfilled", "Fulfilled"
    RELEASED = "released", "Released"
    EXPIRED = "expired", "Expired"


class StockReservation(UUIDModel):
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="reservations")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="reservations")
    quantity = models.DecimalField(**QTY)
    status = models.CharField(max_length=10, choices=ReservationStatus.choices, default=ReservationStatus.ACTIVE)
    ref_type = models.CharField(max_length=32, blank=True)
    ref_id = models.CharField(max_length=64, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name="reservation_quantity_positive")]
        indexes = [
            # the periodic "release expired reservations" job only ever scans active ones
            models.Index(fields=["expires_at"], name="resv_active_expiry_idx", condition=Q(status="active")),
            models.Index(fields=["ref_type", "ref_id"], name="resv_ref_idx"),  # reservations of one order
        ]


class AdjustmentReason(models.TextChoices):
    COUNT = "count", "Stock count"
    DAMAGE = "damage", "Damage"
    LOSS = "loss", "Loss / theft"
    FOUND = "found", "Found"
    OPENING = "opening", "Opening balance"
    OTHER = "other", "Other"


class StockAdjustment(UUIDModel, TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"

    number = models.CharField(max_length=32, unique=True)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="adjustments")
    reason = models.CharField(max_length=10, choices=AdjustmentReason.choices)
    note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    created_by = _user_fk()
    posted_by = _user_fk()
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"], name="adj_status_created_idx")]


class StockAdjustmentLine(models.Model):
    adjustment = models.ForeignKey(StockAdjustment, on_delete=models.CASCADE, related_name="lines")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    counted_qty = models.DecimalField(**QTY)
    system_qty = models.DecimalField(null=True, blank=True, **QTY)  # filled when posted
    diff = models.DecimalField(null=True, blank=True, **QTY)
    unit_cost = models.DecimalField(null=True, blank=True, **MONEY)  # for increases; base currency

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["adjustment", "variant"], name="uniq_variant_per_adjustment"),
            models.CheckConstraint(condition=Q(counted_qty__gte=0), name="counted_non_negative"),
        ]


class StockTransfer(UUIDModel, TimeStampedModel):
    class Status(models.TextChoices):
        IN_TRANSIT = "in_transit", "In transit"
        RECEIVED = "received", "Received"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=32, unique=True)
    from_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="transfers_out")
    to_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="transfers_in")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.IN_TRANSIT)
    note = models.CharField(max_length=255, blank=True)
    created_by = _user_fk()
    received_by = _user_fk()
    received_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=~Q(from_warehouse=F("to_warehouse")), name="transfer_distinct_warehouses"),
        ]
        indexes = [models.Index(fields=["status", "-created_at"], name="trf_status_created_idx")]


class StockTransferLine(models.Model):
    transfer = models.ForeignKey(StockTransfer, on_delete=models.CASCADE, related_name="lines")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(**QTY)
    unit_cost = models.DecimalField(**MONEY)  # snapshot at dispatch (base currency)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["transfer", "variant"], name="uniq_variant_per_transfer"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="transfer_quantity_positive"),
        ]
