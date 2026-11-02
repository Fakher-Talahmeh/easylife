from decimal import Decimal

from django.db import transaction

from apps.core import audit
from apps.accounts.whatsapp import WhatsAppDeliveryError, send_text
from apps.catalog.models import ProductVariant
from apps.core.exceptions import ServiceError
from apps.inventory.models import MovementType, StockLevel, Warehouse
from apps.inventory.services import move_stock

from .models import ShopProfile, SocialLink

STATUS_LABELS_AR = {
    "pending": "معلقة", "confirmed": "تم تأكيدها", "preparing": "قيد التجهيز",
    "ready_for_shipping": "جاهزة للشحن", "out_for_delivery": "قيد التوصيل",
    "delivered": "تم التسليم", "cancelled": "ملغاة", "delivery_failed": "فشل التسليم", "returned": "مرتجعة",
}


@transaction.atomic
def deduct_online_order_stock(order, *, actor=None):
    """Deduct an online order exactly once when it starts being prepared."""
    locked_order = type(order).objects.select_for_update().get(pk=order.pk)
    if locked_order.stock_deducted:
        return locked_order

    warehouses = list(Warehouse.objects.filter(is_active=True).order_by("-is_default", "code"))
    if not warehouses:
        raise ServiceError("warehouse_not_configured", "لا يوجد مستودع فعال لخصم المخزون.")

    lines = []
    for line in locked_order.lines:
        variant_id = line.get("variant")
        quantity = Decimal(str(line.get("qty", 0)))
        if not variant_id or quantity <= 0:
            raise ServiceError("order_lines_missing_variant", "لا يمكن خصم المخزون لأن تفاصيل الطلب غير مكتملة.")
        # Online-order snapshots store the public UUID, not the database integer PK.
        variant = ProductVariant.objects.select_related("product").get(uuid=variant_id)
        if variant.product.track_inventory:
            lines.append((variant, quantity))

    # move_stock is atomic itself; this surrounding transaction also rolls back
    # every previous line if a later line has insufficient stock.
    for variant, quantity in sorted(lines, key=lambda item: str(item[0].pk)):
        remaining = quantity
        levels = StockLevel.objects.filter(variant=variant, warehouse__in=warehouses, on_hand__gt=0).select_related("warehouse").order_by("-warehouse__is_default", "warehouse__code")
        for level in levels:
            available = level.on_hand - level.reserved
            if available <= 0:
                continue
            taken = min(remaining, available)
            move_stock(variant=variant, warehouse=level.warehouse, quantity=-taken, type=MovementType.SALE_OUT, ref_type="online_order", ref_id=locked_order.number, note=f"Online order {locked_order.number}", actor=actor)
            remaining -= taken
            if remaining <= 0:
                break
        if remaining > 0:
            raise ServiceError("insufficient_stock", f"Not enough stock for {variant.sku}.", fields={"sku": [variant.sku], "requested": [str(quantity)], "available": [str(quantity - remaining)]})

    locked_order.stock_deducted = True
    locked_order.save(update_fields=["stock_deducted", "updated_at"])
    return locked_order


def notify_order_status(order):
    try:
        message = [
            f"أهلًا {order.customer_name} 🌿",
            f"تم تغيير حالة طلبك رقم {order.number} إلى: {STATUS_LABELS_AR.get(order.status, order.status)}.",
            "",
            "محتوى الطلبية:",
        ]
        message.extend(f"• {line.get('name', '')} × {line.get('qty', 0)} — {line.get('total', '0')} ₪" for line in order.lines)
        message.extend([
            "",
            f"المجموع الفرعي: {order.subtotal} ₪",
            *([f"خصم العروض: -{order.discount_total} ₪"] if order.discount_total > 0 else []),
            f"التوصيل ({order.area.name}): {order.delivery_fee} ₪",
            f"الإجمالي النهائي: {order.total} ₪",
            f"العنوان: {order.city}، {order.address}",
        ])
        if order.status == "delivered":
            message.extend(["", "شكرًا لاختيارك متجرنا ووقوفك معنا 🤍", "نتمنى أن تنال مشترياتك إعجابك، ونسعد بخدمتك دائمًا."])
        send_text(order.phone, "\n".join(message))
        return True
    except WhatsAppDeliveryError:
        return False


@transaction.atomic
def update_profile(*, actor, data, request=None):
    """Saves the shop profile; `social_links`, when sent, REPLACES the whole list (the admin edits it as one block)."""
    data = dict(data)
    links = data.pop("social_links", None)
    profile = ShopProfile.load()
    for key, value in data.items():
        setattr(profile, key, value)
    profile.save()
    if links is not None:
        profile.social_links.all().delete()
        SocialLink.objects.bulk_create(
            [SocialLink(profile=profile, **{**link, "sort_order": link.get("sort_order", i)}) for i, link in enumerate(links)])
    audit.log("store_profile.updated", actor=actor, request=request, obj=profile,
              fields=sorted(data) + (["social_links"] if links is not None else []))
    return profile
