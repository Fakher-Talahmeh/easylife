import csv
from datetime import date as date_cls

from django.db.models import F, Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.accounting.models import TaxRate
from apps.accounts import rbac
from apps.accounts.models import User, UserType
from apps.accounts.rbac import HasPermission
from apps.catalog.models import Category, ProductImage, ProductStatus, ProductVariant
from apps.core.exceptions import ServiceError
from apps.inventory.models import StockLevel, Warehouse
from apps.sales import reports, services
from apps.sales.models import DebtPayment, Sale, SaleStatus, Shift

from . import serializers as s


class PosPagination(PageNumberPagination):
    page_size = 24
    page_size_query_param = "page_size"
    max_page_size = 100


def _descendant_ids(category):
    ids, frontier = {category.pk}, [category.pk]
    while frontier:
        children = list(Category.objects.filter(parent_id__in=frontier, is_active=True).values_list("pk", flat=True))
        frontier = [c for c in children if c not in ids]
        ids.update(frontier)
    return ids


class ConfigView(APIView):
    """Everything the till needs to start: current shift, warehouses, categories, tax rates, what I may do."""

    permission_classes = [HasPermission]
    required_permission = "sales.pos"

    def get(self, request):
        user = request.user
        shift = services.current_shift(user)
        return Response({
            "currency": "ILS",
            "shift": s.ShiftSerializer(shift).data if shift else None,
            "warehouses": s.WarehouseRefSerializer(Warehouse.objects.filter(is_active=True), many=True).data,
            "default_warehouse": next((str(w.uuid) for w in Warehouse.objects.filter(is_active=True, is_default=True)), None),
            "categories": [{"uuid": str(c.uuid), "name": c.name, "parent": str(c.parent.uuid) if c.parent_id else None}
                           for c in Category.objects.filter(is_active=True).select_related("parent")
                           .order_by("sort_order", "name")],
            "tax_rates": [{"uuid": str(t.uuid), "name": t.name, "rate": str(t.rate)}
                          for t in TaxRate.objects.filter(is_active=True).order_by("name")],
            "can_discount": rbac.has_permission(user, "sales.discount"),
            "can_refund": rbac.has_permission(user, "sales.refund"),
            "can_credit": rbac.has_permission(user, "sales.credit"),
        })


class ProductSearchView(APIView):
    """Sellable items for the till: active products with a retail price. `barcode` is an exact scan match."""

    permission_classes = [HasPermission]
    required_permission = "sales.pos"

    def get(self, request):
        p = request.query_params
        qs = (
            ProductVariant.objects.filter(
                deleted_at__isnull=True, is_active=True, product__deleted_at__isnull=True,
                product__status=ProductStatus.ACTIVE,
                prices__price_list__is_default=True, prices__price_list__is_active=True,
                prices__price_list__currency__is_base=True,
            )
            .select_related("product__category", "product__brand").annotate(price=F("prices__price")).order_by("product__name", "sku")
        )
        if p.get("barcode"):
            qs = qs.filter(barcode=p["barcode"].strip())
        if p.get("q"):
            for term in p["q"].split():
                qs = qs.filter(Q(sku__icontains=term) | Q(barcode__icontains=term) | Q(name__icontains=term)
                               | Q(product__name__icontains=term) | Q(product__brand__name__icontains=term))
        if p.get("category"):
            category = Category.objects.filter(uuid=p["category"]).first()
            qs = qs.filter(product__category_id__in=_descendant_ids(category)) if category else qs.none()

        paginator = PosPagination()
        page = paginator.paginate_queryset(qs, request)

        warehouse = (Warehouse.objects.filter(uuid=p["warehouse"]).first() if p.get("warehouse")
                     else Warehouse.objects.filter(is_default=True).first())
        levels = {}
        if warehouse and page:
            for lv in StockLevel.objects.filter(warehouse=warehouse, variant_id__in=[v.pk for v in page]):
                levels[lv.variant_id] = lv.on_hand - lv.reserved
        images = {}
        for img in ProductImage.objects.filter(product_id__in={v.product_id for v in page}, is_primary=True):
            images[img.product_id] = request.build_absolute_uri(img.image.url)

        results = [{
            "uuid": str(v.uuid), "sku": v.sku, "barcode": v.barcode, "name": v.name, "product": v.product.name,
            "brand": v.product.brand.name if v.product.brand_id else "", "category": v.product.category.name if v.product.category_id else None,
            "image": images.get(v.product_id), "price": str(services.money(v.price)), "track_inventory": v.product.track_inventory,
            "available": str(levels.get(v.pk, 0)) if v.product.track_inventory else None,
        } for v in page]
        return paginator.get_paginated_response(results)


