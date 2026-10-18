from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator, URLValidator
from django.db import models

from apps.core.models import TimeStampedModel, UUIDModel

# links shown to customers must be real web addresses: never javascript:, data:, file: ...
web_url = URLValidator(schemes=["http", "https"])


class ShopProfile(TimeStampedModel):
    """The shop's public face (About page, contact, location). One row only (pk=1), edited from the admin.

    Every text has a default (`about`, `address`, `hours`) plus optional Arabic / English / Hebrew versions; an empty
    translation falls back to the default, exactly like product names.
    """

    about = models.TextField(blank=True)
    about_ar = models.TextField(blank=True)
    about_en = models.TextField(blank=True)
    about_he = models.TextField(blank=True)
    address = models.CharField(max_length=300, blank=True)
    address_ar = models.CharField(max_length=300, blank=True)
    address_en = models.CharField(max_length=300, blank=True)
    address_he = models.CharField(max_length=300, blank=True)
    hours = models.CharField(max_length=200, blank=True)
    hours_ar = models.CharField(max_length=200, blank=True)
    hours_en = models.CharField(max_length=200, blank=True)
    hours_he = models.CharField(max_length=200, blank=True)

    # contact (empty = the storefront falls back to the deployment's configured number)
    phone = models.CharField(max_length=30, blank=True,
                             validators=[RegexValidator(r"^[0-9+\-\s()]*$", "Digits, spaces, + - ( ) only.")])
    whatsapp = models.CharField(max_length=15, blank=True,
                                validators=[RegexValidator(r"^\d{8,15}$", "International digits only, e.g. 972535746112.")])
    email = models.EmailField(blank=True)

    # where the shop is
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
                                   validators=[MinValueValidator(Decimal("-90")), MaxValueValidator(Decimal("90"))])
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
                                    validators=[MinValueValidator(Decimal("-180")), MaxValueValidator(Decimal("180"))])
    map_url = models.URLField(max_length=500, blank=True, validators=[web_url])

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(latitude__isnull=True) | models.Q(latitude__gte=-90, latitude__lte=90),
                                   name="shop_latitude_range"),
            models.CheckConstraint(condition=models.Q(longitude__isnull=True) | models.Q(longitude__gte=-180, longitude__lte=180),
                                   name="shop_longitude_range"),
        ]

    def __str__(self):
        return "Shop profile"

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]


class SocialPlatform(models.TextChoices):
    FACEBOOK = "facebook", "Facebook"
    INSTAGRAM = "instagram", "Instagram"
    WHATSAPP = "whatsapp", "WhatsApp"
    TIKTOK = "tiktok", "TikTok"
    YOUTUBE = "youtube", "YouTube"
    X = "x", "X"
    TELEGRAM = "telegram", "Telegram"
    SNAPCHAT = "snapchat", "Snapchat"
    LINKEDIN = "linkedin", "LinkedIn"
    WEBSITE = "website", "Website"
    OTHER = "other", "Other"


class SocialLink(UUIDModel, TimeStampedModel):
    profile = models.ForeignKey(ShopProfile, on_delete=models.CASCADE, related_name="social_links")
    platform = models.CharField(max_length=12, choices=SocialPlatform.choices)
    url = models.URLField(max_length=500, validators=[web_url])
    label = models.CharField(max_length=60, blank=True)  # e.g. the handle "@easylife"
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "id"]
