"""Point-of-sale business rules. Everything that changes data runs in one transaction:
a sale either completes with its stock movements AND its journal entry, or leaves no trace.

Prices are tax-INCLUSIVE retail prices. When a tax rate is chosen, the tax inside the total is
`total * rate / (100 + rate)` and the revenue booked is the rest.

On credit: with `on_credit=True` and a registered customer, whatever the payments do not cover is left on
the customer's account (debit accounts receivable). Customer payments settle the oldest open invoices first.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounting import services as accounting
from apps.catalog.models import ProductPrice, ProductStatus, ProductVariant
from apps.core import audit
from apps.core.exceptions import ServiceError
from apps.core.numbering import next_number
from apps.currencies import services as fx
from apps.inventory import services as stock
from apps.inventory.models import MovementType

from .models import (
    DebtAllocation, DebtPayment, Payment, PaymentMethod, Sale, SaleLine, SaleStatus, Shift, ShiftStatus,
)

ZERO = Decimal("0")
CENT = Decimal("0.01")


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _amount(value, field):
    try:
        out = Decimal(str(value))
    except Exception:
        raise ServiceError("invalid_amount", fields={field: ["Not a valid number."]})
    return out


# ---------------------------------------------------------------- shifts


def current_shift(user):
    return Shift.objects.select_related("warehouse", "cashier").filter(cashier=user, status=ShiftStatus.OPEN).first()


@transaction.atomic
def open_shift(*, actor, warehouse, opening_cash, request=None):
    if not warehouse.is_active:
        raise ServiceError("warehouse_inactive", f"Warehouse {warehouse.code} is inactive.")
    opening = money(_amount(opening_cash, "opening_cash"))
    if opening < 0:
        raise ServiceError("invalid_amount", fields={"opening_cash": ["Cannot be negative."]})
    if current_shift(actor):
        raise ServiceError("shift_already_open", "You already have an open shift.")
    try:
        with transaction.atomic():
            shift = Shift.objects.create(
                number=next_number("shift", "SH-"), cashier=actor, warehouse=warehouse, opening_cash=opening)
    except IntegrityError:  # two requests raced past the check above
        raise ServiceError("shift_already_open", "You already have an open shift.")
    audit.log("pos.shift_opened", actor=actor, request=request, obj=shift, number=shift.number,
              opening_cash=str(opening))
    return shift


def _sum(qs, field="amount"):
    return qs.aggregate(t=Sum(field))["t"] or ZERO


def cash_expected(shift):
    """Opening float + cash taken for this shift's sales and for customers' debts - cash paid back for refunds."""
    taken = _sum(Payment.objects.filter(sale__shift=shift, method=PaymentMethod.CASH))
    debts = _sum(DebtPayment.objects.filter(shift=shift, method=PaymentMethod.CASH))
    paid_back = _sum(Payment.objects.filter(sale__refund_shift=shift, method=PaymentMethod.CASH))
    return money(shift.opening_cash + taken + debts - paid_back)


def shift_report(shift):
    """Totals for the shift summary / Z report."""
    sales = Sale.objects.filter(shift=shift)
    by_method = {m: ZERO for m in PaymentMethod.values}
    for row in Payment.objects.filter(sale__shift=shift).values("method").annotate(t=Sum("amount")):
        by_method[row["method"]] = row["t"]
    refunded = Sale.objects.filter(refund_shift=shift)
    refunds_by_method = {m: ZERO for m in PaymentMethod.values}
    for row in Payment.objects.filter(sale__refund_shift=shift).values("method").annotate(t=Sum("amount")):
        refunds_by_method[row["method"]] = row["t"]
    debts_by_method = {m: ZERO for m in PaymentMethod.values}
    for row in DebtPayment.objects.filter(shift=shift).values("method").annotate(t=Sum("amount")):
        debts_by_method[row["method"]] = row["t"]
    return {
        "sales_count": sales.count(),
        "sales_total": money(_sum(sales, "total")),
        "discount_total": money(_sum(sales, "discount_total")),
        "tax_total": money(_sum(sales, "tax_total")),
        "refunds_count": refunded.count(),
        "refunds_total": money(_sum(refunded, "total")),
        "by_method": {k: money(v) for k, v in by_method.items()},
        "refunds_by_method": {k: money(v) for k, v in refunds_by_method.items()},
        # left on customers' accounts by this shift's sales, and debt payments the till took in
        "credit_total": money(_sum(sales.filter(status=SaleStatus.COMPLETED), "credit_amount")),
        "debt_collected_by_method": {k: money(v) for k, v in debts_by_method.items()},
        "expected_cash": cash_expected(shift) if shift.status == ShiftStatus.OPEN else shift.expected_cash,
    }


@transaction.atomic
def close_shift(*, actor, shift, counted_cash, note="", request=None):
    shift = Shift.objects.select_for_update().get(pk=shift.pk)
    if shift.status != ShiftStatus.OPEN:
        raise ServiceError("shift_not_open", "This shift is already closed.")
    counted = money(_amount(counted_cash, "counted_cash"))
    if counted < 0:
        raise ServiceError("invalid_amount", fields={"counted_cash": ["Cannot be negative."]})
    expected = cash_expected(shift)
    shift.status, shift.closed_at, shift.closed_by = ShiftStatus.CLOSED, timezone.now(), actor
    shift.counted_cash, shift.expected_cash, shift.difference = counted, expected, counted - expected
    shift.close_note = (note or "")[:255]
    shift.save()
    audit.log("pos.shift_closed", actor=actor, request=request, obj=shift, number=shift.number,
              expected=str(expected), counted=str(counted), difference=str(shift.difference))
    return shift


# ---------------------------------------------------------------- selling


def pos_price(variant):
    """Retail price from the default price list of the base currency, or None when the item is unpriced."""
    row = (
        ProductPrice.objects.filter(variant=variant, price_list__is_default=True, price_list__is_active=True,
                                    price_list__currency__is_base=True)
        .select_related("price_list").first()
    )
    return row.price if row else None


def _check_sellable(variant):
    product = variant.product
    if variant.deleted_at or product.deleted_at or not variant.is_active or product.status != ProductStatus.ACTIVE:
        raise ServiceError("not_sellable", f"{variant.sku} is not available for sale.", fields={"sku": [variant.sku]})


def _build_lines(lines):
    if not lines:
        raise ServiceError("no_lines", "A sale needs at least one line.")
    built = []
    for i, ln in enumerate(lines, start=1):
        variant = ProductVariant.objects.select_related("product").get(pk=ln["variant"].pk)
        _check_sellable(variant)
        qty = _amount(ln["quantity"], "quantity")
        if qty <= 0:
            raise ServiceError("invalid_quantity", fields={"lines": {str(i): {"quantity": ["Must be greater than zero."]}}})
        price = pos_price(variant)
        if price is None:
            raise ServiceError("no_price", f"{variant.sku} has no retail price.", fields={"sku": [variant.sku]})
        price = money(price)
        gross = money(qty * price)
        discount = money(_amount(ln.get("discount") or 0, "discount"))
        if discount < 0 or discount > gross:
            raise ServiceError("discount_too_large", f"The discount on {variant.sku} exceeds the line total.",
                               fields={"lines": {str(i): {"discount": ["Must be between 0 and the line total."]}}})
        built.append({"line_no": i, "variant": variant, "qty": qty, "price": price, "gross": gross, "discount": discount})
    return built


def _split_payments(total, payments, on_credit=False):
    """Validate tender; return (applied rows, cash_tendered, change, credit). Change comes out of cash only.
    On credit, a shortfall is not an error: it becomes the amount left on the customer's account."""
    if not payments and not on_credit:
        raise ServiceError("payment_insufficient", "No payment was given.",
                           fields={"total": [str(total)], "paid": ["0.00"]})
    rows = []
    for p in payments:
        method = p["method"]
        if method not in PaymentMethod.values:
            raise ServiceError("invalid_payment_method", fields={"payments": [f"Unknown method: {method}."]})
        amount = money(_amount(p["amount"], "amount"))
        if amount <= 0:
            raise ServiceError("invalid_amount", fields={"payments": ["Payment amounts must be greater than zero."]})
        rows.append({"method": method, "amount": amount, "reference": (p.get("reference") or "")[:64]})
    paid = sum((r["amount"] for r in rows), ZERO)
    if paid < total and on_credit:
        return rows, sum((r["amount"] for r in rows if r["method"] == PaymentMethod.CASH), ZERO), ZERO, total - paid
    if paid < total:
        raise ServiceError("payment_insufficient", "The payments do not cover the total.",
                           fields={"total": [str(total)], "paid": [str(paid)]})
    over = paid - total
    cash_tendered = sum((r["amount"] for r in rows if r["method"] == PaymentMethod.CASH), ZERO)
    if over > cash_tendered:
        raise ServiceError("change_exceeds_cash", "Change can only be given from cash.",
                           fields={"change": [str(over)], "cash": [str(cash_tendered)]})
    for r in reversed(rows):  # take the change out of the cash rows, last first
        if over <= 0:
            break
        if r["method"] == PaymentMethod.CASH:
            take = min(r["amount"], over)
            r["amount"] -= take
            over -= take
    applied = [r for r in rows if r["amount"] > 0]
    return applied, cash_tendered, paid - total, ZERO


