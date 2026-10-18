from rest_framework import serializers

from apps.currencies.models import Currency, ExchangeRate


class CurrencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Currency
        fields = ["code", "name", "symbol", "decimals", "is_base", "is_active"]
        read_only_fields = ["is_base"]

    def validate_code(self, value):
        value = value.strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise serializers.ValidationError("Use a 3-letter ISO 4217 code, e.g. ILS.")
        return value

    def validate_decimals(self, value):
        if not 0 <= value <= 4:
            raise serializers.ValidationError("Decimals must be between 0 and 4.")
        return value

    def validate(self, attrs):
        # The code is the identifier; it can't change after creation.
        if self.instance is not None and "code" in attrs and attrs["code"] != self.instance.code:
            raise serializers.ValidationError({"code": "The code cannot be changed."})
        if self.instance is not None and self.instance.is_base and attrs.get("is_active") is False:
            raise serializers.ValidationError({"is_active": "The base currency cannot be deactivated."})
        return attrs


class ExchangeRateSerializer(serializers.ModelSerializer):
    currency = serializers.SlugRelatedField(slug_field="code", queryset=Currency.objects.filter(is_active=True))

    class Meta:
        model = ExchangeRate
        fields = ["id", "currency", "date", "rate_to_base"]
        read_only_fields = ["id"]
        validators = []  # uniqueness is handled as an upsert in the service

    def validate_rate_to_base(self, value):
        if value <= 0:
            raise serializers.ValidationError("The rate must be greater than zero.")
        return value