class ShiftCurrentView(APIView):
    permission_classes = [HasPermission]
    required_permission = "sales.pos"

    def get(self, request):
        shift = services.current_shift(request.user)
        if shift is None:
            return Response({"shift": None, "report": None})
        return Response({"shift": s.ShiftSerializer(shift).data, "report": _report(shift)})


def _stringify(value):
    if isinstance(value, dict):
        return {k: _stringify(v) for k, v in value.items()}
    if isinstance(value, int) or value is None:
        return value
    return str(value)


def _report(shift):
    return _stringify(services.shift_report(shift))


class ShiftOpenView(APIView):
    permission_classes = [HasPermission]
    required_permission = "sales.pos"

    def post(self, request):
        ser = s.OpenShiftSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        shift = services.open_shift(actor=request.user, warehouse=ser.validated_data["warehouse"],
                                    opening_cash=ser.validated_data["opening_cash"], request=request)
        return Response(s.ShiftSerializer(shift).data, status=status.HTTP_201_CREATED)


class ShiftCloseView(APIView):
    permission_classes = [HasPermission]
    required_permission = "sales.pos"

    def post(self, request):
        shift = services.current_shift(request.user)
        if shift is None:
            raise ServiceError("shift_not_open", "You have no open shift.")
        ser = s.CloseShiftSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        closed = services.close_shift(actor=request.user, shift=shift, counted_cash=ser.validated_data["counted_cash"],
                                      note=ser.validated_data["note"], request=request)
        return Response({"shift": s.ShiftSerializer(closed).data, "report": _report(closed)})


class ShiftViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Shift history for managers (read-only)."""

    permission_classes = [HasPermission]
    serializer_class = s.ShiftSerializer
    lookup_field = "uuid"
    required_permission = {"list": "sales.view", "retrieve": "sales.view"}

    def get_queryset(self):
        qs = Shift.objects.select_related("cashier", "warehouse")
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("cashier"):
            qs = qs.filter(cashier__uuid=p["cashier"])
        return qs

    def retrieve(self, request, *args, **kwargs):
        shift = self.get_object()
        return Response({"shift": self.get_serializer(shift).data, "report": _report(shift)})


class SaleViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    permission_classes = [HasPermission]
    lookup_field = "uuid"
    required_permission = {
        "list": ("sales.view", "sales.pos"), "retrieve": ("sales.view", "sales.pos"),
        "create": "sales.pos", "refund": "sales.refund",
    }

    def get_serializer_class(self):
        return s.SaleListSerializer if self.action == "list" else s.SaleSerializer

    def get_queryset(self):
        qs = (Sale.objects.select_related("shift", "warehouse", "customer", "created_by", "refunded_by", "entry")
              .prefetch_related("lines", "payments", "allocations__payment"))
        if not rbac.has_permission(self.request.user, "sales.view"):
            qs = qs.filter(created_by=self.request.user)  # a cashier sees only their own sales
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("on_credit") in ("1", "true"):
            qs = qs.filter(credit_amount__gt=0)
        if p.get("shift"):
            qs = qs.filter(shift__uuid=p["shift"])
        if p.get("from"):
            qs = qs.filter(created_at__date__gte=p["from"])
        if p.get("to"):
            qs = qs.filter(created_at__date__lte=p["to"])
        if p.get("search"):
            qs = qs.filter(Q(number__icontains=p["search"]) | Q(customer__full_name__icontains=p["search"])
                           | Q(customer__phone__icontains=p["search"]))
        return qs

    def create(self, request, *args, **kwargs):
        ser = s.CreateSaleSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        if any(ln["discount"] > 0 for ln in data["lines"]) and not rbac.has_permission(request.user, "sales.discount"):
            raise ServiceError("discount_not_allowed", "You are not allowed to give discounts.", status_code=403)
        if data["on_credit"] and not rbac.has_permission(request.user, "sales.credit"):
            raise ServiceError("credit_not_allowed", "You are not allowed to sell on credit.", status_code=403)
        customer = None
        if data.get("customer"):
            customer = User.objects.filter(uuid=data["customer"], user_type=UserType.CUSTOMER, is_active=True).first()
            if customer is None:
                raise ServiceError("customer_not_found", "Customer not found.", status_code=404)
        shift = services.current_shift(request.user)
        if shift is None:
            raise ServiceError("shift_required", "Open a shift before selling.")
        sale = services.create_sale(
            actor=request.user, shift=shift, lines=data["lines"], payments=data["payments"], customer=customer,
            tax_rate=data.get("tax_rate"), note=data["note"], on_credit=data["on_credit"], request=request)
        return Response(s.SaleSerializer(self.get_queryset().get(pk=sale.pk)).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def refund(self, request, uuid=None):
        ser = s.RefundSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        sale = services.refund_sale(actor=request.user, sale=self.get_object(), reason=ser.validated_data["reason"],
                                    request=request)
        return Response(s.SaleSerializer(self.get_queryset().get(pk=sale.pk)).data)


# ================================================================== sales report

REPORT_SECTIONS = ("daily", "products", "categories", "cashiers", "payments", "invoices")


def _date_param(request, name, default):
    raw = request.query_params.get(name)
    if not raw:
        return default
    try:
        return date_cls.fromisoformat(raw)
    except ValueError:
        raise ServiceError("invalid_date", f"'{name}' must be YYYY-MM-DD.", fields={name: [raw]})


class SalesReportView(APIView):
    """Sales between two dates: totals, per day/hour, payments, cashiers, categories and every product sold."""

    permission_classes = [HasPermission]
    required_permission = "reports.sales"

    def get(self, request):
        today = timezone.localdate()
        date_from = _date_param(request, "from", today.replace(day=1))
        date_to = _date_param(request, "to", today)
        if date_from > date_to:
            raise ServiceError("invalid_range", "'from' must not be after 'to'.", fields={"from": [str(date_from)]})
        if (date_to - date_from).days + 1 > reports.MAX_DAYS:
            raise ServiceError("range_too_large", f"The range is limited to {reports.MAX_DAYS} days.")
        p = request.query_params
        cashier = warehouse = None
        if p.get("cashier"):
            cashier = User.objects.filter(uuid=p["cashier"], user_type=UserType.STAFF).first()
            if cashier is None:
                raise ServiceError("not_found", "Cashier not found.", status_code=404)
        if p.get("warehouse"):
            warehouse = Warehouse.objects.filter(uuid=p["warehouse"]).first()
            if warehouse is None:
                raise ServiceError("not_found", "Warehouse not found.", status_code=404)
        with_cost = rbac.has_permission(request.user, "inventory.view_cost")
        data = reports.sales_report(date_from=date_from, date_to=date_to, cashier=cashier, warehouse=warehouse,
                                    with_cost=with_cost)
        data["filters"] = {"cashier": str(cashier.uuid) if cashier else None,
                           "warehouse": str(warehouse.uuid) if warehouse else None}
        export = p.get("export")
        if not export:
            return Response(data)
        if export != "csv":
            raise ServiceError("export_not_supported", "Only ?export=csv is supported.")
        if not rbac.has_permission(request.user, "reports.export"):
            raise ServiceError("forbidden", "Missing reports.export.", status_code=403)
        section = p.get("section", "daily")
        if section not in REPORT_SECTIONS:
            raise ServiceError("export_not_supported", f"section must be one of {', '.join(REPORT_SECTIONS)}.")
        header, rows = _csv_section(section, data, with_cost, date_from, date_to, cashier, warehouse)
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="sales-{section}-{date_from}-{date_to}.csv"'
        response.write("﻿")  # BOM so Excel opens Arabic/Hebrew correctly
        writer = csv.writer(response)
        writer.writerow(header)
        writer.writerows(rows)
        return response


def _csv_section(section, data, with_cost, date_from, date_to, cashier, warehouse):
    if section == "daily":
        header = ["date", "sales", "total", "net", "discounts", "tax", "refunds", "refunds_total"] + (["profit"] if with_cost else [])
        rows = [[d["date"], d["sales_count"], d["total"], d["net"], d["discounts"], d["tax"], d["refunds_count"],
                 d["refunds_total"]] + ([d["profit"]] if with_cost else []) for d in data["daily"]]
    elif section == "products":
        header = ["sku", "product", "quantity", "orders", "discount", "net_revenue"] + (["cost", "profit", "margin_pct"] if with_cost else [])
        rows = [[r["sku"], r["name"], r["quantity"], r["orders"], r["discount"], r["revenue"]]
                + ([r["cost"], r["profit"], r["margin_pct"]] if with_cost else []) for r in data["products"]]
    elif section == "categories":
        header = ["category", "quantity", "orders", "net_revenue"] + (["cost", "profit"] if with_cost else [])
        rows = [[r["name"], r["quantity"], r["orders"], r["revenue"]] + ([r["cost"], r["profit"]] if with_cost else [])
                for r in data["categories"]]
    elif section == "cashiers":
        header = ["cashier", "sales", "total", "avg_ticket", "discounts"]
        rows = [[r["name"], r["sales_count"], r["total"], r["avg_ticket"], r["discounts"]] for r in data["cashiers"]]
    elif section == "payments":
        header = ["method", "payments", "amount"]
        rows = [[r["method"], r["count"], r["amount"]] for r in data["payments"]]
    else:  # invoices
        header = ["number", "date", "status", "cashier", "shift", "warehouse", "subtotal", "discount", "total", "tax",
                  "net", "payments"]
        rows = [[s.number, timezone.localtime(s.created_at).strftime("%Y-%m-%d %H:%M"), s.status,
                 s.created_by.full_name if s.created_by else "", s.shift.number, s.warehouse.code, s.subtotal,
                 s.discount_total, s.total, s.tax_total, s.net_total,
                 "; ".join(f"{p.method}:{p.amount}" for p in s.payments.all())]
                for s in reports.sales_list(date_from=date_from, date_to=date_to, cashier=cashier, warehouse=warehouse)]
    return header, rows


# ================================================================== customer debts (back office)


def _customer(uuid):
    customer = User.objects.filter(uuid=uuid, user_type=UserType.CUSTOMER).first()
    if customer is None:
        raise ServiceError("not_found", "Customer not found.", status_code=404)
    return customer


def _invoices(customer, request):
    qs = (Sale.objects.filter(customer=customer).prefetch_related("lines")
          .order_by("-created_at", "-id"))
    if request.query_params.get("open") in ("1", "true"):
        qs = qs.filter(status=SaleStatus.COMPLETED, balance_due__gt=0)
    return qs


def _account_summary(customer):
    sales = Sale.objects.filter(customer=customer, status=SaleStatus.COMPLETED)
    return {
        "balance": str(services.customer_balance(customer)),
        "open_invoices": services.open_invoices(customer).count(),
        "invoices": sales.count(),
        "total_purchases": str(services.money(services._sum(sales, "total"))),
        "total_paid_off": str(services.money(services._sum(DebtPayment.objects.filter(customer=customer)))),
    }


class CustomerAccountView(APIView):
    """A customer's file in the back office: who they are and what they owe."""

    permission_classes = [HasPermission]
    required_permission = "customers.view_balance"

    def get(self, request, uuid):
        customer = _customer(uuid)
        return Response({
            "customer": {"uuid": str(customer.uuid), "full_name": customer.full_name, "phone": customer.phone,
                         "is_active": customer.is_active,
                         "created_at": serializers.DateTimeField().to_representation(customer.created_at)},
            **_account_summary(customer),
        })


