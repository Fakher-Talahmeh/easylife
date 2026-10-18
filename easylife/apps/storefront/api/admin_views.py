from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.rbac import HasPermission

from .. import services
from ..models import ShopProfile
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
