from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import generics
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, MultiPartParser, JSONParser
from django.db import transaction

from apps.accounts.rbac import HasPermission

from .. import services
from ..models import DeliveryArea, OnlineOrder, ShopProfile, StoreAd
from . import serializers as s


class ShopProfileView(APIView):
    """The shop's About / contact / location content, edited as one block (there is exactly one profile)."""

    permission_classes = [HasPermission]
    http_method_names = ["get", "patch", "head", "options"]

    @property
    def required_permission(self):
        return "storefront.manage" if self.request.method == "PATCH" else "storefront.view"

    def get(self, request):
        return Response(s.ShopProfileSerializer(ShopProfile.load()).data)

    def patch(self, request):
        ser = s.ShopProfileSerializer(ShopProfile.load(), data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        profile = services.update_profile(actor=request.user, data=ser.validated_data, request=request)
        return Response(s.ShopProfileSerializer(profile).data)


class DeliveryAreaListCreateView(generics.ListCreateAPIView):
    permission_classes = [HasPermission]
    required_permission = "storefront.manage"
    queryset = DeliveryArea.objects.all()
    serializer_class = s.DeliveryAreaSerializer


class DeliveryAreaDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [HasPermission]
    required_permission = "storefront.manage"
    queryset = DeliveryArea.objects.all()
    serializer_class = s.DeliveryAreaSerializer
    lookup_field = "uuid"


class StoreAdListCreateView(generics.ListCreateAPIView):
    permission_classes = [HasPermission]
    required_permission = "storefront.manage"
    queryset = StoreAd.objects.all()
    serializer_class = s.StoreAdSerializer
    parser_classes = [JSONParser, FormParser, MultiPartParser]


class StoreAdDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [HasPermission]
    required_permission = "storefront.manage"
    queryset = StoreAd.objects.all()
    serializer_class = s.StoreAdSerializer
    parser_classes = [JSONParser, FormParser, MultiPartParser]
    lookup_field = "uuid"


class OnlineOrderListView(generics.ListAPIView):
    permission_classes = [HasPermission]
    required_permission = "orders.view"
    queryset = OnlineOrder.objects.select_related("area").order_by("-created_at")
    serializer_class = s.OnlineOrderSerializer
    class Pagination(PageNumberPagination):
        page_size = 40
        page_size_query_param = "page_size"
        max_page_size = 40

    pagination_class = Pagination


class OnlineOrderDetailView(generics.UpdateAPIView):
    permission_classes = [HasPermission]
    required_permission = "orders.update"
    queryset = OnlineOrder.objects.select_related("area").all()
    serializer_class = s.OnlineOrderSerializer
    lookup_field = "uuid"

    def update(self, request, *args, **kwargs):
        order = self.get_object()
        old_status = order.status
        with transaction.atomic():
            response = super().update(request, *args, **kwargs)
            # DRF loads a fresh instance inside super().update(); re-read it before comparing statuses.
            updated_order = self.get_queryset().select_for_update().get(uuid=order.uuid)
            if old_status == OnlineOrder.Status.CONFIRMED and updated_order.status == OnlineOrder.Status.PREPARING:
                updated_order = services.deduct_online_order_stock(updated_order, actor=request.user)
        if updated_order.status != old_status:
            response.data["whatsapp_notified"] = services.notify_order_status(updated_order)
        return response