def _sale_entry_lines(sale, applied, built, tax_rate):
    cash = sum((p["amount"] for p in applied if p["method"] == PaymentMethod.CASH), ZERO)
    bank = sum((p["amount"] for p in applied if p["method"] != PaymentMethod.CASH), ZERO)
    memo = f"POS {sale.number}"
    out = []
    if cash:
        out.append({"account": accounting.mapped_account("cash"), "side": "debit", "amount": cash, "description": memo})
    if bank:
        out.append({"account": accounting.mapped_account("bank"), "side": "debit", "amount": bank, "description": memo})
    if sale.credit_amount:
        out.append({"account": accounting.mapped_account("receivable"), "side": "debit", "amount": sale.credit_amount,
                    "description": f"{memo} · {sale.customer.full_name}"[:255]})
    out.append({"account": accounting.mapped_account("sales"), "side": "credit", "amount": sale.net_total, "description": memo})
    if sale.tax_total > 0:
        out.append({"account": tax_rate.collected_account, "side": "credit", "amount": sale.tax_total, "description": memo})
    if sale.cost_total > 0:
        out.append({"account": accounting.mapped_account("cogs"), "side": "debit", "amount": sale.cost_total,
                    "description": memo})
        out.append({"account": accounting.mapped_account("inventory"), "side": "credit", "amount": sale.cost_total,
                    "description": memo})
    return out


