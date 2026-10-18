from rest_framework import serializers

from apps.accounts import rbac
from apps.accounts.models import Role, User


class SendCodeSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=32)


class VerifyCodeSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=32)
    code = serializers.CharField(max_length=10)
    full_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")


class RefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class MeSerializer(serializers.ModelSerializer):
    """Own profile. Role/type/flags are read-only: no mass assignment."""

    permissions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["uuid", "full_name", "phone", "user_type", "is_superadmin", "permissions"]
        read_only_fields = ["uuid", "phone", "user_type", "is_superadmin", "permissions"]

    def get_permissions(self, user):
        return sorted(rbac.effective_permissions(user))


class StaffSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["uuid", "full_name", "phone", "is_active", "is_superadmin",
                  "last_login", "created_at", "roles"]
        # Phone is the identity: changing it needs normalisation + verification.
        read_only_fields = ["uuid", "phone", "is_active", "is_superadmin", "last_login",
                            "created_at", "roles"]

    def get_roles(self, user):
        return [ur.role.name for ur in user.user_roles.all()]


class StaffCreateSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=32)
    full_name = serializers.CharField(max_length=150)
    role_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    is_superadmin = serializers.BooleanField(required=False, default=False)


class SetRolesSerializer(serializers.Serializer):
    role_ids = serializers.ListField(child=serializers.IntegerField())


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["id", "name", "description", "is_system"]


class CustomerSerializer(serializers.ModelSerializer):
    """`balance`: what the customer owes now (null when the viewer may not see balances)."""

    balance = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["uuid", "full_name", "phone", "is_active", "created_at", "balance"]
        read_only_fields = fields

    def get_balance(self, user):
        if not self.context.get("show_balance"):
            return None
        return f"{getattr(user, 'balance', 0) or 0:.2f}"


class CustomerCreateSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=150)
    phone = serializers.CharField(max_length=32)


class PromoteSerializer(serializers.Serializer):
    role_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
