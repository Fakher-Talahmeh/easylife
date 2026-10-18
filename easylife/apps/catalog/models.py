from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from django.db.models import Q
from django.utils.text import slugify

from apps.core.models import TimeStampedModel, UUIDModel


class Category(UUIDModel, TimeStampedModel):
    # `name` is the default (and the fallback); the optional per-language names win when the storefront asks for that language
    name = models.CharField(max_length=120)
    name_ar = models.CharField(max_length=120, blank=True)
    name_en = models.CharField(max_length=120, blank=True)
    name_he = models.CharField(max_length=120, blank=True)
    slug = models.SlugField(max_length=140, unique=True, allow_unicode=True)
    # the category's picture, shown in its card on the storefront (POST /admin/categories/{uuid}/image/)
    image = models.ImageField(upload_to="categories/", null=True, blank=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Brand(UUIDModel, TimeStampedModel):
    name = models.CharField(max_length=120)
    name_ar = models.CharField(max_length=120, blank=True)
    name_en = models.CharField(max_length=120, blank=True)
    name_he = models.CharField(max_length=120, blank=True)
    # Stable address of the brand page (the same in every language); made once, so renaming never breaks links.
    slug = models.SlugField(max_length=140, unique=True, allow_unicode=True, blank=True)
    # the brand's picture, shown on the storefront brands page (uploaded through POST /admin/brands/{uuid}/logo/)
    logo = models.ImageField(upload_to="brands/", null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(Lower("name"), name="brand_name_ci_unique")]

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name, allow_unicode=True)[:60] or "brand"
            slug, n = base, 2
            while Brand.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ProductStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class Product(UUIDModel, TimeStampedModel):
    name = models.CharField(max_length=200)
    name_ar = models.CharField(max_length=200, blank=True)
    name_en = models.CharField(max_length=200, blank=True)
    name_he = models.CharField(max_length=200, blank=True)
    slug = models.SlugField(max_length=220, unique=True, allow_unicode=True)
    description = models.TextField(blank=True)
    description_ar = models.TextField(blank=True)
    description_en = models.TextField(blank=True)
    description_he = models.TextField(blank=True)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="products")
    brand = models.ForeignKey(Brand, null=True, blank=True, on_delete=models.SET_NULL, related_name="products")
    status = models.CharField(max_length=10, choices=ProductStatus.choices, default=ProductStatus.DRAFT, db_index=True)
    # Customers see a product only when status=active AND this flag is true.
    is_visible_on_store = models.BooleanField(default=False)
    track_inventory = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # storefront listing: only visible products, newest first (tiny index, no hidden/draft rows)
            models.Index(
                fields=["-created_at"], name="product_store_list_idx",
                condition=Q(status="active", is_visible_on_store=True, deleted_at__isnull=True),
            ),
            # admin listing: every live product, newest first
            models.Index(fields=["-created_at"], name="product_live_list_idx", condition=Q(deleted_at__isnull=True)),
        ]

    def __str__(self):
        return self.name


class ProductVariant(UUIDModel, TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    sku = models.CharField(max_length=64, unique=True)  # never reused, even after soft delete
    barcode = models.CharField(max_length=64, blank=True)
    name = models.CharField(max_length=200, blank=True)  # e.g. "Blue / L"
    attributes = models.JSONField(default=dict, blank=True)
    # Weighted-average cost in the BASE currency. Maintained by inventory, never set by hand.
    cost_price = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal("0"))
    weight = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    reorder_level = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["barcode"], condition=~Q(barcode=""), name="uniq_variant_barcode"),
            models.CheckConstraint(condition=Q(cost_price__gte=0), name="variant_cost_non_negative"),
            models.CheckConstraint(condition=Q(reorder_level__gte=0), name="variant_reorder_non_negative"),
        ]

    def __str__(self):
        return self.sku


class ProductImage(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    variant = models.ForeignKey(ProductVariant, null=True, blank=True, on_delete=models.SET_NULL, related_name="images")
    image = models.ImageField(upload_to="products/%Y/%m/")
    alt = models.CharField(max_length=200, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_primary = models.BooleanField(default=False)

    class Meta:
        ordering = ["sort_order", "id"]


class PriceList(UUIDModel, TimeStampedModel):
    name = models.CharField(max_length=120, unique=True)
    currency = models.ForeignKey("currencies.Currency", on_delete=models.PROTECT, related_name="price_lists")
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            # one default price list per currency: this is what the storefront reads
            models.UniqueConstraint(fields=["currency"], condition=Q(is_default=True), name="one_default_price_list_per_currency"),
        ]

    def __str__(self):
        return self.name


class ProductPrice(TimeStampedModel):
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="prices")
    price_list = models.ForeignKey(PriceList, on_delete=models.CASCADE, related_name="prices")
    price = models.DecimalField(max_digits=18, decimal_places=4)
    compare_at_price = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["variant", "price_list"], name="uniq_price_per_list"),
            models.CheckConstraint(condition=Q(price__gte=0), name="price_non_negative"),
        ]
