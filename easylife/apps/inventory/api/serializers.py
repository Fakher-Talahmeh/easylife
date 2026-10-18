from decimal import Decimal

from rest_framework import serializers

from apps.accounts import rbac
from apps.catalog.models import ProductVariant
from apps.inventory.models import (
    AdjustmentReason, StockAdjustment, StockAdjustmentLine, StockLevel, StockMovement,
    StockTransfer, StockTransferLine, Warehouse,
)

QTY = {"max_digits": 14, "decimal_places": 3}
MONEY = {"max_digits": 18, "decimal_places": 4, "min_value": Decimal("0")}


def _can_see_cost(serializer):
    user = getattr(serializer.context.get("request"), "user", None)
    return bool(user and rbac.has_permission(user, "inventory.view_cost"))


class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = ["uuid", "code", "name", "address", "is_default", "is_active"]
        read_only_fields = ["uuid"]
        validators = []  # the "one default" rule is applied by the service (it demotes the old default)
        extra_kwargs = {"is_default": {"validators": []}}


class _WarehouseRef(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = ["uuid", "code", "name"]


class _VariantRef(serializers.ModelSerializer):
    product = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = ProductVariant
        fields = ["uuid", "sku", "name", "product"]


class StockLevelSerializer(serializers.ModelSerializer):
    variant = _VariantRef(read_only=True)
    warehouse = _WarehouseRef(read_only=True)
    available = serializers.DecimalField(read_only=True, **QTY)
    reorder_level = serializers.DecimalField(source="variant.reorder_level", read_only=True, **QTY)
    low_stock = serializers.SerializerMethodField()

    class Meta:
        model = StockLevel
        fields = ["variant", "warehouse", "on_hand", "reserved", "available", "reorder_level", "low_stock"]

    def get_low_stock(self, level):
        reorder = level.variant.reorder_level
        return reorder > 0 and level.available <= reorder


class CountSheetRowSerializer(serializers.ModelSerializer):
    """One variant of the count sheet with its quantities in the chosen warehouse (0 when it has no stock row)."""

    product = serializers.CharField(source="product.name", read_only=True)
    on_hand = serializers.DecimalField(read_only=True, **QTY)
    reserved = serializers.DecimalField(read_only=True, **QTY)
    available = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = ["uuid", "sku", "name", "product", "on_hand", "reserved", "available"]

    def get_available(self, variant):
        return f"{variant.on_hand - variant.reserved:.3f}"


class MovementSerializer(serializers.ModelSerializer):
    variant = _VariantRef(read_only=True)
    warehouse = _WarehouseRef(read_only=True)
    created_by = serializers.CharField(source="created_by.full_name", default=None, read_only=True)

    class Meta:
        model = StockMovement
        fields = ["uuid", "variant", "warehouse", "type", "quantity", "unit_cost", "balance_after",
                  "ref_type", "ref_id", "note", "created_by", "created_at"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not _can_see_cost(self):
            data["unit_cost"] = None
        return data


# ---------------------------------------------------------------- adjustments


class AdjustmentLineSerializer(serializers.ModelSerializer):
    variant = _VariantRef(read_only=True)

    class Meta:
        model = StockAdjustmentLine
        fields = ["variant", "counted_qty", "system_qty", "diff", "unit_cost"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not _can_see_cost(self):
            data["unit_cost"] = None
        return data


class AdjustmentSerializer(serializers.ModelSerializer):
    warehouse = _WarehouseRef(read_only=True)
    lines = AdjustmentLineSerializer(many=True, read_only=True)
    created_by = serializers.CharField(source="created_by.full_name", default=None, read_only=True)
    posted_by = serializers.CharField(source="posted_by.full_name", default=None, read_only=True)

    class Meta:
        model = StockAdjustment
        fields = ["uuid", "number", "warehouse", "reason", "note", "status", "lines",
                  "created_by", "created_at", "posted_by", "posted_at"]


class AdjustmentLineInput(serializers.Serializer):
    variant = serializers.SlugRelatedField(
        slug_field="uuid", queryset=ProductVariant.objects.filter(deleted_at__isnull=True).select_related("product"))
    counted_qty = serializers.DecimalField(min_value=Decimal("0"), **QTY)
    unit_cost = serializers.DecimalField(required=False, allow_null=True, **MONEY)


class AdjustmentCreateSerializer(serializers.Serializer):
    warehouse = serializers.SlugRelatedField(slug_field="uuid", queryset=Warehouse.objects.all())
    reason = serializers.ChoiceField(choices=AdjustmentReason.choices)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    lines = AdjustmentLineInput(many=True, allow_empty=False)
    post = serializers.BooleanField(required=False, default=False)


# ---------------------------------------------------------------- transfers


class TransferLineSerializer(serializers.ModelSerializer):
    variant = _VariantRef(read_only=True)

    class Meta:
        model = StockTransferLine
        fields = ["variant", "quantity"]


class TransferSerializer(serializers.ModelSerializer):
    from_warehouse = _WarehouseRef(read_only=True)
    to_warehouse = _WarehouseRef(read_only=True)
    lines = TransferLineSerializer(many=True, read_only=True)
    created_by = serializers.CharField(source="created_by.full_name", default=None, read_only=True)
    received_by = serializers.CharField(source="received_by.full_name", default=None, read_only=True)

    class Meta:
        model = StockTransfer
        fields = ["uuid", "number", "from_warehouse", "to_warehouse", "status", "note", "lines",
                  "created_by", "created_at", "received_by", "received_at"]


class TransferLineInput(serializers.Serializer):
    variant = serializers.SlugRelatedField(
        slug_field="uuid", queryset=ProductVariant.objects.filter(deleted_at__isnull=True).select_related("product"))
    quantity = serializers.DecimalField(min_value=Decimal("0.001"), **QTY)


class TransferCreateSerializer(serializers.Serializer):
    from_warehouse = serializers.SlugRelatedField(slug_field="uuid", queryset=Warehouse.objects.all())
    to_warehouse = serializers.SlugRelatedField(slug_field="uuid", queryset=Warehouse.objects.all())
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    lines = TransferLineInput(many=True, allow_empty=False)
