"""Sales reports for the point of sale (read-only, base currency).

Scope: sales CREATED between the two dates (inclusive, project time zone). Only `completed` sales count towards
revenue; refunded sales are reported separately and excluded from the totals. Revenue is the net of tax
(`net_total`); `total` is what customers paid. Cost and profit are only filled for users allowed to see costs.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Count, Sum
from django.db.models.functions import ExtractHour, TruncDate

from .models import Payment, Sale, SaleLine, SaleStatus

ZERO = Decimal("0")
CENT = Decimal("0.01")
MAX_DAYS = 732


def _m(value):
    return str((value or ZERO).quantize(CENT, rounding=ROUND_HALF_UP))


def _q(value):
    """Quantity without trailing zeros: 3.000 -> "3", 10.000 -> "10", 1.500 -> "1.5"."""
    return format(Decimal(value or 0).normalize(), "f")


def _pct(part, whole):
    return None if not whole else str((part * 100 / whole).quantize(CENT, rounding=ROUND_HALF_UP))


def sales_report(*, date_from, date_to, cashier=None, warehouse=None, with_cost=False):
    sales = Sale.objects.filter(created_at__date__gte=date_from, created_at__date__lte=date_to)
    if cashier is not None:
        sales = sales.filter(created_by=cashier)
    if warehouse is not None:
        sales = sales.filter(warehouse=warehouse)
    completed = sales.filter(status=SaleStatus.COMPLETED)
    refunded = sales.filter(status=SaleStatus.REFUNDED)

    # ---- summary
    agg = completed.aggregate(
        count=Count("id"), gross=Sum("subtotal"), discounts=Sum("discount_total"), total=Sum("total"),
        tax=Sum("tax_total"), net=Sum("net_total"), cost=Sum("cost_total"))
    count = agg["count"] or 0
    total, net, cost = agg["total"] or ZERO, agg["net"] or ZERO, agg["cost"] or ZERO
    items = SaleLine.objects.filter(sale__in=completed).aggregate(q=Sum("quantity"))["q"] or ZERO
    ref = refunded.aggregate(count=Count("id"), total=Sum("total"))
    profit = net - cost
    summary = {
        "sales_count": count,
        "items_sold": _q(items),
        "gross": _m(agg["gross"]),
        "discounts": _m(agg["discounts"]),
        "total": _m(total),
        "tax": _m(agg["tax"]),
        "net": _m(net),
        "avg_ticket": _m(total / count) if count else "0.00",
        "refunds_count": ref["count"] or 0,
        "refunds_total": _m(ref["total"]),
        "cost": _m(cost) if with_cost else None,
        "profit": _m(profit) if with_cost else None,
        "margin_pct": _pct(profit, net) if with_cost else None,
    }

    # ---- per day (every day of the range, zeros included, so charts have no holes)
    per_day = {r["day"]: r for r in completed.annotate(day=TruncDate("created_at")).values("day").annotate(
        n=Count("id"), total=Sum("total"), net=Sum("net_total"), discounts=Sum("discount_total"),
        tax=Sum("tax_total"), cost=Sum("cost_total"))}
    refunds_per_day = {r["day"]: r for r in refunded.annotate(day=TruncDate("created_at")).values("day").annotate(
        n=Count("id"), total=Sum("total"))}
    daily, day = [], date_from
    while day <= date_to:
        r, rf = per_day.get(day), refunds_per_day.get(day)
        daily.append({
            "date": day.isoformat(),
            "sales_count": r["n"] if r else 0,
            "total": _m(r["total"]) if r else "0.00",
            "net": _m(r["net"]) if r else "0.00",
            "discounts": _m(r["discounts"]) if r else "0.00",
            "tax": _m(r["tax"]) if r else "0.00",
            "refunds_count": rf["n"] if rf else 0,
            "refunds_total": _m(rf["total"]) if rf else "0.00",
            "profit": _m((r["net"] or ZERO) - (r["cost"] or ZERO)) if (r and with_cost) else ("0.00" if with_cost else None),
        })
        day += timedelta(days=1)

    # ---- per hour of day (all 24)
    per_hour = {r["h"]: r for r in completed.annotate(h=ExtractHour("created_at")).values("h").annotate(
        n=Count("id"), total=Sum("total"))}
    hourly = [{"hour": h, "sales_count": per_hour[h]["n"] if h in per_hour else 0,
               "total": _m(per_hour[h]["total"]) if h in per_hour else "0.00"} for h in range(24)]

    # ---- payments (what was applied; change is not a payment)
    payments = [{"method": r["method"], "count": r["n"], "amount": _m(r["amount"])} for r in
                Payment.objects.filter(sale__in=completed).values("method").annotate(n=Count("id"), amount=Sum("amount"))
                .order_by("-amount")]

    # ---- per cashier
    cashiers = []
    for r in completed.values("created_by__uuid", "created_by__full_name").annotate(
            n=Count("id"), total=Sum("total"), discounts=Sum("discount_total")).order_by("-total"):
        cashiers.append({
            "uuid": str(r["created_by__uuid"]) if r["created_by__uuid"] else None,
            "name": r["created_by__full_name"] or "",
            "sales_count": r["n"],
            "total": _m(r["total"]),
            "avg_ticket": _m(r["total"] / r["n"]),
            "discounts": _m(r["discounts"]),
        })

    # ---- per product and per category: net revenue (the sale's tax share removed) and cost, in Python
    products, categories = {}, defaultdict(lambda: {"quantity": ZERO, "revenue": ZERO, "cost": ZERO, "orders": set()})
    lines = SaleLine.objects.filter(sale__in=completed).values(
        "variant__uuid", "sku", "name", "quantity", "line_total", "discount", "unit_cost", "sale_id",
        "sale__net_total", "sale__total", "variant__product__category__name")
    for ln in lines.iterator():
        share = (ln["sale__net_total"] / ln["sale__total"]) if ln["sale__total"] else Decimal(1)
        revenue = ln["line_total"] * share
        line_cost = ln["quantity"] * ln["unit_cost"]
        key = ln["variant__uuid"]
        p = products.setdefault(key, {"uuid": str(key), "sku": ln["sku"], "name": ln["name"], "quantity": ZERO,
                                      "revenue": ZERO, "discount": ZERO, "cost": ZERO, "orders": set()})
        p["quantity"] += ln["quantity"]
        p["revenue"] += revenue
        p["discount"] += ln["discount"]
        p["cost"] += line_cost
        p["orders"].add(ln["sale_id"])
        c = categories[ln["variant__product__category__name"] or ""]
        c["quantity"] += ln["quantity"]
        c["revenue"] += revenue
        c["cost"] += line_cost
        c["orders"].add(ln["sale_id"])

    def product_row(p):
        row = {"uuid": p["uuid"], "sku": p["sku"], "name": p["name"], "quantity": _q(p["quantity"]),
               "orders": len(p["orders"]), "discount": _m(p["discount"]), "revenue": _m(p["revenue"]),
               "cost": None, "profit": None, "margin_pct": None}
        if with_cost:
            row.update(cost=_m(p["cost"]), profit=_m(p["revenue"] - p["cost"]), margin_pct=_pct(p["revenue"] - p["cost"], p["revenue"]))
        return row

    product_rows = [product_row(p) for p in sorted(products.values(), key=lambda p: p["revenue"], reverse=True)]
    category_rows = []
    for name, c in sorted(categories.items(), key=lambda kv: kv[1]["revenue"], reverse=True):
        category_rows.append({
            "name": name, "quantity": _q(c["quantity"]), "orders": len(c["orders"]), "revenue": _m(c["revenue"]),
            "cost": _m(c["cost"]) if with_cost else None,
            "profit": _m(c["revenue"] - c["cost"]) if with_cost else None,
        })

    return {
        "from": date_from.isoformat(), "to": date_to.isoformat(), "currency": "ILS", "cost_visible": with_cost,
        "summary": summary, "daily": daily, "hourly": hourly, "payments": payments, "cashiers": cashiers,
        "categories": category_rows, "products": product_rows,
    }


def sales_list(*, date_from, date_to, cashier=None, warehouse=None):
    """One row per sale in the range (completed and refunded), oldest first: the CSV 'invoices' section."""
    qs = Sale.objects.filter(created_at__date__gte=date_from, created_at__date__lte=date_to).select_related(
        "created_by", "shift", "warehouse", "customer").prefetch_related("payments").order_by("created_at")
    if cashier is not None:
        qs = qs.filter(created_by=cashier)
    if warehouse is not None:
        qs = qs.filter(warehouse=warehouse)
    return qs
