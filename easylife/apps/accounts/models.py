from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models import Q

from apps.core.models import TimeStampedModel, UUIDModel


class UserType(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    STAFF = "staff", "Staff"


class UserManager(BaseUserManager):
    """Accounts are identified by a WhatsApp-verified phone number. There are no
    passwords: authentication happens only through one-time codes sent over WhatsApp."""

    use_in_migrations = True

    def _create(self, phone, **extra):
        from .whatsapp import normalize_phone

        normalized = normalize_phone(phone)
        if not normalized:
            raise ValueError("A valid phone number is required.")
        user = self.model(phone=normalized, **extra)
        user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_customer(self, phone, **extra):
        return self._create(phone, user_type=UserType.CUSTOMER, **extra)

    def create_staff(self, phone, **extra):
        return self._create(phone, user_type=UserType.STAFF, **extra)

    def create_superadmin(self, phone, **extra):
        return self._create(phone, user_type=UserType.STAFF, is_superadmin=True, **extra)


class User(UUIDModel, TimeStampedModel, AbstractBaseUser):
    # Digits only, international format (e.g. 970591234567), see whatsapp.normalize_phone.
    phone = models.CharField(max_length=20, unique=True)
    full_name = models.CharField(max_length=150)
    user_type = models.CharField(max_length=16, choices=UserType.choices, default=UserType.CUSTOMER)
    is_superadmin = models.BooleanField(
        default=False, help_text="Admin: bypasses permission checks. Staff only."
    )
    is_active = models.BooleanField(default=True)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(is_superadmin=False) | Q(user_type="staff"),
                name="superadmin_must_be_staff",
            ),
        ]
        indexes = [
            # staff / customer lists filter by type and show the newest first
            models.Index(fields=["user_type", "-created_at"], name="user_type_created_idx"),
        ]

    def __str__(self):
        return self.phone

    @property
    def is_staff_user(self):
        return self.user_type == UserType.STAFF


# ---------------------------------------------------------------- RBAC


class Permission(models.Model):
    """Catalog of permission codes; seeded from permissions_catalog.py, not user-created."""

    code = models.CharField(max_length=64, unique=True)  # "module.action"
    module = models.CharField(max_length=32, db_index=True)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class Role(TimeStampedModel):
    name = models.CharField(max_length=64, unique=True)
    description = models.CharField(max_length=200, blank=True)
    is_system = models.BooleanField(default=False)
    permissions = models.ManyToManyField(Permission, through="RolePermission", related_name="roles")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    def __str__(self):
        return self.name


class RolePermission(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE)
    scope = models.JSONField(default=dict, blank=True)  # e.g. {"warehouses": [1]} (enforced later)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["role", "permission"], name="uniq_role_permission")
        ]


class UserRole(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_roles")
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="user_roles")
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "role"], name="uniq_user_role")]


class UserPermissionOverride(models.Model):
    class Effect(models.TextChoices):
        ALLOW = "allow", "Allow"
        DENY = "deny", "Deny"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="permission_overrides")
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE)
    effect = models.CharField(max_length=8, choices=Effect.choices)
    scope = models.JSONField(default=dict, blank=True)
    reason = models.CharField(max_length=200, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "permission"], name="uniq_user_permission_override")
        ]


class WhatsAppCode(models.Model):
    """One-time login/registration code sent over WhatsApp.

    Only an HMAC of the code is stored, never the code itself."""

    phone = models.CharField(max_length=20, db_index=True)
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
    requested_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["phone", "-created_at"])]
