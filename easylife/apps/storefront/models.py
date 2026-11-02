from decimal import Decimal
import uuid
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator, URLValidator
from django.db import models

from apps.core.models import TimeStampedModel, UUIDModel
from PIL import Image, ImageOps

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


class StoreAd(UUIDModel, TimeStampedModel):
    image = models.ImageField(upload_to="store/ads/")
    description = models.TextField(blank=True)
    description_ar = models.TextField(blank=True)
    description_en = models.TextField(blank=True)
    description_he = models.TextField(blank=True)
    button_text = models.CharField(max_length=80, blank=True)
    button_text_ar = models.CharField(max_length=80, blank=True)
    button_text_en = models.CharField(max_length=80, blank=True)
    button_text_he = models.CharField(max_length=80, blank=True)
    button_url = models.URLField(max_length=500, blank=True, validators=[web_url])
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["sort_order", "-created_at"]
        constraints = [models.CheckConstraint(condition=models.Q(ends_at__isnull=True) | models.Q(starts_at__isnull=True) | models.Q(ends_at__gt=models.F("starts_at")), name="store_ad_end_after_start")]

    def save(self, *args, **kwargs):
        if self.image and not self.image.name.lower().endswith(".webp"):
            self.image.file.seek(0)
            source = ImageOps.exif_transpose(Image.open(self.image.file))
            has_alpha = source.mode in ("RGBA", "LA") or (source.mode == "P" and "transparency" in source.info)
            converted = source.convert("RGBA" if has_alpha else "RGB")
            output = BytesIO()
            converted.save(output, format="WEBP", quality=86, method=6)
            filename = self.image.name.rsplit("/", 1)[-1].rsplit(".", 1)[0] + ".webp"
            self.image.save(filename, ContentFile(output.getvalue()), save=False)
        super().save(*args, **kwargs)


class DeliveryArea(UUIDModel, TimeStampedModel):
    """A destination available at checkout, with its fee frozen into each order."""
    name = models.CharField(max_length=120, unique=True)
    fee = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.CheckConstraint(condition=models.Q(fee__gte=0), name="delivery_area_fee_gte0")]


class OnlineOrder(UUIDModel, TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending confirmation"
        CONFIRMED = "confirmed", "Confirmed"
        PREPARING = "preparing", "Being prepared"
        READY_FOR_SHIPPING = "ready_for_shipping", "Ready for shipping"
        OUT_FOR_DELIVERY = "out_for_delivery", "Out for delivery"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"
        DELIVERY_FAILED = "delivery_failed", "Delivery failed"
        RETURNED = "returned", "Returned"

    number = models.CharField(max_length=32, unique=True)
    confirmation_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    stock_deducted = models.BooleanField(default=False)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="online_orders")
    customer_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=30)
    city = models.CharField(max_length=120)
    address = models.CharField(max_length=300)
    area = models.ForeignKey(DeliveryArea, null=True, on_delete=models.PROTECT, related_name="orders")
    lines = models.JSONField(default=list)
    subtotal = models.DecimalField(max_digits=18, decimal_places=2)
    discount_total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    delivery_fee = models.DecimalField(max_digits=18, decimal_places=2)
    total = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        ordering = ["-created_at"]


class Favorite(UUIDModel, TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="store_favorites")
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="store_favorites")

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["user", "product"], name="unique_store_favorite")]
