import uuid

from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UUIDModel(models.Model):
    """Public identifier used in URLs/API instead of the sequential PK."""

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    class Meta:
        abstract = True


class AuditLog(models.Model):
    """Append-only record of security-relevant events (docs/06-security.md §4)."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    action = models.CharField(max_length=64, db_index=True)
    object_type = models.CharField(max_length=64, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=256, blank=True)
    data = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-at"]
        indexes = [
            models.Index(fields=["object_type", "object_id"], name="audit_object_idx"),  # history of one record
            models.Index(fields=["actor", "-at"], name="audit_actor_at_idx"),  # what did this user do
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("AuditLog is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("AuditLog is append-only.")


class DocumentSequence(models.Model):
    """Gap-free per-key counters for human-readable document numbers (ADJ-000001)."""

    key = models.CharField(max_length=64, unique=True)
    next_value = models.PositiveBigIntegerField(default=1)
