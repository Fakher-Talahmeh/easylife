from decimal import Decimal

from rest_framework import serializers

from apps.accounting.models import TaxRate
from apps.catalog.models import ProductVariant
from apps.inventory.models import Warehouse
from apps.sales.models import DebtPayment, PaymentMethod, Sale, SaleLine, Shift

MONEY_IN = {"max_digits": 18, "decimal_places": 2}
QTY_IN = {"max_digits": 14, "decimal_places": 3}


class WarehouseRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = ["uuid", "code", "name"]


class ShiftSerializer(serializers.ModelSerializer):
    cashier = serializers.SerializerMethodField()
    warehouse = WarehouseRefSerializer(read_only=True)

    class Meta:
        model = Shift
        fields = ["uuid", "number", "status", "cashier", "warehouse", "opening_cash", "opened_at", "closed_at",
                  "counted_cash", "expected_cash", "difference", "close_note"]

    def get_cashier(self, obj):
        return {"uuid": str(obj.cashier.uuid), "full_name": obj.cashier.full_name}


class OpenShiftSerializer(serializers.Serializer):
    warehouse = serializers.SlugRelatedField(slug_field="uuid", queryset=Warehouse.objects.all())
    opening_cash = serializers.DecimalField(min_value=Decimal("0"), default=Decimal("0"), **MONEY_IN)


class CloseShiftSerializer(serializers.Serializer):
    counted_cash = serializers.DecimalField(min_value=Decimal("0"), **MONEY_IN)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class SaleLineInputSerializer(serializers.Serializer):
    variant = serializers.SlugRelatedField(slug_field="uuid", queryset=ProductVariant.objects.all())
    quantity = serializers.DecimalField(min_value=Decimal("0.001"), **QTY_IN)
    discount = serializers.DecimalField(min_value=Decimal("0"), default=Decimal("0"), **MONEY_IN)


class PaymentInputSerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=PaymentMethod.choices)
    amount = serializers.DecimalField(min_value=Decimal("0.01"), **MONEY_IN)
    reference = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")


class CreateSaleSerializer(serializers.Serializer):
    lines = SaleLineInputSerializer(many=True, allow_empty=False)
    # may be empty only on credit (the whole total goes on the customer's account)
    payments = PaymentInputSerializer(many=True, allow_empty=True)
    customer = serializers.UUIDField(required=False, allow_null=True)
    on_credit = serializers.BooleanField(required=False, default=False)
    tax_rate = serializers.SlugRelatedField(slug_field="uuid", queryset=TaxRate.objects.all(), required=False,
                                            allow_null=True)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class RefundSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class SaleLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleLine
        fields = ["line_no", "sku", "name", "quantity", "unit_price", "discount", "line_total"]


def debt_payments_of(sale):
    """Customer payments that settled this invoice (oldest first)."""
    when = serializers.DateTimeField()
    return [{"number": a.payment.number, "date": when.to_representation(a.payment.created_at), "method": a.payment.method,
             "amount": str(a.amount)} for a in sorted(sale.allocations.all(), key=lambda a: a.pk)]


