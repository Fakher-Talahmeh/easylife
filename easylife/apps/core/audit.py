from .models import AuditLog


def client_ip(request):
    if request is None:
        return None
    return request.META.get("REMOTE_ADDR") or None


def log(action, *, actor=None, request=None, obj=None, **data):
    """Record an audit event. Never put secrets (passwords, tokens) in `data`."""
    AuditLog.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        action=action,
        object_type=obj.__class__.__name__ if obj is not None else "",
        object_id=str(getattr(obj, "pk", "")) if obj is not None else "",
        ip=client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT", "")[:256] if request else ""),
        data=data,
    )