@transaction.atomic
def create_sale(*, actor, shift, lines, payments, customer=None, tax_rate=None, note="", on_credit=False,
                request=None):
    shift = Shift.objects.select_for_update().select_related("warehouse").get(pk=shift.pk)
    if shift.status != ShiftStatus.OPEN:
        raise ServiceError("shift_not_open", "Open a shift before selling.")
    if tax_rate is not None and not tax_rate.is_active:
        raise ServiceError("tax_inactive", f"{tax_rate.name} is inactive.")
    fx.get_base_currency()

    built = _build_lines(lines)
    subtotal = sum((b["gross"] for b in built), ZERO)
    discount_total = sum((b["discount"] for b in built), ZERO)
    total = subtotal - discount_total
    if total <= 0:
        raise ServiceError("empty_sale", "The sale total must be greater than zero.")
    tax_total = money(total * tax_rate.rate / (Decimal(100) + tax_rate.rate)) if tax_rate else ZERO
    if on_credit and customer is None:
        raise ServiceError("customer_required", "Choose the customer the debt is recorded on.",
                           fields={"customer": ["Required to sell on credit."]})
    applied, cash_tendered, change, credit = _split_payments(total, payments, on_credit=on_credit)

    sale = Sale.objects.create(
        number=next_number("pos_sale", "POS-"), shift=shift, warehouse=shift.warehouse, customer=customer,
        subtotal=subtotal, discount_total=discount_total, total=total,
        tax_name=tax_rate.name if tax_rate else "", tax_rate=tax_rate.rate if tax_rate else None,
        tax_total=tax_total, net_total=total - tax_total,
        cash_tendered=cash_tendered, change_due=change, credit_amount=credit, balance_due=credit,
        note=(note or "")[:255], created_by=actor,
    )

    # Stock first, in variant order (the inventory module's lock order), then price the cost from the ledger.
    costs = {}
    for b in sorted(built, key=lambda x: x["variant"].pk):
        if b["variant"].product.track_inventory:
            mv = stock.move_stock(variant=b["variant"], warehouse=shift.warehouse, quantity=-b["qty"],
                                  type=MovementType.SALE_OUT, ref_type="pos_sale", ref_id=sale.number,
                                  note=f"POS {sale.number}", actor=actor)
            costs[b["line_no"]] = mv.unit_cost
    cost_total = ZERO
    for b in built:
        unit_cost = costs.get(b["line_no"], ZERO)
        cost_total += money(b["qty"] * unit_cost)
        SaleLine.objects.create(
            sale=sale, line_no=b["line_no"], variant=b["variant"], sku=b["variant"].sku,
            name=" · ".join(x for x in (b["variant"].product.name, b["variant"].name) if x)[:255],
            quantity=b["qty"], unit_price=b["price"], discount=b["discount"], line_total=b["gross"] - b["discount"],
            unit_cost=unit_cost, tracked=b["line_no"] in costs,
        )
    sale.cost_total = cost_total
    Payment.objects.bulk_create([Payment(sale=sale, **p) for p in applied])

    entry = accounting.create_posted_entry(
        actor=actor, date=timezone.localdate(), memo=f"POS {sale.number}",
        lines=_sale_entry_lines(sale, applied, built, tax_rate), source_type="pos_sale", source_id=sale.number,
        request=request)
    sale.entry = entry
    sale.save()
    audit.log("pos.sale_completed", actor=actor, request=request, obj=sale, number=sale.number,
              total=str(total), lines=len(built), **({"credit": str(credit)} if credit else {}))
    return sale


