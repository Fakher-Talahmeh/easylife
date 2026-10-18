from decimal import Decimal

from django.db.models import Count, DecimalField, Exists, F, OuterRef, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from apps.accounts import rbac
from apps.accounts.rbac import HasPermission
from apps.catalog import import_products, services
from apps.catalog.import_template import build_template
from apps.catalog.models import Brand, Category, PriceList, Product, ProductImage, ProductVariant
from apps.core.exceptions import ServiceError

from . import serializers as s

PATCH_ONLY = ["get", "post", "patch", "delete", "head", "options"]


def _require_cost_permission(request, *payloads):
    """Entering a purchase cost is its own permission (the cost is confidential and drives profit reports)."""
    if any("cost_price" in p for p in payloads) and not rbac.has_permission(request.user, "products.manage_cost"):
        raise ServiceError("forbidden", "Missing products.manage_cost.", status.HTTP_403_FORBIDDEN)


class CategoryViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
    mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet,
):
    permission_classes = [HasPermission]
    serializer_class = s.CategorySerializer
    queryset = Category.objects.select_related("parent").order_by("sort_order", "name")
    lookup_field = "uuid"
    http_method_names = PATCH_ONLY
    required_permission = {
        "list": "categories.view", "retrieve": "categories.view", "create": "categories.manage",
        "partial_update": "categories.manage", "destroy": "categories.manage", "image": "categories.manage",
    }

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        obj = services.save_category(actor=request.user, data=ser.validated_data, request=request)
        return Response(self.get_serializer(obj).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        ser = self.get_serializer(self.get_object(), data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        obj = services.save_category(actor=request.user, instance=ser.instance, data=ser.validated_data, request=request)
        return Response(self.get_serializer(obj).data)

    def destroy(self, request, *args, **kwargs):
        services.delete_category(actor=request.user, category=self.get_object(), request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post", "delete"], parser_classes=[MultiPartParser, FormParser])
    def image(self, request, uuid=None):
        """POST uploads/replaces the category picture (multipart `image`); DELETE removes it."""
        category = self.get_object()
        if request.method == "DELETE":
            services.remove_category_image(actor=request.user, category=category, request=request)
            return Response(status=status.HTTP_204_NO_CONTENT)
        ser = s.SingleImageSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        services.set_category_image(actor=request.user, category=category, file=ser.validated_data["image"], request=request)
        return Response(self.get_serializer(category).data)


class BrandViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
    mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet,
):
    permission_classes = [HasPermission]
    serializer_class = s.BrandSerializer
    lookup_field = "uuid"
    http_method_names = PATCH_ONLY
    pagination_class = None
    required_permission = {
        "list": "brands.view", "retrieve": "brands.view", "create": "brands.manage",
        "partial_update": "brands.manage", "destroy": "brands.manage", "logo": "brands.manage",
    }

    def get_queryset(self):
        qs = Brand.objects.annotate(
            products_count=Count("products", filter=Q(products__deleted_at__isnull=True))).order_by("name")
        p = self.request.query_params
        if p.get("search"):
            qs = qs.filter(name__icontains=p["search"].strip())
        if p.get("active") not in (None, ""):
            qs = qs.filter(is_active=_truthy(p["active"]))
        return qs

    def _fresh(self, obj):
        return self.get_serializer(self.get_queryset().get(pk=obj.pk)).data

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        obj = services.save_brand(actor=request.user, data=ser.validated_data, request=request)
        return Response(self._fresh(obj), status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        ser = self.get_serializer(self.get_object(), data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        obj = services.save_brand(actor=request.user, instance=ser.instance, data=ser.validated_data, request=request)
        return Response(self._fresh(obj))

    def destroy(self, request, *args, **kwargs):
        services.delete_brand(actor=request.user, brand=self.get_object(), request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post", "delete"], parser_classes=[MultiPartParser, FormParser])
    def logo(self, request, uuid=None):
        """POST uploads/replaces the brand picture (multipart `image`); DELETE removes it."""
        brand = self.get_object()
        if request.method == "DELETE":
            services.remove_brand_logo(actor=request.user, brand=brand, request=request)
            return Response(status=status.HTTP_204_NO_CONTENT)
        ser = s.SingleImageSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        services.set_brand_logo(actor=request.user, brand=brand, file=ser.validated_data["image"], request=request)
        return Response(self._fresh(brand))


class PriceListViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
    mixins.UpdateModelMixin, viewsets.GenericViewSet,
):
    permission_classes = [HasPermission]
    serializer_class = s.PriceListSerializer
    queryset = PriceList.objects.select_related("currency").order_by("currency__code", "name")
    lookup_field = "uuid"
    http_method_names = PATCH_ONLY
    pagination_class = None
    required_permission = {
        "list": "products.view", "retrieve": "products.view",
        "create": "products.manage_prices", "partial_update": "products.manage_prices",
    }

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        obj = services.save_price_list(actor=request.user, data=ser.validated_data, request=request)
        return Response(self.get_serializer(obj).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        ser = self.get_serializer(self.get_object(), data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        obj = services.save_price_list(actor=request.user, instance=ser.instance, data=ser.validated_data, request=request)
        return Response(self.get_serializer(obj).data)


def _truthy(value):
    return str(value).lower() in {"1", "true", "yes"}


class ProductViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
    mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet,
):
    permission_classes = [HasPermission]
    lookup_field = "uuid"
    http_method_names = PATCH_ONLY
    required_permission = {
        "list": "products.view", "retrieve": "products.view", "create": "products.create",
        "partial_update": "products.update", "destroy": "products.delete",
        "publish": "products.publish", "archive": "products.archive",
        "show": "products.toggle_visibility", "hide": "products.toggle_visibility",
        "bulk": "products.toggle_visibility",
        "import_template": "products.import", "import_products": "products.import",
        "variants:GET": "products.view", "variants:POST": "products.update",
        "images": "products.update",
    }

    def get_queryset(self):
        live_variants = ProductVariant.objects.filter(deleted_at__isnull=True).prefetch_related(
            "prices__price_list__currency")
        qs = (
            Product.objects.filter(deleted_at__isnull=True)
            .select_related("category", "brand")
            .prefetch_related(Prefetch("variants", queryset=live_variants), "images")
            .annotate(available=Coalesce(
                Sum(F("variants__stock_levels__on_hand") - F("variants__stock_levels__reserved"),
                    filter=Q(variants__deleted_at__isnull=True)),
                Value(Decimal("0")), output_field=DecimalField()))
        )
        qs = qs.order_by("-created_at", "-id")
        if self.action != "list":
            return qs
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("category"):
            qs = qs.filter(category__uuid=p["category"])
        if p.get("visible") is not None and p.get("visible") != "":
            qs = qs.filter(is_visible_on_store=_truthy(p["visible"]))
        if p.get("search"):
            term = p["search"].strip()
            matching = ProductVariant.objects.filter(product=OuterRef("pk"), deleted_at__isnull=True).filter(
                Q(sku__icontains=term) | Q(barcode__icontains=term))
            qs = qs.filter(Q(name__icontains=term) | Q(brand__name__icontains=term) | Exists(matching))
        if _truthy(p.get("low_stock", "")):
            low = (
                ProductVariant.objects.filter(product=OuterRef("pk"), deleted_at__isnull=True, is_active=True,
                                              reorder_level__gt=0)
                .annotate(avail=Coalesce(Sum(F("stock_levels__on_hand") - F("stock_levels__reserved")),
                                         Value(Decimal("0")), output_field=DecimalField()))
                .filter(avail__lte=F("reorder_level"))
            )
            qs = qs.filter(track_inventory=True).filter(Exists(low))
        return qs

    def get_serializer_class(self):
        if self.action in ("create", "partial_update"):
            return s.ProductWriteSerializer
        return s.ProductDetailSerializer if self.action != "list" else s.ProductListSerializer

    def _detail(self, request, product, code=status.HTTP_200_OK):
        fresh = self.get_queryset().get(pk=product.pk)
        return Response(s.ProductDetailSerializer(fresh, context={"request": request}).data, status=code)

    def create(self, request, *args, **kwargs):
        ser = s.ProductWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = dict(ser.validated_data)
        variants = data.pop("variants", [])
        _require_cost_permission(request, *variants)
        product = services.create_product(actor=request.user, data=data, variants=variants, request=request)
        return self._detail(request, product, status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        ser = s.ProductWriteSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = {k: v for k, v in ser.validated_data.items() if k != "variants"}
        product = services.update_product(actor=request.user, product=self.get_object(), data=data, request=request)
        return self._detail(request, product)

    def destroy(self, request, *args, **kwargs):
        services.delete_product(actor=request.user, product=self.get_object(), request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def publish(self, request, uuid=None):
        return self._detail(request, services.publish(actor=request.user, product=self.get_object(), request=request))

    @action(detail=True, methods=["post"])
    def archive(self, request, uuid=None):
        return self._detail(request, services.archive(actor=request.user, product=self.get_object(), request=request))

    @action(detail=True, methods=["post"])
    def show(self, request, uuid=None):
        return self._detail(request, services.set_visibility(
            actor=request.user, product=self.get_object(), visible=True, request=request))

    @action(detail=True, methods=["post"])
    def hide(self, request, uuid=None):
        return self._detail(request, services.set_visibility(
            actor=request.user, product=self.get_object(), visible=False, request=request))

    @action(detail=False, methods=["post"])
    def bulk(self, request):
        ser = s.BulkSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ids, act = set(ser.validated_data["ids"]), ser.validated_data["action"]
        if act == "archive" and not rbac.has_permission(request.user, "products.archive"):
            raise ServiceError("forbidden", "Missing products.archive.", status.HTTP_403_FORBIDDEN)
        products = list(Product.objects.filter(uuid__in=ids, deleted_at__isnull=True))
        if len(products) != len(ids):
            missing = sorted(str(i) for i in ids - {p.uuid for p in products})
            raise ServiceError("unknown_products", "Some products do not exist.",
                               status.HTTP_404_NOT_FOUND, fields={"ids": missing})
        count = services.bulk(actor=request.user, products=products, action=act, request=request)
        return Response({"action": act, "updated": count})

    @action(detail=False, methods=["get"], url_path="import/template")
    def import_template(self, request):
        response = HttpResponse(
            build_template(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = 'attachment; filename="products-import-template.xlsx"'
        return response

    @action(detail=False, methods=["post"], url_path="import", parser_classes=[MultiPartParser, FormParser])
    def import_products(self, request):
        ser = s.ProductImportSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        report = import_products.run(
            actor=request.user, workbook=d["file"], dry_run=d["dry_run"],
            can_adjust_stock=rbac.has_permission(request.user, "inventory.adjust"), request=request)
        body = report.as_dict(d["dry_run"])
        if report.errors and not d["dry_run"]:
            return Response({"code": "import_invalid", "detail": "The file has errors; nothing was imported.", **body},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(body, status=status.HTTP_200_OK if d["dry_run"] else status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"])
    def variants(self, request, uuid=None):
        product = self.get_object()
        if request.method == "GET":
            from apps.inventory.services import totals_for

            live = [v for v in product.variants.all()]
            ctx = {"request": request, "stock_map": totals_for([v.pk for v in live])}
            return Response(s.VariantSerializer(live, many=True, context=ctx).data)
        ser = s.VariantWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = dict(ser.validated_data)
        prices = data.pop("prices", None)
        _require_cost_permission(request, data)
        variant = services.add_variant(actor=request.user, product=product, data=data, prices=prices, request=request)
        variant = ProductVariant.objects.prefetch_related("prices__price_list__currency").get(pk=variant.pk)
        return Response(s.VariantSerializer(variant, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def images(self, request, uuid=None):
        ser = s.ImageUploadSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        image = services.add_image(
            actor=request.user, product=self.get_object(), file=d["image"], alt=d["alt"],
            variant=d.get("variant"), is_primary=d["is_primary"], request=request)
        return Response(s.ImageSerializer(image, context={"request": request}).data, status=status.HTTP_201_CREATED)


class VariantViewSet(mixins.DestroyModelMixin, viewsets.GenericViewSet):
    permission_classes = [HasPermission]
    lookup_field = "uuid"
    http_method_names = ["patch", "delete", "put", "head", "options"]
    queryset = ProductVariant.objects.filter(deleted_at__isnull=True, product__deleted_at__isnull=True)
    required_permission = {"partial_update": "products.update", "destroy": "products.delete",
                           "prices": "products.manage_prices"}

    def get_queryset(self):
        return self.queryset.select_related("product").prefetch_related("prices__price_list__currency")

    def _out(self, request, variant):
        return Response(s.VariantSerializer(self.get_queryset().get(pk=variant.pk), context={"request": request}).data)

    def partial_update(self, request, *args, **kwargs):
        ser = s.VariantWriteSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = {k: v for k, v in ser.validated_data.items() if k != "prices"}  # prices: use PUT .../prices/
        _require_cost_permission(request, data)
        variant = services.update_variant(actor=request.user, variant=self.get_object(), data=data, request=request)
        return self._out(request, variant)

    def destroy(self, request, *args, **kwargs):
        services.delete_variant(actor=request.user, variant=self.get_object(), request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["put"])
    def prices(self, request, uuid=None):
        ser = s.SetPricesSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        variant = services.set_prices(actor=request.user, variant=self.get_object(),
                                      rows=ser.validated_data["prices"], request=request)
        return self._out(request, variant)


class ImageViewSet(mixins.DestroyModelMixin, viewsets.GenericViewSet):
    permission_classes = [HasPermission]
    queryset = ProductImage.objects.filter(product__deleted_at__isnull=True).select_related("product")
    lookup_url_kwarg = "id"
    http_method_names = ["delete", "head", "options"]
    required_permission = {"destroy": "products.update"}

    def destroy(self, request, *args, **kwargs):
        services.delete_image(actor=request.user, image=self.get_object(), request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)
