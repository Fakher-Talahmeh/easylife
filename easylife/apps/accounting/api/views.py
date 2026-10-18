import csv
from datetime import date as date_cls
from datetime import datetime

from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounting import reports, services
from apps.accounting.models import (
    MAPPING_TYPES, Account, AccountMapping, EntryStatus, FiscalPeriod, JournalEntry, JournalLine, MappingKey, TaxRate,
)
from apps.accounts import rbac
from apps.accounts.rbac import HasPermission
from apps.core.exceptions import ServiceError

from . import serializers as s

NO_PUT = ["get", "post", "patch", "delete", "head", "options"]


def _truthy(v):
    return str(v).lower() in {"1", "true", "yes"}


def _date(request, name, default=None):
    raw = request.query_params.get(name)
    if not raw:
        return default
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise ServiceError("invalid_date", f"'{name}' must be YYYY-MM-DD.", fields={name: [raw]})


def _lang(request):
    lang = request.query_params.get("lang", "ar")
    return lang if lang in ("ar", "en", "he") else "ar"


# ================================================================== accounts


class AccountViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                     mixins.UpdateModelMixin, viewsets.GenericViewSet):
    permission_classes = [HasPermission]
    lookup_field = "uuid"
    http_method_names = NO_PUT
    pagination_class = None  # a chart is a few hundred rows at most; the UI builds the tree
    required_permission = {"list": "accounting.view", "retrieve": "accounting.view",
                           "create": "accounting.manage_accounts", "partial_update": "accounting.manage_accounts",
                           "seed": "accounting.manage_accounts"}

    def get_queryset(self):
        qs = Account.objects.select_related("parent", "currency").annotate(
            has_entries_flag=Exists(JournalLine.objects.filter(account=OuterRef("pk"))))
        p = self.request.query_params
        if p.get("type"):
            qs = qs.filter(type=p["type"])
        if p.get("active") not in (None, ""):
            qs = qs.filter(is_active=_truthy(p["active"]))
        if p.get("postable") not in (None, ""):
            qs = qs.filter(is_postable=_truthy(p["postable"]))
        if p.get("search"):
            t = p["search"].strip()
            qs = qs.filter(Q(code__icontains=t) | Q(name_ar__icontains=t) | Q(name_en__icontains=t) | Q(name_he__icontains=t))
        return qs.order_by("code")

    def get_serializer_class(self):
        return s.AccountWriteSerializer if self.action in ("create", "partial_update") else s.AccountSerializer

    def _balances(self, request):
        if not _truthy(request.query_params.get("with_balance", "")):
            return None
        as_of = _date(request, "as_of", timezone.localdate())
        sums = reports._by_account(reports.posted_lines().filter(entry__date__lte=as_of))
        out = {}
        for acc in Account.objects.filter(pk__in=sums):
            d, c = sums[acc.pk]
            out[acc.pk] = reports.m((d - c) if acc.debit_normal else (c - d))
        return out

    def _read(self, request, account):
        fresh = self.get_queryset().get(pk=account.pk)
        return s.AccountSerializer(fresh, context={"request": request, "balances": self._balances(request)}).data

    def list(self, request, *args, **kwargs):
        ctx = {"request": request, "balances": self._balances(request)}
        return Response(s.AccountSerializer(self.get_queryset(), many=True, context=ctx).data)

    def retrieve(self, request, *args, **kwargs):
        return Response(self._read(request, self.get_object()))

    def create(self, request, *args, **kwargs):
        ser = s.AccountWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        account = services.create_account(actor=request.user, data=dict(ser.validated_data), request=request)
        return Response(self._read(request, account), status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        ser = s.AccountWriteSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        account = services.update_account(actor=request.user, account=self.get_object(),
                                          data=dict(ser.validated_data), request=request)
        return Response(self._read(request, account))

    @action(detail=False, methods=["post"])
    def seed(self, request):
        created = services.seed_chart()
        return Response({"created": created, "total": Account.objects.count()},
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


# ================================================================== fiscal periods


class FiscalPeriodViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [HasPermission]
    serializer_class = s.FiscalPeriodSerializer
    lookup_field = "uuid"
    pagination_class = None
    required_permission = {"list": "accounting.view", "retrieve": "accounting.view",
                           "checklist": "accounting.view", "generate": "accounting.close_period",
                           "close": "accounting.close_period", "reopen": "accounting.close_period"}

    def get_queryset(self):
        qs = FiscalPeriod.objects.annotate(
            posted_entries=Count("entries", filter=Q(entries__status__in=[EntryStatus.POSTED, EntryStatus.REVERSED])),
            draft_entries=Count("entries", filter=Q(entries__status=EntryStatus.DRAFT)),
        )
        if self.request.query_params.get("year"):
            qs = qs.filter(start_date__year=self.request.query_params["year"])
        return qs.order_by("start_date")

    def _read(self, request, period):
        return Response(s.FiscalPeriodSerializer(self.get_queryset().get(pk=period.pk)).data)

    @action(detail=False, methods=["post"])
    def generate(self, request):
        ser = s.GenerateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        services.generate_periods(actor=request.user, year=ser.validated_data["year"], request=request)
        qs = self.get_queryset().filter(start_date__year=ser.validated_data["year"])
        return Response(s.FiscalPeriodSerializer(qs, many=True).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="close-checklist")
    def checklist(self, request, uuid=None):
        period = self.get_object()
        checks = services.close_checklist(period)
        return Response({"period": period.name, "can_close": all(c["ok"] for c in checks if c["level"] == "error"),
                         "checks": checks})

    @action(detail=True, methods=["post"])
    def close(self, request, uuid=None):
        period = services.close_period(actor=request.user, period=self.get_object(), request=request)
        return self._read(request, period)

    @action(detail=True, methods=["post"])
    def reopen(self, request, uuid=None):
        ser = s.ReasonSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        period = services.reopen_period(actor=request.user, period=self.get_object(),
                                        reason=ser.validated_data["reason"], request=request)
        return self._read(request, period)


# ================================================================== tax rates & mappings


class TaxRateViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                     mixins.UpdateModelMixin, viewsets.GenericViewSet):
    permission_classes = [HasPermission]
    serializer_class = s.TaxRateSerializer
    queryset = TaxRate.objects.select_related("collected_account", "paid_account").order_by("name")
    lookup_field = "uuid"
    http_method_names = ["get", "post", "patch", "head", "options"]
    pagination_class = None
    required_permission = {"list": "accounting.view", "retrieve": "accounting.view",
                           "create": "accounting.manage_tax", "partial_update": "accounting.manage_tax"}

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        obj = services.save_tax_rate(actor=request.user, data=dict(ser.validated_data), request=request)
        return Response(self.get_serializer(obj).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        ser = self.get_serializer(self.get_object(), data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        obj = services.save_tax_rate(actor=request.user, instance=ser.instance, data=dict(ser.validated_data), request=request)
        return Response(self.get_serializer(obj).data)


class MappingViewSet(viewsets.ViewSet):
    permission_classes = [HasPermission]
    required_permission = {"list": "accounting.view", "replace": "accounting.manage_accounts"}

    def _payload(self):
        current = {r.key: r.account for r in AccountMapping.objects.select_related("account")}
        items = [{
            "key": key.value, "label": key.label, "expected_type": MAPPING_TYPES[key.value],
            "account": ({"uuid": str(current[key.value].uuid), "code": current[key.value].code,
                         "name_ar": current[key.value].name_ar, "name_en": current[key.value].name_en,
                         "name_he": current[key.value].name_he} if key.value in current else None),
        } for key in MappingKey]
        filled = sum(1 for i in items if i["account"])
        return {"items": items, "filled": filled, "total": len(items), "complete": filled == len(items)}

    def list(self, request):
        return Response(self._payload())

    def replace(self, request):
        ser = s.MappingWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        resolved = {}
        for key, uuid in ser.validated_data["mappings"].items():
            resolved[key] = get_object_or_404(Account, uuid=uuid)
        services.set_mappings(actor=request.user, mapping=resolved, request=request)
        return Response(self._payload())


# ================================================================== journal entries


class JournalEntryViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                          mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    permission_classes = [HasPermission]
    lookup_field = "uuid"
    http_method_names = NO_PUT
    required_permission = {"list": "accounting.view", "retrieve": "accounting.view",
                           "create": "accounting.create_entry", "partial_update": "accounting.create_entry",
                           "destroy": "accounting.create_entry", "post_entry": "accounting.post_entry",
                           "reverse": "accounting.reverse_entry"}

    def get_queryset(self):
        qs = JournalEntry.objects.select_related("period", "created_by", "posted_by", "reversal_of").annotate(
            total_debit=Sum("lines__debit"))
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("kind"):
            qs = qs.filter(kind=p["kind"])
        if p.get("from"):
            qs = qs.filter(date__gte=_date(self.request, "from"))
        if p.get("to"):
            qs = qs.filter(date__lte=_date(self.request, "to"))
        if p.get("source"):
            qs = qs.filter(source_type="") if p["source"] == "manual" else qs.filter(source_type=p["source"])
        if p.get("account"):
            qs = qs.filter(Exists(JournalLine.objects.filter(entry=OuterRef("pk"), account__uuid=p["account"])))
        if p.get("created_by"):
            qs = qs.filter(created_by__uuid=p["created_by"])
        if p.get("search"):
            qs = qs.filter(Q(number__icontains=p["search"].strip()) | Q(memo__icontains=p["search"].strip()))
        return qs.order_by("-date", "-id")

    def get_serializer_class(self):
        return s.EntryListSerializer if self.action == "list" else s.EntryDetailSerializer

    def _detail(self, request, entry, code=status.HTTP_200_OK):
        fresh = (JournalEntry.objects.select_related("period", "created_by", "posted_by", "reversal_of")
                 .prefetch_related("lines__account", "lines__currency").get(pk=entry.pk))
        return Response(s.EntryDetailSerializer(fresh, context={"request": request}).data, status=code)

    def retrieve(self, request, *args, **kwargs):
        return self._detail(request, self.get_object())

    def create(self, request, *args, **kwargs):
        ser = s.EntryWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        entry = services.create_entry(actor=request.user, date=d["date"], memo=d["memo"], kind=d["kind"],
                                      lines=[dict(ln) for ln in d["lines"]], request=request)
        return self._detail(request, entry, status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        ser = s.EntryPatchSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = dict(ser.validated_data)
        if "lines" in data:
            data["lines"] = [dict(ln) for ln in data["lines"]]
        entry = services.update_entry(actor=request.user, entry=self.get_object(), data=data, request=request)
        return self._detail(request, entry)

    def destroy(self, request, *args, **kwargs):
        services.delete_entry(actor=request.user, entry=self.get_object(), request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="post")
    def post_entry(self, request, uuid=None):
        entry = services.post_entry(actor=request.user, entry=self.get_object(), request=request)
        return self._detail(request, entry)

    @action(detail=True, methods=["post"])
    def reverse(self, request, uuid=None):
        ser = s.ReverseSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        reversal = services.reverse_entry(actor=request.user, entry=self.get_object(),
                                          reason=ser.validated_data["reason"], date=ser.validated_data.get("date"),
                                          request=request)
        return self._detail(request, reversal, status.HTTP_201_CREATED)


# ================================================================== reports


class _ReportView(APIView):
    permission_classes = [HasPermission]
    required_permission = "reports.financial"
    export_supported = False

    def _wants_csv(self, request):
        export = request.query_params.get("export")
        if not export:
            return False
        if export != "csv" or not self.export_supported:
            raise ServiceError("export_not_supported", "Only ?export=csv on trial-balance and general-ledger.")
        if not rbac.has_permission(request.user, "reports.export"):
            raise ServiceError("forbidden", "Missing reports.export.", status.HTTP_403_FORBIDDEN)
        return True

    @staticmethod
    def _csv(filename, header, rows):
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.write("﻿")  # BOM so Excel opens Arabic/Hebrew correctly
        writer = csv.writer(response)
        writer.writerow(header)
        writer.writerows(rows)
        return response


def _range(request):
    today = timezone.localdate()
    from_date = _date(request, "from", date_cls(today.year, 1, 1))
    to_date = _date(request, "to", today)
    if from_date > to_date:
        raise ServiceError("invalid_range", "'from' must not be after 'to'.", fields={"from": [str(from_date)]})
    return from_date, to_date


class TrialBalanceView(_ReportView):
    export_supported = True

    def get(self, request):
        from_date, to_date = _range(request)
        data = reports.trial_balance(from_date=from_date, to_date=to_date, lang=_lang(request),
                                     hide_zero=not _truthy(request.query_params.get("include_zero", "")))
        if self._wants_csv(request):
            rows = [[r["account"]["code"], r["account"]["name"], r["opening"]["debit"], r["opening"]["credit"],
                     r["movement"]["debit"], r["movement"]["credit"], r["closing"]["debit"], r["closing"]["credit"]]
                    for r in data["rows"]]
            t = data["totals"]
            rows.append(["", "TOTAL", t["opening"]["debit"], t["opening"]["credit"], t["movement"]["debit"],
                         t["movement"]["credit"], t["closing"]["debit"], t["closing"]["credit"]])
            return self._csv(f"trial-balance-{from_date}-{to_date}.csv",
                             ["code", "account", "opening_debit", "opening_credit", "movement_debit", "movement_credit",
                              "closing_debit", "closing_credit"], rows)
        return Response(data)


class GeneralLedgerView(_ReportView):
    export_supported = True

    def get(self, request):
        uuid = request.query_params.get("account")
        if not uuid:
            raise ServiceError("account_required", "The 'account' parameter is required.", fields={"account": ["Required."]})
        account = get_object_or_404(Account, uuid=uuid)
        from_date, to_date = _range(request)
        csv_mode = self._wants_csv(request)
        try:
            page = max(int(request.query_params.get("page", 1)), 1)
            size = min(max(int(request.query_params.get("page_size", 100)), 1), 500)
        except ValueError:
            raise ServiceError("invalid_page", "page and page_size must be integers.")
        limit = 100000 if csv_mode else size
        data = reports.general_ledger(account=account, from_date=from_date, to_date=to_date, lang=_lang(request),
                                      offset=0 if csv_mode else (page - 1) * size, limit=limit)
        if csv_mode:
            rows = [[r["date"], r["entry"]["number"], r["description"], r["currency"], r["amount_currency"], r["rate"],
                     r["debit"], r["credit"], r["balance"]] for r in data["rows"]]
            return self._csv(f"ledger-{account.code}-{from_date}-{to_date}.csv",
                             ["date", "entry", "description", "currency", "amount", "rate", "debit", "credit", "balance"], rows)
        data.update({"page": page, "page_size": size})
        return Response(data)


class IncomeStatementView(_ReportView):
    def get(self, request):
        from_date, to_date = _range(request)
        self._wants_csv(request)
        return Response(reports.income_statement(from_date=from_date, to_date=to_date, lang=_lang(request)))


class BalanceSheetView(_ReportView):
    def get(self, request):
        self._wants_csv(request)
        return Response(reports.balance_sheet(as_of=_date(request, "as_of", timezone.localdate()), lang=_lang(request)))


class SummaryView(APIView):
    permission_classes = [HasPermission]
    required_permission = "accounting.view"

    def get(self, request):
        data = reports.summary(request.user)
        recent = (JournalEntry.objects.select_related("period", "created_by")
                  .annotate(total_debit=Sum("lines__debit")).order_by("-date", "-id")[:10])
        data["recent_entries"] = s.EntryListSerializer(recent, many=True).data
        return Response(data)
