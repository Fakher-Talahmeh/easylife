"""All stock changes go through here: row-locked, ledgered, never negative.

Lock order: ProductVariant (only when its average cost changes) -> StockLevel rows sorted by
(variant_id, warehouse_id). Callers moving several lines must iterate in that order.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.catalog.models import ProductVariant
from apps.core import audit
from apps.core.exceptions import ServiceError
from apps.core.numbering import next_number

from .models import (
    AdjustmentReason, MovementType, ReservationStatus, StockAdjustment, StockAdjustmentLine,
    StockLevel, StockMovement, StockReservation, StockTransfer, StockTransferLine, Warehouse,
)

ZERO = Decimal("0")
COST_STEP = Decimal("0.0001")


# ---------------------------------------------------------------- helpers


def _dec(value, field="quantity"):
    try:
        return Decimal(str(value))
    except Exception:
        raise ServiceError("invalid_quantity", fields={field: ["Not a valid number."]})


def _ensure_stockable(variant, warehouse):
    if variant.deleted_at or variant.product.deleted_at:
        raise ServiceError("variant_deleted", f"{variant.sku} was deleted.")
    if not variant.product.track_inventory:
        raise ServiceError("not_tracked", f"{variant.sku} does not track inventory.")
    if not warehouse.is_active:
        raise ServiceError("warehouse_inactive", f"Warehouse {warehouse.code} is inactive.")


def _lock_level(variant, warehouse):
    level, _ = StockLevel.objects.get_or_create(variant=variant, warehouse=warehouse)
    return StockLevel.objects.select_for_update().get(pk=level.pk)


def totals_for(variant_ids):
    """{variant_id: {"on_hand", "reserved", "available"}} summed over all warehouses."""
    rows = (
        StockLevel.objects.filter(variant_id__in=variant_ids)
        .values("variant_id").annotate(on_hand=Sum("on_hand"), reserved=Sum("reserved"))
    )
    out = {vid: {"on_hand": ZERO, "reserved": ZERO, "available": ZERO} for vid in variant_ids}
    for r in rows:
        out[r["variant_id"]] = {
            "on_hand": r["on_hand"], "reserved": r["reserved"], "available": r["on_hand"] - r["reserved"],
        }
    return out


def _insufficient(variant, wanted, available):
    return ServiceError(
        "insufficient_stock", f"Not enough stock for {variant.sku}.",
        fields={"sku": [variant.sku], "requested": [str(wanted)], "available": [str(available)]},
    )


# ---------------------------------------------------------------- core ledger


@transaction.atomic
def move_stock(*, variant, warehouse, quantity, type, unit_cost=None, update_cost=False,
               ref_type="", ref_id="", note="", actor=None):
    """Apply a signed quantity to one variant/warehouse and append the ledger row.

    `unit_cost` (base currency) is the cost of incoming goods. With `update_cost=True` the variant's
    weighted-average cost is recomputed over the stock held in ALL warehouses.
    Outgoing quantity may only consume *available* stock (on hand minus reserved)."""
    quantity = _dec(quantity)
    if quantity == 0:
        raise ServiceError("invalid_quantity", fields={"quantity": ["Quantity cannot be zero."]})
    _ensure_stockable(variant, warehouse)

    # Always work from the database's current cost: the caller's instance may be stale.
    if update_cost:
        variant = ProductVariant.objects.select_for_update().select_related("product").get(pk=variant.pk)
    else:
        variant = ProductVariant.objects.select_related("product").get(pk=variant.pk)
    level = _lock_level(variant, warehouse)

    new_on_hand = level.on_hand + quantity
    if new_on_hand < level.reserved:
        raise _insufficient(variant, -quantity, level.on_hand - level.reserved)

    cost_before = variant.cost_price
    if quantity > 0 and unit_cost is not None:
        unit_cost = _dec(unit_cost, "unit_cost")
        if unit_cost < 0:
            raise ServiceError("invalid_cost", fields={"unit_cost": ["Cost cannot be negative."]})
        if update_cost:
            held = StockLevel.objects.filter(variant=variant).aggregate(t=Sum("on_hand"))["t"] or ZERO
            new_cost = (held * cost_before + quantity * unit_cost) / (held + quantity)
            variant.cost_price = new_cost.quantize(COST_STEP, rounding=ROUND_HALF_UP)
            variant.save(update_fields=["cost_price", "updated_at"])
        move_cost = unit_cost
    else:
        move_cost = cost_before

    level.on_hand = new_on_hand
    level.save(update_fields=["on_hand", "updated_at"])
    return StockMovement.objects.create(
        variant=variant, warehouse=warehouse, type=type, quantity=quantity, unit_cost=move_cost,
        balance_after=new_on_hand, ref_type=ref_type, ref_id=str(ref_id), note=note[:255], created_by=actor,
    )


# ---------------------------------------------------------------- reservations


@transaction.atomic
def reserve(*, variant, warehouse, quantity, ref_type="", ref_id="", expires_at=None):
    quantity = _dec(quantity)
    if quantity <= 0:
        raise ServiceError("invalid_quantity", fields={"quantity": ["Must be greater than zero."]})
    _ensure_stockable(variant, warehouse)
    level = _lock_level(variant, warehouse)
    if quantity > level.on_hand - level.reserved:
        raise _insufficient(variant, quantity, level.on_hand - level.reserved)
    level.reserved += quantity
    level.save(update_fields=["reserved", "updated_at"])
    return StockReservation.objects.create(
        variant=variant, warehouse=warehouse, quantity=quantity,
        ref_type=ref_type, ref_id=str(ref_id), expires_at=expires_at,
    )


def _active_reservation(reservation):
    locked = StockReservation.objects.select_for_update().get(pk=reservation.pk)
    if locked.status != ReservationStatus.ACTIVE:
        raise ServiceError("reservation_not_active", f"Reservation is already {locked.status}.")
    return locked


@transaction.atomic
def release_reservation(reservation, status=ReservationStatus.RELEASED):
    res = _active_reservation(reservation)
    level = _lock_level(res.variant, res.warehouse)
    level.reserved -= res.quantity
    level.save(update_fields=["reserved", "updated_at"])
    res.status, res.resolved_at = status, timezone.now()
    res.save(update_fields=["status", "resolved_at"])
    return res


@transaction.atomic
def fulfill_reservation(reservation, *, actor=None, note=""):
    """Turn a reservation into a real sale: stock leaves the warehouse."""
    res = _active_reservation(reservation)
    level = _lock_level(res.variant, res.warehouse)
    level.on_hand -= res.quantity
    level.reserved -= res.quantity
    level.save(update_fields=["on_hand", "reserved", "updated_at"])
    movement = StockMovement.objects.create(
        variant=res.variant, warehouse=res.warehouse, type=MovementType.SALE_OUT, quantity=-res.quantity,
        unit_cost=res.variant.cost_price, balance_after=level.on_hand,
        ref_type=res.ref_type, ref_id=res.ref_id, note=note[:255], created_by=actor,
    )
    res.status, res.resolved_at = ReservationStatus.FULFILLED, timezone.now()
    res.save(update_fields=["status", "resolved_at"])
    return movement


def expire_reservations(now=None):
    """Release every active reservation past its expiry (run periodically)."""
    now = now or timezone.now()
    count = 0
    for res in StockReservation.objects.filter(status=ReservationStatus.ACTIVE, expires_at__lte=now):
        try:
            release_reservation(res, ReservationStatus.EXPIRED)
            count += 1
        except ServiceError:
            continue  # resolved concurrently
    return count


# ---------------------------------------------------------------- adjustments (stock counts)


@transaction.atomic
def create_adjustment(*, actor, warehouse, reason, note, lines, request=None):
    if not warehouse.is_active:
        raise ServiceError("warehouse_inactive", f"Warehouse {warehouse.code} is inactive.")
    if not lines:
        raise ServiceError("no_lines", fields={"lines": ["Add at least one line."]})
    seen = set()
    for ln in lines:
        v = ln["variant"]
        if v.pk in seen:
            raise ServiceError("duplicate_line", f"{v.sku} appears twice.", fields={"lines": [v.sku]})
        seen.add(v.pk)
        _ensure_stockable(v, warehouse)
    adj = StockAdjustment.objects.create(
        number=next_number("adjustment", "ADJ-"), warehouse=warehouse, reason=reason,
        note=note, created_by=actor,
    )
    StockAdjustmentLine.objects.bulk_create([
        StockAdjustmentLine(adjustment=adj, variant=ln["variant"], counted_qty=ln["counted_qty"],
                            unit_cost=ln.get("unit_cost")) for ln in lines
    ])
    audit.log("stock.adjustment_created", actor=actor, request=request, obj=adj, reason=reason, lines=len(lines))
    return adj


@transaction.atomic
def post_adjustment(*, actor, adjustment, request=None):
    """Compare each counted quantity with the system quantity and book the difference."""
    adj = StockAdjustment.objects.select_for_update().get(pk=adjustment.pk)
    if adj.status == StockAdjustment.Status.POSTED:
        raise ServiceError("already_posted", "This adjustment is already posted.")
    is_opening = adj.reason == AdjustmentReason.OPENING
    changed = 0
    for line in sorted(adj.lines.select_related("variant__product"), key=lambda ln: ln.variant_id):
        level = _lock_level(line.variant, adj.warehouse)
        diff = line.counted_qty - level.on_hand
        if line.counted_qty < level.reserved:
            raise ServiceError(
                "below_reserved", f"{line.variant.sku}: counted quantity is below the reserved quantity.",
                fields={"sku": [line.variant.sku], "reserved": [str(level.reserved)]},
            )
        if is_opening and diff > 0 and line.unit_cost is None:
            raise ServiceError("unit_cost_required", f"{line.variant.sku}: opening stock needs a unit cost.",
                               fields={"sku": [line.variant.sku]})
        line.system_qty, line.diff = level.on_hand, diff
        line.save(update_fields=["system_qty", "diff"])
        if diff != 0:
            move_stock(
                variant=line.variant, warehouse=adj.warehouse, quantity=diff,
                type=MovementType.OPENING if is_opening else MovementType.ADJUSTMENT,
                unit_cost=line.unit_cost if diff > 0 else None,
                update_cost=diff > 0 and line.unit_cost is not None,
                ref_type="adjustment", ref_id=adj.pk, note=f"{adj.number} ({adj.reason})", actor=actor,
            )
            changed += 1
    adj.status, adj.posted_by, adj.posted_at = StockAdjustment.Status.POSTED, actor, timezone.now()
    adj.save(update_fields=["status", "posted_by", "posted_at", "updated_at"])
    audit.log("stock.adjustment_posted", actor=actor, request=request, obj=adj, moved_lines=changed)
    return adj


# ---------------------------------------------------------------- transfers


@transaction.atomic
def create_transfer(*, actor, from_warehouse, to_warehouse, note, lines, request=None):
    """Dispatch stock from one warehouse; it is "in transit" until the destination receives it."""
    if from_warehouse.pk == to_warehouse.pk:
        raise ServiceError("same_warehouse", "Source and destination must differ.")
    if not to_warehouse.is_active:
        raise ServiceError("warehouse_inactive", f"Warehouse {to_warehouse.code} is inactive.")
    if not lines:
        raise ServiceError("no_lines", fields={"lines": ["Add at least one line."]})
    seen = set()
    for ln in lines:
        v = ln["variant"]
        if v.pk in seen:
            raise ServiceError("duplicate_line", f"{v.sku} appears twice.", fields={"lines": [v.sku]})
        seen.add(v.pk)
        if _dec(ln["quantity"]) <= 0:
            raise ServiceError("invalid_quantity", fields={"lines": ["Quantities must be greater than zero."]})
    transfer = StockTransfer.objects.create(
        number=next_number("transfer", "TRF-"), from_warehouse=from_warehouse,
        to_warehouse=to_warehouse, note=note, created_by=actor,
    )
    for ln in sorted(lines, key=lambda x: x["variant"].pk):
        v, qty = ln["variant"], _dec(ln["quantity"])
        move = move_stock(
            variant=v, warehouse=from_warehouse, quantity=-qty, type=MovementType.TRANSFER_OUT,
            ref_type="transfer", ref_id=transfer.pk, note=transfer.number, actor=actor,
        )
        StockTransferLine.objects.create(transfer=transfer, variant=v, quantity=qty, unit_cost=move.unit_cost)
    audit.log("stock.transfer_created", actor=actor, request=request, obj=transfer, lines=len(lines))
    return transfer


def _in_transit(transfer):
    locked = StockTransfer.objects.select_for_update().get(pk=transfer.pk)
    if locked.status != StockTransfer.Status.IN_TRANSIT:
        raise ServiceError("invalid_transfer_state", f"The transfer is already {locked.status}.")
    return locked


@transaction.atomic
def receive_transfer(*, actor, transfer, request=None):
    t = _in_transit(transfer)
    for line in t.lines.select_related("variant__product").order_by("variant_id"):
        move_stock(
            variant=line.variant, warehouse=t.to_warehouse, quantity=line.quantity,
            type=MovementType.TRANSFER_IN, unit_cost=line.unit_cost,
            ref_type="transfer", ref_id=t.pk, note=t.number, actor=actor,
        )
    t.status, t.received_by, t.received_at = StockTransfer.Status.RECEIVED, actor, timezone.now()
    t.save(update_fields=["status", "received_by", "received_at", "updated_at"])
    audit.log("stock.transfer_received", actor=actor, request=request, obj=t)
    return t


@transaction.atomic
def cancel_transfer(*, actor, transfer, request=None):
    """Return the in-transit stock to the source warehouse."""
    t = _in_transit(transfer)
    for line in t.lines.select_related("variant__product").order_by("variant_id"):
        move_stock(
            variant=line.variant, warehouse=t.from_warehouse, quantity=line.quantity,
            type=MovementType.TRANSFER_IN, unit_cost=line.unit_cost,
            ref_type="transfer", ref_id=t.pk, note=f"{t.number} cancelled", actor=actor,
        )
    t.status = StockTransfer.Status.CANCELLED
    t.save(update_fields=["status", "updated_at"])
    audit.log("stock.transfer_cancelled", actor=actor, request=request, obj=t)
    return t


# ---------------------------------------------------------------- warehouses


@transaction.atomic
def save_warehouse(*, actor, instance=None, data, request=None):
    if instance is not None and data.get("is_active") is False and instance.is_active:
        held = StockLevel.objects.filter(warehouse=instance, on_hand__gt=0).exists()
        if held:
            raise ServiceError("warehouse_has_stock", "Move or adjust the stock out before deactivating.")
    if data.get("is_default"):
        Warehouse.objects.filter(is_default=True).exclude(pk=getattr(instance, "pk", None)).update(is_default=False)
    if instance is None:
        instance = Warehouse(**data)
    else:
        for k, v in data.items():
            setattr(instance, k, v)
    instance.save()
    audit.log("warehouse.saved", actor=actor, request=request, obj=instance)
    return instance


def has_stock_history(variant_ids):
    """True if any ledger row or on-hand quantity exists (blocks hard-delete of catalog items)."""
    return (
        StockMovement.objects.filter(variant_id__in=variant_ids).exists()
        or StockLevel.objects.filter(variant_id__in=variant_ids, on_hand__gt=0).exists()
        or StockReservation.objects.filter(variant_id__in=variant_ids, status=ReservationStatus.ACTIVE).exists()
    )


def sellable_quantities(variant_ids):
    """{variant_id: available quantity} counting only active warehouses (what customers can buy)."""
    rows = (
        StockLevel.objects.filter(variant_id__in=variant_ids, warehouse__is_active=True)
        .values("variant_id").annotate(on_hand=Sum("on_hand"), reserved=Sum("reserved"))
    )
    out = {vid: ZERO for vid in variant_ids}
    for r in rows:
        out[r["variant_id"]] = r["on_hand"] - r["reserved"]
    return out
