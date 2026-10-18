from decimal import Decimal

from django.db.models import DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.rbac import HasPermission
from apps.catalog.models import ProductVariant
from apps.core.exceptions import ServiceError
from apps.inventory import services
from apps.inventory.models import (
    StockAdjustment, StockLevel, StockMovement, StockTransfer, Warehouse,
)

from . import serializers as s

ZERO = Value(Decimal("0"), output_field=DecimalField(max_digits=14, decimal_places=3))
NO_PUT = ["get", "post", "patch", "head", "options"]


def _truthy(value):
    return str(value).lower() in {"1", "true", "yes"}


class WarehouseViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
    mixins.UpdateModelMixin, viewsets.GenericViewSet,
):
    permission_classes = [HasPermission]
    serializer_class = s.WarehouseSerializer
    queryset = Warehouse.objects.all()
    lookup_field = "uuid"
    http_method_names = NO_PUT
    pagination_class = None
    required_permission = {
        "list": "inventory.view", "retrieve": "inventory.view",
        "create": "inventory.manage_warehouses", "partial_update": "inventory.manage_warehouses",
    }

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        obj = services.save_warehouse(actor=request.user, data=ser.validated_data, request=request)
        return Response(self.get_serializer(obj).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        ser = self.get_serializer(self.get_object(), data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        obj = services.save_warehouse(actor=request.user, instance=ser.instance,
                                      data=ser.validated_data, request=request)
        return Response(self.get_serializer(obj).data)


class StockViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Current quantities per variant and warehouse."""

    permission_classes = [HasPermission]
    serializer_class = s.StockLevelSerializer
    required_permission = {"list": "inventory.view"}

    def get_queryset(self):
        qs = StockLevel.objects.select_related("variant__product", "warehouse").filter(
            variant__deleted_at__isnull=True).order_by("variant__sku", "warehouse__code")
        p = self.request.query_params
        if p.get("warehouse"):
            qs = qs.filter(warehouse__uuid=p["warehouse"])
        if p.get("variant"):
            qs = qs.filter(variant__uuid=p["variant"])
        if p.get("search"):
            t = p["search"].strip()
            qs = qs.filter(Q(variant__sku__icontains=t) | Q(variant__name__icontains=t)
                           | Q(variant__product__name__icontains=t) | Q(variant__barcode__icontains=t))
        if _truthy(p.get("low_stock", "")):
            qs = qs.filter(variant__reorder_level__gt=0, on_hand__lte=F("variant__reorder_level") + F("reserved"))
        if not _truthy(p.get("include_empty", "")):
            qs = qs.filter(Q(on_hand__gt=0) | Q(reserved__gt=0))
        return qs


class CountSheetViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """The stocktake sheet: every live variant of an inventory-tracked product with its quantity in one warehouse,
    including variants that have never been stocked (quantity 0)."""

    permission_classes = [HasPermission]
    serializer_class = s.CountSheetRowSerializer
    required_permission = {"list": "inventory.view"}

    def get_queryset(self):
        p = self.request.query_params
        if not p.get("warehouse"):
            raise ServiceError("warehouse_required", "Choose a warehouse.", fields={"warehouse": ["This query parameter is required."]})
        warehouse = Warehouse.objects.filter(uuid=p["warehouse"]).first()
        if warehouse is None:
            raise ServiceError("warehouse_not_found", "Unknown warehouse.", status_code=status.HTTP_404_NOT_FOUND)
        here = Q(stock_levels__warehouse=warehouse)
        qs = (
            ProductVariant.objects.select_related("product")
            .filter(deleted_at__isnull=True, is_active=True, product__deleted_at__isnull=True, product__track_inventory=True)
            .annotate(on_hand=Coalesce(Sum("stock_levels__on_hand", filter=here), ZERO),
                      reserved=Coalesce(Sum("stock_levels__reserved", filter=here), ZERO))
            .order_by("product__name", "sku")
        )
        if p.get("search"):
            t = p["search"].strip()
            qs = qs.filter(Q(sku__icontains=t) | Q(name__icontains=t) | Q(product__name__icontains=t) | Q(barcode__icontains=t))
        return qs


class MovementViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """The append-only stock ledger (newest first)."""

    permission_classes = [HasPermission]
    serializer_class = s.MovementSerializer
    required_permission = {"list": "inventory.view"}

    def get_queryset(self):
        qs = StockMovement.objects.select_related("variant__product", "warehouse", "created_by")
        p = self.request.query_params
        if p.get("variant"):
            qs = qs.filter(variant__uuid=p["variant"])
        if p.get("warehouse"):
            qs = qs.filter(warehouse__uuid=p["warehouse"])
        if p.get("type"):
            qs = qs.filter(type=p["type"])
        if p.get("from"):
            qs = qs.filter(created_at__date__gte=p["from"])
        if p.get("to"):
            qs = qs.filter(created_at__date__lte=p["to"])
        return qs


class AdjustmentViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    permission_classes = [HasPermission]
    serializer_class = s.AdjustmentSerializer
    lookup_field = "uuid"
    http_method_names = ["get", "post", "head", "options"]
    required_permission = {"list": "inventory.view", "retrieve": "inventory.view",
                           "create": "inventory.adjust", "post_adjustment": "inventory.adjust"}

    def get_queryset(self):
        qs = StockAdjustment.objects.select_related("warehouse", "created_by", "posted_by").prefetch_related(
            "lines__variant__product")
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("warehouse"):
            qs = qs.filter(warehouse__uuid=p["warehouse"])
        return qs

    def _out(self, adj, code=status.HTTP_200_OK):
        fresh = self.get_queryset().get(pk=adj.pk)
        return Response(s.AdjustmentSerializer(fresh, context=self.get_serializer_context()).data, status=code)

    def create(self, request, *args, **kwargs):
        ser = s.AdjustmentCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        adj = services.create_adjustment(
            actor=request.user, warehouse=d["warehouse"], reason=d["reason"], note=d["note"],
            lines=[dict(ln) for ln in d["lines"]], request=request)
        if d["post"]:
            services.post_adjustment(actor=request.user, adjustment=adj, request=request)
        return self._out(adj, status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="post")
    def post_adjustment(self, request, uuid=None):
        adj = services.post_adjustment(actor=request.user, adjustment=self.get_object(), request=request)
        return self._out(adj)


class TransferViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    permission_classes = [HasPermission]
    serializer_class = s.TransferSerializer
    lookup_field = "uuid"
    http_method_names = ["get", "post", "head", "options"]
    required_permission = {"list": "inventory.view", "retrieve": "inventory.view",
                           "create": "inventory.transfer", "receive": "inventory.transfer",
                           "cancel": "inventory.transfer"}

    def get_queryset(self):
        qs = StockTransfer.objects.select_related(
            "from_warehouse", "to_warehouse", "created_by", "received_by").prefetch_related("lines__variant__product")
        if self.request.query_params.get("status"):
            qs = qs.filter(status=self.request.query_params["status"])
        return qs

    def _out(self, transfer, code=status.HTTP_200_OK):
        fresh = self.get_queryset().get(pk=transfer.pk)
        return Response(s.TransferSerializer(fresh, context=self.get_serializer_context()).data, status=code)

    def create(self, request, *args, **kwargs):
        ser = s.TransferCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        t = services.create_transfer(
            actor=request.user, from_warehouse=d["from_warehouse"], to_warehouse=d["to_warehouse"],
            note=d["note"], lines=[dict(ln) for ln in d["lines"]], request=request)
        return self._out(t, status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def receive(self, request, uuid=None):
        return self._out(services.receive_transfer(actor=request.user, transfer=self.get_object(), request=request))

    @action(detail=True, methods=["post"])
    def cancel(self, request, uuid=None):
        return self._out(services.cancel_transfer(actor=request.user, transfer=self.get_object(), request=request))
