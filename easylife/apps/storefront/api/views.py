"""Public, read-only catalog for customers. Nothing here ever exposes cost, stock counts or drafts."""

from decimal import Decimal, InvalidOperation

from django.db.models import DecimalField, Exists, F, Max, Min, OuterRef, Prefetch, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import serializers

from apps.catalog.i18n import LANGS, requested_lang
from apps.catalog.models import Category, Offer, PriceList, Product, ProductPrice, ProductStatus, ProductVariant
from apps.core.exceptions import ServiceError
from apps.currencies.models import Currency
from apps.currencies.services import get_base_currency
from apps.inventory.models import StockLevel
from apps.inventory.services import sellable_quantities
from apps.sales.models import SaleLine, SaleStatus

from ..models import DeliveryArea, Favorite, OnlineOrder, ShopProfile, StoreAd
from .. import services
from apps.accounts.whatsapp import WhatsAppDeliveryError, normalize_phone, send_text
from apps.core.numbering import next_number
from . import serializers as s

ORDERINGS = {
    "newest": "-created_at", "name": "name", "price": "price_from", "-price": "-price_from",
    "sales": "sold_quantity", "-sales": "-sold_quantity",
}


def _invoice_message(order, invoice_url):
    lines = [
        f"أهلًا {order.customer_name}، شكرًا لطلبك من Easy Life 🌿", "",
        f"🧾 فاتورة رقم: {order.number}", "تفاصيل الطلب:",
    ]
    lines.extend(f"• {line['name']} × {line['qty']} — {line['total']} ₪" for line in order.lines)
    lines.extend([
        "", f"المجموع الفرعي: {order.subtotal} ₪",
        *([f"خصم العروض: -{order.discount_total} ₪"] if order.discount_total > 0 else []),
        f"التوصيل ({order.area.name}): {order.delivery_fee} ₪",
        f"الإجمالي النهائي: {order.total} ₪", "", f"العنوان: {order.city}، {order.address}",
        f"رابط الفاتورة وتأكيد الطلب: {invoice_url}", "اضغط على الرابط ثم اضغط زر تأكيد الطلبية.",
    ])
    return "\n".join(lines)


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


