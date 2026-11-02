import re
from decimal import Decimal

from rest_framework import serializers

from apps.catalog.i18n import translated
from apps.catalog.models import Category, Offer, Product, ProductImage, ProductVariant

from ..models import DeliveryArea, OnlineOrder, ShopProfile, SocialLink, StoreAd, web_url


class CategorySerializer(serializers.ModelSerializer):
    parent = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    name = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ["name", "slug", "image", "parent"]

    def get_image(self, category):
        return self.context["request"].build_absolute_uri(category.image.url) if category.image else None

    def get_name(self, category):
        return translated(category, "name", self.context.get("lang"))


class StoreAdSerializer(serializers.ModelSerializer):
    image = serializers.ImageField()

    class Meta:
        model = StoreAd
        fields = ["uuid", "image", "description", "description_ar", "description_en", "description_he", "button_text", "button_text_ar", "button_text_en", "button_text_he", "button_url", "sort_order", "is_active", "starts_at", "ends_at", "created_at"]
        read_only_fields = ["uuid", "created_at"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if request and instance.image:
            data["image"] = request.build_absolute_uri(instance.image.url)
        lang = request.query_params.get("lang") if request else None
        if lang in {"ar", "en", "he"}:
            data["description"] = translated(instance, "description", lang)
            data["button_text"] = translated(instance, "button_text", lang)
        return data


class _CategoryRef(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ["name", "slug"]

    def get_name(self, category):
        return translated(category, "name", self.context.get("lang"))


class ImageSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ["url", "alt", "is_primary"]

    def get_url(self, image):
        return self.context["request"].build_absolute_uri(image.image.url)


def _sellable(context, variant):
    return context["sellable"].get(variant.pk, 0)


class StoreVariantSerializer(serializers.ModelSerializer):
    price = serializers.SerializerMethodField()
    compare_at_price = serializers.SerializerMethodField()
    in_stock = serializers.SerializerMethodField()
    available_quantity = serializers.SerializerMethodField()
    offer_types = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = ["uuid", "sku", "name", "attributes", "price", "compare_at_price", "in_stock", "available_quantity", "offer_types"]

    def _price(self, variant):
        return variant.prices.all()[0]  # prefetched: only the requested currency's default list

    def get_price(self, variant):
        return str(self._price(variant).price)

    def get_compare_at_price(self, variant):
        value = self._price(variant).compare_at_price
        return None if value is None else str(value)

    def get_in_stock(self, variant):
        if not variant.product.track_inventory:
            return True
        return _sellable(self.context, variant) > 0

    def get_available_quantity(self, variant):
        if not variant.product.track_inventory:
            return None
        return str(_sellable(self.context, variant))

    def get_offer_types(self, variant):
        return self.context.get("offer_types", {}).get(variant.pk, [])


class ProductListSerializer(serializers.ModelSerializer):
    category = _CategoryRef(read_only=True)
    name = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()
    price_from = serializers.SerializerMethodField()
    in_stock = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()
    offer_types = serializers.SerializerMethodField()
    default_variant = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ["uuid", "name", "slug", "category", "currency", "price_from", "in_stock", "image", "offer_types", "default_variant"]

    def get_name(self, product):
        return translated(product, "name", self.context.get("lang"))

    def get_currency(self, product):
        return self.context["currency"].code

    def get_price_from(self, product):
        return str(Decimal(product.price_from).quantize(Decimal("0.0001")))

    def get_in_stock(self, product):
        if not product.track_inventory:
            return True
        return any(_sellable(self.context, v) > 0 for v in product.variants.all())

    def get_image(self, product):
        images = list(product.images.all())
        img = next((i for i in images if i.is_primary), None) or (images[0] if images else None)
        return self.context["request"].build_absolute_uri(img.image.url) if img else None

    def get_offer_types(self, product):
        return list(dict.fromkeys(
            kind for variant in product.variants.all()
            for kind in self.context.get("offer_types", {}).get(variant.pk, [])
        ))

    def get_default_variant(self, product):
        variants = list(product.variants.all())
        if len(variants) != 1:
            return None
        variants[0].product = product
        return StoreVariantSerializer(variants[0], context=self.context).data


class ProductDetailSerializer(ProductListSerializer):
    description = serializers.SerializerMethodField()
    variants = serializers.SerializerMethodField()
    images = ImageSerializer(many=True, read_only=True)

    class Meta(ProductListSerializer.Meta):
        fields = ProductListSerializer.Meta.fields + ["description", "variants", "images"]

    def get_description(self, product):
        return translated(product, "description", self.context.get("lang"))

    def get_variants(self, product):
        for v in product.variants.all():
            v.product = product  # avoid a query per variant for track_inventory
        return StoreVariantSerializer(product.variants.all(), many=True, context=self.context).data


class StoreOfferSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    currency = serializers.CharField(source="price_list.currency.code", read_only=True)
    items = serializers.SerializerMethodField()
    regular_price = serializers.SerializerMethodField()
    savings = serializers.SerializerMethodField()

    class Meta:
        model = Offer
        fields = ["uuid", "name", "type", "fixed_price", "currency", "items", "regular_price", "savings"]

    def get_name(self, offer):
        return translated(offer, "name", self.context.get("lang"))

    def get_items(self, offer):
        request = self.context["request"]
        rows = []
        for item in offer.items.all():
            product = item.variant.product
            images = list(product.images.all())
            image = next((entry for entry in images if entry.is_primary), None) or (images[0] if images else None)
            price = item.variant.prices.all()[0].price if item.variant.prices.all() else None
            rows.append({
                "variant": str(item.variant.uuid), "sku": item.variant.sku,
                "product": translated(product, "name", self.context.get("lang")), "slug": product.slug,
                "variant_name": item.variant.name, "quantity": item.quantity,
                "image": request.build_absolute_uri(image.image.url) if image else None,
                "price": None if price is None else str(price),
            })
        return rows

    def get_regular_price(self, offer):
        total = sum(
            (item.variant.prices.all()[0].price * item.quantity for item in offer.items.all() if item.variant.prices.all()),
            Decimal("0"),
        )
        return str(total)

    def get_savings(self, offer):
        total = Decimal(self.get_regular_price(offer))
        return str(max(Decimal("0"), total - offer.fixed_price))


# ------------------------------------------------------------------ shop profile (About page)
class SocialLinkSerializer(serializers.ModelSerializer):
    # DRF drops the model's URLValidator (it adds its own, which also allows ftp://): restate the http/https-only rule
    url = serializers.URLField(max_length=500, validators=[web_url])

    class Meta:
        model = SocialLink
        fields = ["platform", "url", "label", "sort_order", "is_active"]


class ShopProfileSerializer(serializers.ModelSerializer):
    """Admin view of the profile: every language of every text, and the social links as one list."""

    social_links = SocialLinkSerializer(many=True, required=False)
    # typed freely ("+972 53-574-6112"); validate_whatsapp reduces it to digits and then checks it
    whatsapp = serializers.CharField(max_length=40, required=False, allow_blank=True)
    map_url = serializers.URLField(max_length=500, required=False, allow_blank=True, validators=[web_url])

    class Meta:
        model = ShopProfile
        fields = ["about", "about_ar", "about_en", "about_he", "address", "address_ar", "address_en", "address_he",
                  "hours", "hours_ar", "hours_en", "hours_he", "phone", "whatsapp", "email",
                  "latitude", "longitude", "map_url", "social_links", "updated_at"]
        read_only_fields = ["updated_at"]

    def validate_whatsapp(self, value):
        digits = re.sub(r"\D", "", value or "")  # "+972 53-574-6112" -> "972535746112"
        if digits and not 8 <= len(digits) <= 15:
            raise serializers.ValidationError("Use the international number, 8 to 15 digits, e.g. 972535746112.")
        return digits

    def validate_social_links(self, links):
        if len(links) > 20:
            raise serializers.ValidationError("At most 20 links.")
        return links

    def validate(self, attrs):
        lat, lng = attrs.get("latitude", self.instance.latitude if self.instance else None), attrs.get(
            "longitude", self.instance.longitude if self.instance else None)
        if (lat is None) != (lng is None):
            raise serializers.ValidationError({"latitude": ["Give both latitude and longitude, or neither."]})
        return attrs


class PublicSocialLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = SocialLink
        fields = ["platform", "url", "label"]


def public_profile(profile, lang):
    """What customers see: the texts in their language (default as fallback), only the active social links."""
    def coord(value):
        return None if value is None else str(value)

    return {
        "about": translated(profile, "about", lang),
        "address": translated(profile, "address", lang),
        "hours": translated(profile, "hours", lang),
        "phone": profile.phone, "whatsapp": profile.whatsapp, "email": profile.email,
        "latitude": coord(profile.latitude), "longitude": coord(profile.longitude), "map_url": profile.map_url,
        "social_links": PublicSocialLinkSerializer(profile.social_links.filter(is_active=True), many=True).data,
    }


class DeliveryAreaSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryArea
        fields = ["uuid", "name", "fee", "is_active"]


class OnlineOrderCreateSerializer(serializers.Serializer):
    lines = serializers.ListField(child=serializers.DictField(), allow_empty=False)
    customer_name = serializers.CharField(max_length=120)
    phone = serializers.CharField(max_length=30)
    city = serializers.CharField(max_length=120)
    address = serializers.CharField(max_length=300)
    area = serializers.UUIDField()


class OnlineOrderSerializer(serializers.ModelSerializer):
    area_name = serializers.CharField(source="area.name", read_only=True)

    class Meta:
        model = OnlineOrder
        fields = ["uuid", "number", "confirmation_token", "status", "customer_name", "phone", "city", "address",
                  "area_name", "lines", "subtotal", "discount_total", "delivery_fee", "total", "created_at"]

    def validate_status(self, value):
        return value