@transaction.atomic
def refund_sale(*, actor, sale, reason, request=None):
    """Full refund: stock goes back, the journal entry is reversed, the money is returned by the original methods
    out of the refunding cashier's open shift."""
    sale = Sale.objects.select_for_update().select_related("entry", "warehouse").get(pk=sale.pk)
    if not (reason or "").strip():
        raise ServiceError("reason_required", "A reason is required.", fields={"reason": ["This field is required."]})
    if sale.status == SaleStatus.REFUNDED:
        raise ServiceError("sale_already_refunded", f"{sale.number} was already refunded.")
    if sale.allocations.exists():
        # the customer already paid part of this debt: settle that by hand before undoing the invoice
        raise ServiceError("sale_has_debt_payments",
                           f"{sale.number} was partly paid off by the customer and cannot be refunded here.")
    shift = current_shift(actor)
    if shift is None:
        raise ServiceError("shift_required", "Open a shift to process a refund.")

    lines = list(sale.lines.select_related("variant__product").order_by("variant_id"))
    for ln in lines:
        if ln.tracked:
            stock.move_stock(variant=ln.variant, warehouse=sale.warehouse, quantity=ln.quantity,
                             type=MovementType.RETURN_IN, unit_cost=ln.unit_cost, ref_type="pos_refund",
                             ref_id=sale.number, note=f"Refund {sale.number}", actor=actor)
    reversal = accounting.reverse_entry(actor=actor, entry=sale.entry, reason=reason.strip(),
                                        date=timezone.localdate(), request=request)
    sale.status, sale.refunded_at, sale.refunded_by = SaleStatus.REFUNDED, timezone.now(), actor
    sale.refund_reason, sale.refund_shift, sale.refund_entry = reason.strip()[:255], shift, reversal
    sale.balance_due = ZERO  # the reversal also takes the debt off the customer's account
    sale.save()
    audit.log("pos.sale_refunded", actor=actor, request=request, obj=sale, number=sale.number,
              reason=reason.strip(), total=str(sale.total))
    return sale


# ---------------------------------------------------------------- customer debts


def open_invoices(customer):
    return Sale.objects.filter(customer=customer, status=SaleStatus.COMPLETED, balance_due__gt=0)


def customer_balance(customer):
    """What the customer owes the shop now."""
    return money(_sum(open_invoices(customer), "balance_due"))


@transaction.atomic
def receive_debt_payment(*, actor, customer, amount, method, reference="", note="", request=None):
    """The customer pays (part of) what they owe: the money settles the oldest open invoices first and is booked
    debit cash/bank, credit accounts receivable. Cash needs an open shift so the till count includes it."""
    amount = money(_amount(amount, "amount"))
    if amount <= 0:
        raise ServiceError("invalid_amount", fields={"amount": ["Must be greater than zero."]})
    if method not in PaymentMethod.values:
        raise ServiceError("invalid_payment_method", fields={"method": [f"Unknown method: {method}."]})
    shift = current_shift(actor)
    if method == PaymentMethod.CASH and shift is None:
        raise ServiceError("shift_required", "Open a shift at the till to take cash.")
    invoices = list(open_invoices(customer).select_for_update().order_by("created_at", "id"))
    owed = sum((s.balance_due for s in invoices), ZERO)
    if owed <= 0:
        raise ServiceError("no_debt", "This customer owes nothing.")
    if amount > owed:
        raise ServiceError("payment_exceeds_debt", "The payment is more than the customer owes.",
                           fields={"amount": [str(amount)], "owed": [str(owed)]})

    payment = DebtPayment.objects.create(
        number=next_number("debt_payment", "RCV-"), customer=customer, amount=amount, method=method,
        reference=(reference or "")[:64], note=(note or "")[:255], shift=shift, created_by=actor)
    left = amount
    for sale in invoices:
        if left <= 0:
            break
        take = min(sale.balance_due, left)
        DebtAllocation.objects.create(payment=payment, sale=sale, amount=take)
        sale.balance_due -= take
        sale.save(update_fields=["balance_due", "updated_at"])
        left -= take

    memo = f"{payment.number} · {customer.full_name}"[:255]
    payment.entry = accounting.create_posted_entry(
        actor=actor, date=timezone.localdate(), memo=memo, source_type="debt_payment", source_id=payment.number,
        lines=[
            {"account": accounting.mapped_account("cash" if method == PaymentMethod.CASH else "bank"), "side": "debit",
             "amount": amount, "description": memo},
            {"account": accounting.mapped_account("receivable"), "side": "credit", "amount": amount, "description": memo},
        ],
        request=request)
    payment.save(update_fields=["entry", "updated_at"])
    audit.log("customer.debt_paid", actor=actor, request=request, obj=customer, number=payment.number,
              amount=str(amount), method=method, left=str(owed - amount))
    return payment
