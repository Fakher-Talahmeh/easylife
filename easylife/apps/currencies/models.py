from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import TimeStampedModel


class Currency(TimeStampedModel):
    code = models.CharField(max_length=3, unique=True)  # ISO 4217, upper case
    name = models.CharField(max_length=64)
    symbol = models.CharField(max_length=8)
    decimals = models.PositiveSmallIntegerField(default=2)
    is_base = models.BooleanField(default=False, help_text="The currency the books are kept in.")
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "currencies"
        constraints = [
            models.UniqueConstraint(fields=["is_base"], condition=Q(is_base=True), name="one_base_currency"),
        ]

    def __str__(self):
        return self.code


class ExchangeRate(TimeStampedModel):
    """How many units of the base currency one unit of `currency` is worth on `date`."""

    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="rates")
    date = models.DateField()
    rate_to_base = models.DecimalField(max_digits=18, decimal_places=8)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(fields=["currency", "date"], name="uniq_rate_per_currency_day"),
            models.CheckConstraint(condition=Q(rate_to_base__gt=0), name="rate_positive"),
        ]
