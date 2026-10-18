from decimal import Decimal

from rest_framework import serializers

from apps.accounts import rbac
from apps.catalog.models import Brand, Category, PriceList, Product, ProductImage, ProductPrice, ProductVariant
from apps.currencies.models import Currency

MONEY = {"max_digits": 18, "decimal_places": 4, "min_value": Decimal("0")}


class CategorySerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()

    def get_image(self, category):
        if not category.image:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(category.image.url) if request else category.image.url

    parent = serializers.SlugRelatedField(
        slug_field="uuid", queryset=Category.objects.all(), allow_null=True, required=False
    )

    class Meta:
        model = Category
        fields = ["uuid", "name", "name_ar", "name_en", "name_he", "slug", "image", "parent", "sort_order", "is_active"]
        read_only_fields = ["uuid", "slug", "image"]


class BrandSerializer(serializers.ModelSerializer):
    products_count = serializers.IntegerField(read_only=True, default=0)
    logo = serializers.SerializerMethodField()

    def get_logo(self, brand):
        if not brand.logo:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(brand.logo.url) if request else brand.logo.url

    class Meta:
        model = Brand
        fields = ["uuid", "name", "name_ar", "name_en", "name_he", "slug", "logo", "is_active", "products_count"]
        read_only_fields = ["uuid", "slug", "logo"]
        validators = []  # case-insensitive uniqueness is applied by the service (brand_taken)
        extra_kwargs = {"name": {"max_length": 120}}


class _BrandRef(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = ["uuid", "name"]


class PriceListSerializer(serializers.ModelSerializer):
    currency = serializers.SlugRelatedField(slug_field="code", queryset=Currency.objects.filter(is_active=True))

    class Meta:
        model = PriceList
        fields = ["uuid", "name", "currency", "is_default", "is_active"]
        read_only_fields = ["uuid"]
        validators = []  # "one default per currency" is applied by the service


class PriceInputSerializer(serializers.Serializer):
    price_list = serializers.SlugRelatedField(slug_field="uuid", queryset=PriceList.objects.all())
    price = serializers.DecimalField(**MONEY)
    compare_at_price = serializers.DecimalField(required=False, allow_null=True, **MONEY)


class SetPricesSerializer(serializers.Serializer):
    prices = PriceInputSerializer(many=True)


class PriceSerializer(serializers.ModelSerializer):
    price_list = serializers.SlugRelatedField(slug_field="uuid", read_only=True)
    price_list_name = serializers.CharField(source="price_list.name", read_only=True)
    currency = serializers.CharField(source="price_list.currency.code", read_only=True)

    class Meta:
        model = ProductPrice
        fields = ["price_list", "price_list_name", "currency", "price", "compare_at_price"]


class VariantWriteSerializer(serializers.ModelSerializer):
    prices = PriceInputSerializer(many=True, required=False, write_only=True)
    barcode = serializers.CharField(max_length=64, required=False, allow_blank=True)
    attributes = serializers.DictField(required=False)
    cost_price = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=Decimal("0"), required=False)

    class Meta:
        model = ProductVariant
        fields = ["sku", "barcode", "name", "attributes", "weight", "reorder_level", "is_active", "cost_price", "prices"]
        extra_kwargs = {"sku": {"validators": []}}  # uniqueness handled in the service (case-insensitive)


class VariantSerializer(serializers.ModelSerializer):
    prices = PriceSerializer(many=True, read_only=True)
    stock = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = ["uuid", "sku", "barcode", "name", "attributes", "weight", "reorder_level",
                  "is_active", "cost_price", "prices", "stock"]

    def get_stock(self, variant):
        from apps.inventory.services import totals_for

        stock_map = self.context.get("stock_map") or totals_for([variant.pk])
        t = stock_map.get(variant.pk) or {"on_hand": 0, "reserved": 0, "available": 0}
        return {k: str(Decimal(v).quantize(Decimal("0.001"))) for k, v in t.items()}

    def to_representation(self, instance):
        data = super().to_representation(instance)
        user = getattr(self.context.get("request"), "user", None)
        # Purchase cost is confidential: only holders of inventory.view_cost see it.
        if not (user and rbac.has_permission(user, "inventory.view_cost")):
            data["cost_price"] = None
        return data


class ImageSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()
    variant = serializers.SlugRelatedField(slug_field="uuid", read_only=True)

    class Meta:
        model = ProductImage
        fields = ["id", "url", "alt", "sort_order", "is_primary", "variant"]

    def get_url(self, image):
        request = self.context.get("request")
        url = image.image.url
        return request.build_absolute_uri(url) if request else url


class SingleImageSerializer(serializers.Serializer):
    image = serializers.ImageField()


class ImageUploadSerializer(serializers.Serializer):
    image = serializers.ImageField()
    alt = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    is_primary = serializers.BooleanField(required=False, default=False)
    variant = serializers.SlugRelatedField(
        slug_field="uuid", queryset=ProductVariant.objects.filter(deleted_at__isnull=True),
        required=False, allow_null=True,
    )


class _CategoryRef(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["uuid", "name"]


class ProductListSerializer(serializers.ModelSerializer):
    category = _CategoryRef(read_only=True)
    brand = _BrandRef(read_only=True)
    variants_count = serializers.SerializerMethodField()
    available = serializers.SerializerMethodField()
    primary_image = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ["uuid", "name", "slug", "category", "brand", "status", "is_visible_on_store",
                  "track_inventory", "variants_count", "available", "primary_image", "created_at"]

    def get_variants_count(self, p):
        return sum(1 for v in p.variants.all() if v.deleted_at is None)

    def get_available(self, p):
        if not p.track_inventory:
            return None
        value = getattr(p, "available", None)
        return str(Decimal(value if value is not None else 0).quantize(Decimal("0.001")))

    def get_primary_image(self, p):
        img = next((i for i in p.images.all() if i.is_primary), None) or next(iter(p.images.all()), None)
        if img is None:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(img.image.url) if request else img.image.url


class ProductDetailSerializer(ProductListSerializer):
    variants = serializers.SerializerMethodField()
    images = ImageSerializer(many=True, read_only=True)

    class Meta(ProductListSerializer.Meta):
        fields = ProductListSerializer.Meta.fields + [
            "name_ar", "name_en", "name_he", "description", "description_ar", "description_en", "description_he",
            "variants", "images"]

    def get_variants(self, p):
        from apps.inventory.services import totals_for

        live = [v for v in p.variants.all() if v.deleted_at is None]
        ctx = {**self.context, "stock_map": totals_for([v.pk for v in live])}
        return VariantSerializer(live, many=True, context=ctx).data


class ProductWriteSerializer(serializers.ModelSerializer):
    category = serializers.SlugRelatedField(
        slug_field="uuid", queryset=Category.objects.all(), allow_null=True, required=False
    )
    brand = serializers.SlugRelatedField(
        slug_field="uuid", queryset=Brand.objects.all(), allow_null=True, required=False
    )
    variants = VariantWriteSerializer(many=True, required=False, write_only=True)

    class Meta:
        model = Product
        fields = ["name", "name_ar", "name_en", "name_he", "description", "description_ar", "description_en",
                  "description_he", "category", "brand", "track_inventory", "variants"]
        extra_kwargs = {"name": {"max_length": 200}}


class ProductImportSerializer(serializers.Serializer):
    file = serializers.FileField()
    dry_run = serializers.BooleanField(required=False, default=False)


class BulkSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=200)
    action = serializers.ChoiceField(choices=["show", "hide", "archive"])