def _active_offers(price_list):
    """Offers a shopper can actually use right now for the requested price list."""
    now = timezone.now()
    return Offer.objects.filter(
        price_list=price_list, is_active=True,
    ).filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now)).filter(
        Q(ends_at__isnull=True) | Q(ends_at__gte=now))


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
            .select_related("category")
            .prefetch_related(Prefetch("variants", queryset=variants.prefetch_related(Prefetch("prices", queryset=priced))),
                              "images")
            .annotate(price_from=Min("variants__prices__price", filter=Q(
                variants__prices__price_list=self.price_list, variants__deleted_at__isnull=True,
                variants__is_active=True)))
            .annotate(sold_quantity=Coalesce(
                Subquery(
                    SaleLine.objects.filter(
                        variant__product=OuterRef("pk"), sale__status=SaleStatus.COMPLETED,
                    ).values("variant__product").annotate(total=Sum("quantity")).values("total")[:1],
                    output_field=DecimalField(max_digits=18, decimal_places=3),
                ),
                Value(0),
                output_field=DecimalField(max_digits=18, decimal_places=3),
            ))
        )
        if self.action not in ("list", "price_range"):
            return qs
        p = self.request.query_params
        if p.get("category"):
            cat = Category.objects.filter(slug=p["category"], is_active=True).first()
            qs = qs.filter(category_id__in=_descendant_ids(cat)) if cat else qs.none()
        if p.get("search"):
            t = p["search"].strip()
            match = Q()
            for field in ("name", "name_ar", "name_en", "name_he"):  # any language finds the product
                match |= Q(**{f"{field}__icontains": t})
            qs = qs.filter(match)
        if p.get("sale") in ("1", "true", "yes"):
            qs = qs.filter(
                variants__deleted_at__isnull=True,
                variants__is_active=True,
                variants__prices__price_list=self.price_list,
                variants__prices__compare_at_price__gt=F("variants__prices__price"),
            ).distinct()
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
        offer_types = {}
        if variant_ids:
            for offer in _active_offers(self.price_list).prefetch_related("items"):
                for item in offer.items.all():
                    if item.variant_id in variant_ids:
                        offer_types.setdefault(item.variant_id, []).append(offer.type)
        return {
            "request": self.request, "currency": self.currency, "lang": requested_lang(self.request),
            "sellable": sellable_quantities(variant_ids), "offer_types": offer_types,
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


class StoreAdListView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        now = timezone.now()
        ads = StoreAd.objects.filter(is_active=True).filter(
            Q(starts_at__isnull=True) | Q(starts_at__lte=now),
            Q(ends_at__isnull=True) | Q(ends_at__gte=now),
        )
        return Response(s.StoreAdSerializer(ads, many=True, context={"request": request}).data)


class OfferViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Public offer rules used by the storefront cart to calculate fixed-price quantity offers and bundles."""

    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = s.StoreOfferSerializer
    pagination_class = None

    def get_queryset(self):
        currency = _requested_currency(self.request)
        price_list = _price_list(currency)
        if price_list is None:
            return Offer.objects.none()
        prices = ProductPrice.objects.filter(price_list=price_list)
        return _active_offers(price_list).select_related("price_list__currency").prefetch_related(
            "items__variant__product__images",
            Prefetch("items__variant__prices", queryset=prices),
        ).order_by("-created_at")

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "lang": requested_lang(self.request)}


class DeliveryAreaViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = s.DeliveryAreaSerializer
    pagination_class = None

    def get_queryset(self):
        return DeliveryArea.objects.filter(is_active=True).order_by("name")


class OnlineOrderCreateView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        payload = s.OnlineOrderCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        phone = normalize_phone(data["phone"])
        if not phone:
            raise serializers.ValidationError({"phone": ["Enter a valid phone number."]})
        area = DeliveryArea.objects.filter(uuid=data["area"], is_active=True).first()
        if not area:
            raise serializers.ValidationError({"area": ["This delivery area is unavailable."]})
        # Recalculate prices and offers on the server; browser prices are display-only.
        price_list = _price_list(get_base_currency())
        if price_list is None:
            raise serializers.ValidationError({"lines": ["The store has no active price list."]})
        lines, subtotal, quantities, prices = [], Decimal("0"), {}, {}
        for raw in data["lines"]:
            try:
                qty = int(raw["qty"])
                variant = ProductVariant.objects.select_related("product").prefetch_related("product__images").get(
                    uuid=raw["variant"], deleted_at__isnull=True, is_active=True)
                price = variant.prices.get(price_list=price_list).price
                if qty < 1: raise ValueError
                if variant.product.track_inventory and qty > sellable_quantities([variant.pk]).get(variant.pk, Decimal("0")):
                    raise serializers.ValidationError({"lines": [f"الكمية المطلوبة للمنتج {variant.sku} أكبر من الكمية المتوفرة."]})
            except (KeyError, TypeError, ValueError, InvalidOperation, ProductVariant.DoesNotExist, ProductPrice.DoesNotExist):
                raise serializers.ValidationError({"lines": ["Invalid cart line."]})
            total = price * qty
            subtotal += total
            quantities[variant.pk] = quantities.get(variant.pk, 0) + qty
            prices[variant.pk] = price
            product_image = next(iter(variant.product.images.all()), None)
            lines.append({"name": str(raw.get("name", ""))[:255], "sku": str(raw.get("sku", ""))[:64], "qty": qty,
                          "price": str(price), "total": str(total), "variant": str(variant.uuid),
                          "image": request.build_absolute_uri(product_image.image.url) if product_image else None})
        discount = Decimal("0")
        for offer in _active_offers(price_list).prefetch_related("items"):
            items = list(offer.items.all())
            sets = min((quantities.get(item.variant_id, 0) // item.quantity for item in items), default=0)
            if sets:
                regular = sum((prices[item.variant_id] * item.quantity for item in items), Decimal("0"))
                discount += max(Decimal("0"), regular - offer.fixed_price) * sets
        discount = min(discount, subtotal)
        order = OnlineOrder.objects.create(number=next_number("online_order", "WEB-"), customer_name=data["customer_name"].strip(),
            phone=phone, city=data["city"].strip(), address=data["address"].strip(), area=area, lines=lines,
            subtotal=subtotal, discount_total=discount, delivery_fee=area.fee, total=subtotal - discount + area.fee,
            customer=request.user if getattr(request.user, "is_authenticated", False) else None)
        return Response(s.OnlineOrderSerializer(order).data, status=201)


class OnlineOrderSendInvoiceView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, token):
        order = OnlineOrder.objects.select_related("area").get(confirmation_token=token)
        confirmation_url = serializers.URLField().run_validation(request.data.get("confirmation_url"))
        try:
            send_text(order.phone, _invoice_message(order, confirmation_url))
        except WhatsAppDeliveryError:
            return Response({"code": "invoice_delivery_failed", "detail": "تعذر إرسال الفاتورة عبر واتساب."}, status=502)
        return Response({"sent": True, "invoice_url": confirmation_url})


class OnlineOrderView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, token):
        return Response(s.OnlineOrderSerializer(OnlineOrder.objects.select_related("area").get(confirmation_token=token)).data)

    def post(self, request, token):
        order = OnlineOrder.objects.get(confirmation_token=token)
        if order.status == OnlineOrder.Status.PENDING:
            order.status = OnlineOrder.Status.CONFIRMED
            order.save(update_fields=["status", "updated_at"])
        return Response(s.OnlineOrderSerializer(order).data)


class MyOnlineOrdersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        orders = OnlineOrder.objects.filter(customer=request.user).select_related("area").order_by("-created_at")
        return Response(s.OnlineOrderSerializer(orders, many=True).data)


class MyFavoritesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        slugs = Favorite.objects.filter(user=request.user).values_list("product__slug", flat=True)
        return Response({"slugs": list(slugs)})


class FavoriteToggleView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, slug):
        product = Product.objects.filter(slug=slug, status=ProductStatus.ACTIVE, is_visible_on_store=True, deleted_at__isnull=True).first()
        if product is None:
            raise ServiceError("not_found", "Product not found.", status_code=404)
        favorite, created = Favorite.objects.get_or_create(user=request.user, product=product)
        if not created:
            favorite.delete()
        return Response({"slug": product.slug, "is_favorite": created})


class MyOnlineOrderCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, uuid):
        order = OnlineOrder.objects.filter(customer=request.user, uuid=uuid).first()
        if order is None:
            raise ServiceError("not_found", "Order not found.", status_code=404)
        if order.status not in (OnlineOrder.Status.PENDING, OnlineOrder.Status.CONFIRMED):
            raise ServiceError("cannot_cancel_order", "لا يمكن إلغاء الطلب بعد بدء تجهيزه.", status_code=409)
        order.status = OnlineOrder.Status.CANCELLED
        order.save(update_fields=["status", "updated_at"])
        services.notify_order_status(order)
        return Response(s.OnlineOrderSerializer(order).data)
