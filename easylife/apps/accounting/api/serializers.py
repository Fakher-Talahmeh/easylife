from decimal import Decimal

from rest_framework import serializers

from apps.accounting.models import (
    MAPPING_TYPES, Account, AccountType, EntryKind, FiscalPeriod, JournalEntry, JournalLine, TaxRate,
)
from apps.accounting.reports import m
from apps.currencies.models import Currency
from apps.currencies.services import quantize

MONEY = {"max_digits": 18, "decimal_places": 4}


def _name(user):
    return getattr(user, "full_name", None) or None


# ---------------------------------------------------------------- accounts


class AccountSerializer(serializers.ModelSerializer):
    parent = serializers.SlugRelatedField(slug_field="uuid", read_only=True)
    currency = serializers.SlugRelatedField(slug_field="code", read_only=True)
    has_entries = serializers.SerializerMethodField()
    balance = serializers.SerializerMethodField()

    class Meta:
        model = Account
        fields = ["uuid", "code", "name_ar", "name_en", "name_he", "type", "parent", "is_postable",
                  "currency", "is_system", "is_active", "has_entries", "balance"]

    def get_has_entries(self, account):
        return bool(getattr(account, "has_entries_flag", False))

    def get_balance(self, account):
        balances = self.context.get("balances")
        return None if balances is None else balances.get(account.pk, m(0))


class AccountWriteSerializer(serializers.ModelSerializer):
    parent = serializers.SlugRelatedField(slug_field="uuid", queryset=Account.objects.all(), allow_null=True, required=False)
    currency = serializers.SlugRelatedField(slug_field="code", queryset=Currency.objects.filter(is_active=True), allow_null=True, required=False)
    code = serializers.RegexField(r"^[0-9A-Za-z.\-]{1,20}$", validators=[])

    class Meta:
        model = Account
        fields = ["code", "name_ar", "name_en", "name_he", "type", "parent", "is_postable", "currency", "is_active"]
        extra_kwargs = {"code": {"validators": []}}


# ---------------------------------------------------------------- periods, tax, mappings


class FiscalPeriodSerializer(serializers.ModelSerializer):
    posted_entries = serializers.IntegerField(read_only=True, default=0)
    draft_entries = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = FiscalPeriod
        fields = ["uuid", "name", "start_date", "end_date", "status", "posted_entries", "draft_entries", "closed_at"]


class GenerateSerializer(serializers.Serializer):
    year = serializers.IntegerField(min_value=2000, max_value=2100)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, allow_blank=True, default="")


class TaxRateSerializer(serializers.ModelSerializer):
    collected_account = serializers.SlugRelatedField(slug_field="uuid", queryset=Account.objects.all())
    paid_account = serializers.SlugRelatedField(slug_field="uuid", queryset=Account.objects.all())
    rate = serializers.DecimalField(max_digits=7, decimal_places=4, min_value=Decimal("0"), max_value=Decimal("100"))

    class Meta:
        model = TaxRate
        fields = ["uuid", "name", "rate", "collected_account", "paid_account", "is_active"]
        read_only_fields = ["uuid"]


class MappingWriteSerializer(serializers.Serializer):
    mappings = serializers.DictField(child=serializers.UUIDField(), allow_empty=False)

    def validate_mappings(self, value):
        unknown = sorted(set(value) - set(MAPPING_TYPES))
        if unknown:
            raise serializers.ValidationError(f"Unknown keys: {', '.join(unknown)}")
        return value


# ---------------------------------------------------------------- journal


class LineInputSerializer(serializers.Serializer):
    account = serializers.SlugRelatedField(slug_field="uuid", queryset=Account.objects.select_related("currency"))
    description = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    currency = serializers.SlugRelatedField(slug_field="code", queryset=Currency.objects.all(), required=False, allow_null=True)
    side = serializers.ChoiceField(choices=["debit", "credit"])
    amount = serializers.DecimalField(**MONEY)
    rate = serializers.DecimalField(max_digits=18, decimal_places=8, required=False, allow_null=True)


class EntryWriteSerializer(serializers.Serializer):
    date = serializers.DateField()
    memo = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    kind = serializers.ChoiceField(choices=EntryKind.choices, required=False, default=EntryKind.JOURNAL)
    lines = LineInputSerializer(many=True, required=False, default=list)


class EntryPatchSerializer(serializers.Serializer):
    date = serializers.DateField(required=False)
    memo = serializers.CharField(max_length=255, required=False, allow_blank=True)
    lines = LineInputSerializer(many=True, required=False)


class ReverseSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)
    date = serializers.DateField(required=False)


class _AccountRef(serializers.ModelSerializer):
    class Meta:
        model = Account
        fields = ["uuid", "code", "name_ar", "name_en", "name_he"]


class LineOutSerializer(serializers.ModelSerializer):
    account = _AccountRef(read_only=True)
    currency = serializers.CharField(source="currency.code", read_only=True)

    class Meta:
        model = JournalLine
        fields = ["line_no", "account", "description", "currency", "amount_currency", "rate", "debit", "credit"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["debit"], data["credit"] = m(instance.debit), m(instance.credit)
        data["amount_currency"] = format(quantize(instance.amount_currency, instance.currency), "f")  # in the currency's own decimals
        data["rate"] = format(instance.rate, "f")
        return data


class EntryListSerializer(serializers.ModelSerializer):
    period = serializers.CharField(source="period.name", read_only=True)
    created_by = serializers.SerializerMethodField()
    source = serializers.SerializerMethodField()
    total = serializers.SerializerMethodField()

    class Meta:
        model = JournalEntry
        fields = ["uuid", "number", "date", "period", "memo", "kind", "status", "source", "total", "created_by"]

    def get_created_by(self, e):
        return _name(e.created_by)

    def get_source(self, e):
        return {"type": e.source_type or "manual", "id": e.source_id or None}

    def get_total(self, e):
        return m(getattr(e, "total_debit", None) or 0)


class EntryDetailSerializer(EntryListSerializer):
    lines = LineOutSerializer(many=True, read_only=True)
    posted_by = serializers.SerializerMethodField()
    reversal_of = serializers.SerializerMethodField()
    reversed_by = serializers.SerializerMethodField()
    totals = serializers.SerializerMethodField()

    class Meta(EntryListSerializer.Meta):
        fields = EntryListSerializer.Meta.fields + [
            "posted_by", "posted_at", "reversal_of", "reversed_by", "reversed_at", "reverse_reason", "totals", "lines"]

    def get_total(self, e):  # the detail query carries no annotation: sum the lines directly
        return m(sum((ln.debit for ln in e.lines.all()), Decimal(0)))

    def get_posted_by(self, e):
        return _name(e.posted_by)

    def get_reversal_of(self, e):
        o = e.reversal_of
        return {"uuid": str(o.uuid), "number": o.number} if o else None

    def get_reversed_by(self, e):
        r = getattr(e, "reversed_by_entry", None)
        return {"uuid": str(r.uuid), "number": r.number} if r else None

    def get_totals(self, e):
        lines = list(e.lines.all())
        d = sum((ln.debit for ln in lines), Decimal(0))
        c = sum((ln.credit for ln in lines), Decimal(0))
        return {"debit": m(d), "credit": m(c), "difference": m(d - c), "balanced": d == c and len(lines) >= 2}
