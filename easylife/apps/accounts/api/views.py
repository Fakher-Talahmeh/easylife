from decimal import Decimal

from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.exceptions import TokenError

from apps.accounts import rbac, services
from apps.accounts.models import Role, User, UserType
from apps.accounts.rbac import HasPermission

from . import serializers as s


class ThrottledAPIView(APIView):
    throttle_classes = [ScopedRateThrottle]


# ------------------------------------------------------------------ public auth


class WhatsAppSendCodeView(ThrottledAPIView):
    permission_classes = [AllowAny]
    throttle_scope = "otp_send"

    def post(self, request):
        ser = s.SendCodeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        info = services.send_whatsapp_code(phone=ser.validated_data["phone"], request=request)
        return Response({"detail": "Code sent.", **info})


class WhatsAppVerifyView(ThrottledAPIView):
    """Verifies the code, then logs in the existing account (any type) or creates a customer.
    For a new number the client must also send `full_name` (otherwise 400 full_name_required,
    and the code stays valid so the user only has to add their name)."""

    permission_classes = [AllowAny]
    throttle_scope = "otp_verify"

    def post(self, request):
        ser = s.VerifyCodeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user, tokens, created = services.verify_whatsapp_code(request=request, **ser.validated_data)
        return Response(
            {
                "created": created,
                "user_type": user.user_type,
                "is_superadmin": user.is_superadmin,
                "user": s.MeSerializer(user).data,
                **tokens,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class RefreshView(ThrottledAPIView):
    permission_classes = [AllowAny]
    throttle_scope = "login"

    def post(self, request):
        ser = s.RefreshSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        refresh_ser = TokenRefreshSerializer(data=ser.validated_data)
        try:
            refresh_ser.is_valid(raise_exception=True)
        except TokenError:
            return Response({"code": "invalid_token", "detail": "Invalid or expired token."},
                            status=status.HTTP_401_UNAUTHORIZED)
        return Response(refresh_ser.validated_data)


class LogoutView(APIView):
    def post(self, request):
        ser = s.RefreshSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        services.logout(refresh_token=ser.validated_data["refresh"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    def get(self, request):
        return Response(s.MeSerializer(request.user).data)

    def patch(self, request):
        ser = s.MeSerializer(request.user, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)


# ------------------------------------------------------------------ admin: staff


class StaffViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet
):
    permission_classes = [HasPermission]
    lookup_field = "uuid"
    http_method_names = ["get", "post", "put", "patch", "head", "options"]
    required_permission = {
        "list": "users.view",
        "retrieve": "users.view",
        "effective_permissions": "users.view",
        "create": "users.create",
        "partial_update": "users.update",
        "update": "users.update",
        "deactivate": "users.deactivate",
        "activate": "users.deactivate",
        "roles": "users.manage_roles",
    }

    @staticmethod
    def queryset_for_staff():
        return (
            User.objects.filter(user_type=UserType.STAFF)
            .prefetch_related("user_roles__role")
            .order_by("-created_at")
        )

    def get_queryset(self):
        return self.queryset_for_staff()

    def get_serializer_class(self):
        return s.StaffSerializer

    def create(self, request):
        ser = s.StaffCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = services.create_staff(actor=request.user, request=request, **ser.validated_data)
        notified = services.notify_staff_added(user)
        return Response(
            {**s.StaffSerializer(user).data, "whatsapp_notified": notified},
            status=status.HTTP_201_CREATED,
        )

    def _toggle(self, request, uuid, active):
        user = get_object_or_404(self.get_queryset(), uuid=uuid)
        services.set_active(actor=request.user, user=user, active=active, request=request)
        return Response(s.StaffSerializer(user).data)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, uuid=None):
        return self._toggle(request, uuid, False)

    @action(detail=True, methods=["post"])
    def activate(self, request, uuid=None):
        return self._toggle(request, uuid, True)

    @action(detail=True, methods=["put"])
    def roles(self, request, uuid=None):
        user = get_object_or_404(self.get_queryset(), uuid=uuid)
        ser = s.SetRolesSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        services.set_user_roles(
            actor=request.user, user=user, role_ids=ser.validated_data["role_ids"], request=request
        )
        user = self.get_queryset().get(pk=user.pk)
        return Response(s.StaffSerializer(user).data)

    @action(detail=True, methods=["get"], url_path="effective-permissions")
    def effective_permissions(self, request, uuid=None):
        user = get_object_or_404(self.get_queryset(), uuid=uuid)
        return Response({"permissions": sorted(rbac.effective_permissions(user))})


class CustomerViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Customers as seen by the back office: browse/search/add them (also from the till, to sell on credit),
    see what each one owes, and promote one to staff."""

    permission_classes = [HasPermission]
    lookup_field = "uuid"
    filter_backends = [filters.SearchFilter]
    search_fields = ["phone", "full_name"]
    required_permission = {
        # a cashier who may sell on credit has to find (or add) the customer the debt goes on
        "list": ("customers.view", "sales.credit"),
        "retrieve": ("customers.view", "sales.credit"),
        "create": ("customers.create", "sales.credit"),
        "promote": "users.create",
    }

    def get_queryset(self):
        # balance = what is still owed on the customer's invoices (refunded ones owe nothing)
        owed = Coalesce(Sum("purchases__balance_due"), Value(Decimal("0")), output_field=DecimalField())
        return User.objects.filter(user_type=UserType.CUSTOMER).annotate(balance=owed).order_by("-created_at")

    def get_serializer_class(self):
        return s.CustomerSerializer

    def get_serializer_context(self):
        user = self.request.user
        show = rbac.has_permission(user, "customers.view_balance") or rbac.has_permission(user, "sales.credit")
        return {**super().get_serializer_context(), "show_balance": show}

    def create(self, request):
        ser = s.CustomerCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = services.create_customer(actor=request.user, request=request, **ser.validated_data)
        return Response(self.get_serializer(self.get_queryset().get(pk=user.pk)).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def promote(self, request, uuid=None):
        user = get_object_or_404(self.get_queryset(), uuid=uuid)
        ser = s.PromoteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        services.promote_to_staff(
            actor=request.user, user=user, role_ids=ser.validated_data["role_ids"], request=request
        )
        staff = StaffViewSet.queryset_for_staff().get(pk=user.pk)
        notified = services.notify_staff_added(staff)
        return Response({**s.StaffSerializer(staff).data, "whatsapp_notified": notified})


class RoleListView(APIView):
    """Read-only for now; role editing comes in the next step."""

    permission_classes = [HasPermission]
    required_permission = "users.view"

    def get(self, request):
        return Response(s.RoleSerializer(Role.objects.all().order_by("name"), many=True).data)
