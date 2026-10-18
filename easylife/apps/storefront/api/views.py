"""Public, read-only catalog for customers. Nothing here ever exposes cost, stock counts or drafts."""

from decimal import Decimal, InvalidOperation

from django.db.models import Count, Exists, F, Max, Min, OuterRef, Prefetch, Q
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.i18n import LANGS, requested_lang
from apps.catalog.models import Brand, Category, PriceList, Product, ProductPrice, ProductStatus, ProductVariant
from apps.core.exceptions import ServiceError
from apps.currencies.models import Currency
from apps.currencies.services import get_base_currency
from apps.inventory.models import StockLevel
from apps.inventory.services import sellable_quantities

from ..models import ShopProfile
from . import serializers as s

ORDERINGS = {"newest": "-created_at", "name": "name", "price": "price_from", "-price": "-price_from"}


def _requested_currency(request):
    code = (request.query_params.get("currency") or "").upper()
    if not code:
        return get_base_currency()
    currency = Currency.objects.filter(code=code, is_active=True).first()
    if currency is None:
        raise ServiceError("invalid_currency", f"Unsupported currency: {code}.", fields={"currency": [code]})
    return currency


def _price_param(request, name):
    """`?min_price=` / `?max_price=`: a non-negative decimal, or None when absent or empty."""
    raw = (request.query_params.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        value = None
    if value is None or not value.is_finite() or value < 0:
        raise ServiceError("invalid_price", f"{name} must be a non-negative number.", fields={name: [raw]})
    return value


def _descendant_ids(category):
    ids, frontier = {category.pk}, [category.pk]
    while frontier:
        children = list(Category.objects.filter(parent_id__in=frontier, is_active=True).values_list("pk", flat=True))
        frontier = [c for c in children if c not in ids]
        ids.update(frontier)
    return ids


def _price_list(currency):
    return PriceList.objects.filter(currency=currency, is_default=True, is_active=True).first()


def _visible_products(price_list):
    """What customers may see: active + shown + live + at least one live variant priced in this list that has
    sellable stock (available > 0 in an active warehouse). A product that is in no warehouse - including one that
    does not track inventory, which can never hold stock - is not shown."""
    in_stock = StockLevel.objects.filter(variant=OuterRef("pk"), warehouse__is_active=True, on_hand__gt=F("reserved"))
    sellable_variants = ProductVariant.objects.filter(
        product=OuterRef("pk"), deleted_at__isnull=True, is_active=True, prices__price_list=price_list,
    ).filter(Exists(in_stock))
    return (Product.objects.filter(status=ProductStatus.ACTIVE, is_visible_on_store=True, deleted_at__isnull=True)
            .filter(Exists(sellable_variants)))


class CategoryViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = s.CategorySerializer
    queryset = Category.objects.filter(is_active=True).select_related("parent").order_by("sort_order", "name")
    pagination_class = None

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "lang": requested_lang(self.request)}


class BrandViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Brands that have at least one product customers can see (so a brand page is never empty)."""

    permission_classes = [AllowAny]
    authentication_classes = []
    pagination_class = None

    def list(self, request, *args, **kwargs):
        price_list = _price_list(_requested_currency(request))
        if price_list is None:
            return Response([])
        lang = requested_lang(request)
        fields = ["brand__slug", "brand__name", "brand__logo"] + [f"brand__name_{code}" for code in LANGS]
        rows = (_visible_products(price_list).filter(brand__is_active=True)
                .order_by().values(*fields).annotate(products_count=Count("pk", distinct=True)))

        def label(row):  # same fallback rule as everywhere else
            return (lang and row.get(f"brand__name_{lang}")) or row["brand__name"]

        def logo(row):
            return request.build_absolute_uri(Brand._meta.get_field("logo").storage.url(row["brand__logo"])) if row["brand__logo"] else None

        out = [{"name": label(r), "slug": r["brand__slug"], "logo": logo(r), "products_count": r["products_count"]} for r in rows]
        return Response(sorted(out, key=lambda b: b["name"].lower()))


class ProductViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [AllowAny]
    authentication_classes = []  # public: a stray/expired token must not break browsing
    lookup_field = "slug"
    lookup_value_regex = "[^/]+"

    def get_queryset(self):
        self.currency = _requested_currency(self.request)
        self.price_list = _price_list(self.currency)
        if self.price_list is None:
            return Product.objects.none()
        priced = ProductPrice.objects.filter(price_list=self.price_list).select_related("price_list__currency")
        variants = ProductVariant.objects.filter(deleted_at__isnull=True, is_active=True,
                                                 prices__price_list=self.price_list).distinct().order_by("id")
        qs = (
            _visible_products(self.price_list)
            .select_related("category", "brand")
            .prefetch_related(Prefetch("variants", queryset=variants.prefetch_related(Prefetch("prices", queryset=priced))),
                              "images")
            .annotate(price_from=Min("variants__prices__price", filter=Q(
                variants__prices__price_list=self.price_list, variants__deleted_at__isnull=True,
                variants__is_active=True)))
        )
        if self.action not in ("list", "price_range"):
            return qs
        p = self.request.query_params
        if p.get("category"):
            cat = Category.objects.filter(slug=p["category"], is_active=True).first()
            qs = qs.filter(category_id__in=_descendant_ids(cat)) if cat else qs.none()
        if p.get("brand"):
            qs = qs.filter(brand__slug=p["brand"].strip(), brand__is_active=True)
        if p.get("search"):
            t = p["search"].strip()
            match = Q()
            for field in ("name", "name_ar", "name_en", "name_he"):  # any language finds the product
                match |= Q(**{f"{field}__icontains": t}) | Q(**{f"brand__{field}__icontains": t})
            qs = qs.filter(match)
        if self.action == "price_range":  # the bounds of everything the filters leave, so the price filter itself is skipped
            return qs
        low, high = _price_param(self.request, "min_price"), _price_param(self.request, "max_price")
        if low is not None:
            qs = qs.filter(price_from__gte=low)
        if high is not None:
            qs = qs.filter(price_from__lte=high)
        return qs.order_by(ORDERINGS.get(p.get("ordering"), "-created_at"), "-id")

    def _context(self, products):
        variant_ids = [v.pk for p in products for v in p.variants.all()]
        return {
            "request": self.request, "currency": self.currency, "lang": requested_lang(self.request),
            "sellable": sellable_quantities(variant_ids),
        }

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)
        products = list(page) if page is not None else list(queryset)
        data = s.ProductListSerializer(products, many=True, context=self._context(products)).data
        return self.get_paginated_response(data) if page is not None else Response(data)

    @action(detail=False, methods=["get"], url_path="price-range")
    def price_range(self, request):
        """Cheapest and dearest `price_from` among the products the other filters leave: the ends of the price slider."""
        queryset = self.get_queryset()
        bounds = queryset.aggregate(low=Min("price_from"), high=Max("price_from")) if queryset.exists() else {}
        low, high = bounds.get("low"), bounds.get("high")
        def fmt(value):  # four decimals, like every other price in the API
            return None if value is None else str(Decimal(value).quantize(Decimal("0.0001")))

        return Response({"currency": self.currency.code, "min": fmt(low), "max": fmt(high)})

    def retrieve(self, request, *args, **kwargs):
        product = self.get_object()
        return Response(s.ProductDetailSerializer(product, context=self._context([product])).data)


class AboutView(APIView):
    """The About page content: story, contact, opening hours, location and social accounts."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response(s.public_profile(ShopProfile.load(), requested_lang(request)))