class CustomerInvoicesView(APIView):
    """Every invoice of the customer (`?open=1`: only those still owing money), newest first."""

    permission_classes = [HasPermission]
    required_permission = "customers.view_balance"

    def get(self, request, uuid):
        paginator = PosPagination()
        page = paginator.paginate_queryset(_invoices(_customer(uuid), request), request)
        return paginator.get_paginated_response(s.SaleListSerializer(page, many=True).data)


class CustomerDebtPaymentsView(APIView):
    """GET: the customer's payments towards their debt. POST: record a new one (settles the oldest invoices)."""

    permission_classes = [HasPermission]

    @property
    def required_permission(self):
        return "payments.record" if self.request.method == "POST" else "customers.view_balance"

    def get(self, request, uuid):
        qs = (DebtPayment.objects.filter(customer=_customer(uuid)).select_related("created_by")
              .prefetch_related("allocations__sale"))
        paginator = PosPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(s.DebtPaymentSerializer(page, many=True).data)

    def post(self, request, uuid):
        customer = _customer(uuid)
        ser = s.DebtPaymentInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        payment = services.receive_debt_payment(actor=request.user, customer=customer, request=request,
                                                **ser.validated_data)
        return Response({"payment": s.DebtPaymentSerializer(payment).data, **_account_summary(customer)},
                        status=status.HTTP_201_CREATED)


# ================================================================== the customer's own account


class MyAccountView(APIView):
    """What I owe the shop and how many invoices are still open."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_account_summary(request.user))


class MyInvoicesView(APIView):
    """My invoices (`?open=1`: only those I still owe money on), newest first."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        paginator = PosPagination()
        page = paginator.paginate_queryset(_invoices(request.user, request), request)
        return paginator.get_paginated_response(s.CustomerInvoiceListSerializer(page, many=True).data)


class MyInvoiceView(APIView):
    """One of my invoices, with its lines and how its debt was paid off."""

    permission_classes = [IsAuthenticated]

    def get(self, request, uuid):
        sale = (Sale.objects.filter(customer=request.user, uuid=uuid)
                .prefetch_related("lines", "payments", "allocations__payment").first())
        if sale is None:
            raise ServiceError("not_found", "Invoice not found.", status_code=404)
        return Response(s.CustomerInvoiceSerializer(sale).data)


class MyDebtPaymentsView(APIView):
    """What I have paid towards my debt."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = (DebtPayment.objects.filter(customer=request.user).select_related("created_by")
              .prefetch_related("allocations__sale"))
        paginator = PosPagination()
        page = paginator.paginate_queryset(qs, request)
        data = s.DebtPaymentSerializer(page, many=True).data
        for row in data:
            row.pop("received_by", None)  # staff names stay internal
        return paginator.get_paginated_response(data)
