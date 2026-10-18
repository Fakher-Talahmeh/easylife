from datetime import date as date_cls
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from apps.core import audit
from apps.core.exceptions import ServiceError

from .models import Currency, ExchangeRate


def get_base_currency():
    base = Currency.objects.filter(is_base=True).first()
    if base is None:
        raise ServiceError("no_base_currency", "No base currency is configured. Run seed_currencies.")
    return base


def get_rate(currency, on_date=None):
    """Latest rate on or before `on_date` (base currency is always 1)."""
    if currency.is_base:
        return Decimal(1)
    on_date = on_date or date_cls.today()
    rate = (
        ExchangeRate.objects.filter(currency=currency, date__lte=on_date).order_by("-date").first()
    )
    if rate is None:
        raise ServiceError(
            "rate_missing", f"No exchange rate for {currency.code} on or before {on_date}.",
            fields={"currency": [currency.code]},
        )
    return rate.rate_to_base


def quantize(amount, currency):
    return Decimal(amount).quantize(Decimal(1).scaleb(-currency.decimals), rounding=ROUND_HALF_UP)


def convert(amount, from_currency, to_currency, on_date=None):
    """Convert through the base currency using the latest rates, rounded to the target decimals."""
    if from_currency.pk == to_currency.pk:
        return quantize(amount, to_currency)
    in_base = Decimal(amount) * get_rate(from_currency, on_date)
    return quantize(in_base / get_rate(to_currency, on_date), to_currency)


@transaction.atomic
def set_exchange_rate(*, actor, currency, date, rate_to_base, request=None):
    if currency.is_base:
        raise ServiceError("base_currency_rate", "The base currency always has rate 1.")
    obj, created = ExchangeRate.objects.update_or_create(
        currency=currency, date=date, defaults={"rate_to_base": rate_to_base, "created_by": actor}
    )
    audit.log("currency.rate_set", actor=actor, request=request, obj=obj,
              currency=currency.code, date=str(date), rate=str(rate_to_base), created=created)
    return obj, created