class SaleSerializer(serializers.ModelSerializer):
    """Full sale: what the receipt prints. Costs are deliberately not exposed."""

    cashier = serializers.SerializerMethodField()
    shift = serializers.CharField(source="shift.number")
    warehouse = WarehouseRefSerializer(read_only=True)
    customer = serializers.SerializerMethodField()
    lines = SaleLineSerializer(many=True, read_only=True)
    payments = serializers.SerializerMethodField()
    tax = serializers.SerializerMethodField()
    refund = serializers.SerializerMethodField()
    journal_entry = serializers.SerializerMethodField()
    debt_payments = serializers.SerializerMethodField()

    class Meta:
        model = Sale
        fields = ["uuid", "number", "status", "created_at", "cashier", "shift", "warehouse", "customer", "lines",
                  "subtotal", "discount_total", "total", "tax", "net_total", "payments", "cash_tendered",
                  "change_due", "credit_amount", "balance_due", "debt_payments", "note", "refund", "journal_entry"]

    def get_debt_payments(self, obj):
        return debt_payments_of(obj)

    def get_cashier(self, obj):
        return obj.created_by.full_name if obj.created_by else ""

    def get_customer(self, obj):
        c = obj.customer
        return {"uuid": str(c.uuid), "full_name": c.full_name, "phone": c.phone} if c else None

    def get_payments(self, obj):
        return [{"method": p.method, "amount": str(p.amount), "reference": p.reference} for p in obj.payments.all()]

    def get_tax(self, obj):
        if obj.tax_rate is None:
            return None
        return {"name": obj.tax_name, "rate": str(obj.tax_rate), "amount": str(obj.tax_total)}

    def get_refund(self, obj):
        if not obj.refunded_at:
            return None
        return {"at": obj.refunded_at, "by": obj.refunded_by.full_name if obj.refunded_by else "",
                "reason": obj.refund_reason}

    def get_journal_entry(self, obj):
        return obj.entry.number if obj.entry_id else None


class SaleListSerializer(serializers.ModelSerializer):
    cashier = serializers.SerializerMethodField()
    shift = serializers.CharField(source="shift.number")
    items = serializers.SerializerMethodField()
    customer = serializers.SerializerMethodField()

    class Meta:
        model = Sale
        fields = ["uuid", "number", "status", "created_at", "cashier", "shift", "customer", "items", "total",
                  "credit_amount", "balance_due"]

    def get_customer(self, obj):
        return obj.customer.full_name if obj.customer_id else None

    def get_cashier(self, obj):
        return obj.created_by.full_name if obj.created_by else ""

    def get_items(self, obj):
        return sum(int(l.quantity) if l.quantity == int(l.quantity) else 1 for l in obj.lines.all())


# ------------------------------------------------------------------ customer debts


class DebtPaymentInputSerializer(serializers.Serializer):
    amount = serializers.DecimalField(min_value=Decimal("0.01"), **MONEY_IN)
    method = serializers.ChoiceField(choices=PaymentMethod.choices)
    reference = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class DebtPaymentSerializer(serializers.ModelSerializer):
    """A payment towards the customer's debt and the invoices it settled."""

    received_by = serializers.SerializerMethodField()
    invoices = serializers.SerializerMethodField()

    class Meta:
        model = DebtPayment
        fields = ["uuid", "number", "created_at", "amount", "method", "reference", "note", "received_by", "invoices"]

    def get_received_by(self, obj):
        return obj.created_by.full_name if obj.created_by else ""

    def get_invoices(self, obj):
        return [{"uuid": str(a.sale.uuid), "number": a.sale.number, "amount": str(a.amount)} for a in obj.allocations.all()]


class CustomerInvoiceListSerializer(serializers.ModelSerializer):
    """An invoice as its customer sees it in the list."""

    items = serializers.SerializerMethodField()

    class Meta:
        model = Sale
        fields = ["uuid", "number", "status", "created_at", "items", "total", "credit_amount", "balance_due"]

    def get_items(self, obj):
        return SaleListSerializer.get_items(self, obj)


class CustomerInvoiceSerializer(serializers.ModelSerializer):
    """One invoice for its customer: lines, what was paid at the till, the debt and how it was paid off."""

    lines = SaleLineSerializer(many=True, read_only=True)
    payments = serializers.SerializerMethodField()
    debt_payments = serializers.SerializerMethodField()
    tax = serializers.SerializerMethodField()

    class Meta:
        model = Sale
        fields = ["uuid", "number", "status", "created_at", "lines", "subtotal", "discount_total", "total", "tax",
                  "payments", "credit_amount", "balance_due", "debt_payments"]

    def get_payments(self, obj):
        return [{"method": p.method, "amount": str(p.amount)} for p in obj.payments.all()]

    def get_debt_payments(self, obj):
        return debt_payments_of(obj)

    def get_tax(self, obj):
        return SaleSerializer.get_tax(self, obj)
