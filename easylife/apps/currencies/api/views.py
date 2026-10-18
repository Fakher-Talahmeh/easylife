from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from apps.accounts.rbac import HasPermission
from apps.currencies import services
from apps.currencies.models import Currency, ExchangeRate

from . import serializers as s

READ = ("accounting.view", "products.view", "inventory.view")


class CurrencyViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
    mixins.UpdateModelMixin, viewsets.GenericViewSet,
):
    permission_classes = [HasPermission]
    serializer_class = s.CurrencySerializer
    queryset = Currency.objects.all().order_by("-is_base", "code")
    lookup_field = "code"
    lookup_value_regex = "[A-Za-z]{3}"
    http_method_names = ["get", "post", "patch", "head", "options"]
    required_permission = {
        "list": READ, "retrieve": READ,
        "create": "accounting.manage_currencies", "partial_update": "accounting.manage_currencies",
    }
    pagination_class = None  # a handful of rows

    def get_object(self):
        self.kwargs["code"] = self.kwargs["code"].upper()
        return super().get_object()


class ExchangeRateViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    permission_classes = [HasPermission]
    serializer_class = s.ExchangeRateSerializer
    required_permission = {"list": READ, "create": "accounting.manage_currencies"}

    def get_queryset(self):
        qs = ExchangeRate.objects.select_related("currency")
        p = self.request.query_params
        if p.get("currency"):
            qs = qs.filter(currency__code=p["currency"].upper())
        if p.get("from"):
            qs = qs.filter(date__gte=p["from"])
        if p.get("to"):
            qs = qs.filter(date__lte=p["to"])
        return qs

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        obj, created = services.set_exchange_rate(
            actor=request.user, request=request, **ser.validated_data
        )
        return Response(self.get_serializer(obj).data,
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
