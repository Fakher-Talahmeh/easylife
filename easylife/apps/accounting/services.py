"""Accounting write-side logic. Invariants enforced here (and by DB check constraints):

* a posted entry balances (sum debit == sum credit, in the BASE currency) and has >= 2 lines
* each line is either a debit or a credit, on a postable + active account
* nothing posts into a closed (or missing) fiscal period
* posted entries are immutable; corrections are reversal entries
* maker-checker: whoever created a manual entry cannot post it (admins excepted)
"""

from calendar import monthrange
from datetime import date as date_cls
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status

from apps.core import audit
from apps.core.exceptions import ServiceError
from apps.core.numbering import next_number
from apps.currencies import services as fx
from apps.currencies.models import Currency

from .chart import CHART, DEFAULT_MAPPINGS
from .models import (
    MAPPING_TYPES, Account, AccountMapping, AccountType, EntryStatus, FiscalPeriod, JournalEntry,
    JournalLine, PeriodStatus, TaxRate,
)

ZERO = Decimal("0")


# ================================================================== chart of accounts


@transaction.atomic
def seed_chart():
    """Create the default chart and default mappings. Idempotent: never touches existing rows."""
    by_code, created = {}, 0
    for code, typ, parent, postable, ar, en, he in CHART:
        acc, was_created = Account.objects.get_or_create(
            code=code,
            defaults=dict(type=typ, parent=by_code.get(parent), is_postable=postable, name_ar=ar,
                          name_en=en, name_he=he, is_system=True),
        )
        by_code[code] = acc
        created += int(was_created)
    for key, code in DEFAULT_MAPPINGS.items():
        AccountMapping.objects.get_or_create(key=key, defaults={"account": by_code[code]})
    return created


def has_entries(account):
    return JournalLine.objects.filter(account=account).exists()


def _check_parent(account_type, parent, self_account=None):
    node = parent
    while node is not None:
        if self_account is not None and node.pk == self_account.pk:
            raise ServiceError("parent_cycle", "An account cannot be its own ancestor.")
        node = node.parent
    if parent is not None and parent.type != account_type:
        raise ServiceError("parent_type_mismatch", "The parent must be of the same account type.")
    if parent is not None and has_entries(parent):
        raise ServiceError("parent_has_entries", "The parent already has journal entries and cannot be a group.")


@transaction.atomic
def create_account(*, actor, data, request=None):
    if Account.objects.filter(code=data["code"]).exists():
        raise ServiceError("code_taken", "This account code is already used.", fields={"code": ["Already used."]})
    parent = data.get("parent")
    _check_parent(data["type"], parent)
    if parent is not None and parent.is_postable:
        parent.is_postable = False  # a parent becomes a group
        parent.save(update_fields=["is_postable", "updated_at"])
    account = Account.objects.create(**data)
    audit.log("account.created", actor=actor, request=request, obj=account, code=account.code)
    return account


_LOCKED_WHEN_USED = ("code", "type", "parent", "is_postable", "currency")


@transaction.atomic
def update_account(*, actor, account, data, request=None):
    used = has_entries(account)
    for field in _LOCKED_WHEN_USED:
        if field in data and data[field] != getattr(account, field):
            if used:
                raise ServiceError("account_in_use", f"{field} cannot change once the account has entries.")
            if field == "type" and account.is_system:
                raise ServiceError("system_account_locked", "The type of a system account cannot change.")
    if "code" in data and data["code"] != account.code and Account.objects.filter(code=data["code"]).exists():
        raise ServiceError("code_taken", "This account code is already used.", fields={"code": ["Already used."]})
    new_type = data.get("type", account.type)
    new_parent = data["parent"] if "parent" in data else account.parent
    _check_parent(new_type, new_parent, self_account=account)
    if data.get("is_postable") and account.children.exists():
        raise ServiceError("account_has_children", "An account with sub-accounts cannot receive entries.")
    if data.get("is_active") is False and AccountMapping.objects.filter(account=account).exists():
        raise ServiceError("account_mapped", "Remap the default account before deactivating it.")
    for key, value in data.items():
        setattr(account, key, value)
    account.save()
    audit.log("account.updated", actor=actor, request=request, obj=account, fields=sorted(data))
    return account


@transaction.atomic
def set_mappings(*, actor, mapping, request=None):
    """mapping = {key: Account}. Each account must be postable, active and of the expected type."""
    for key, account in mapping.items():
        if key not in MAPPING_TYPES:
            raise ServiceError("unknown_mapping_key", f"Unknown key: {key}.")
        if account.type != MAPPING_TYPES[key]:
            raise ServiceError("mapping_type_mismatch", f"{key} requires a {MAPPING_TYPES[key]} account.",
                               fields={key: [f"Expected {MAPPING_TYPES[key]}."]})
        if not account.is_postable or not account.is_active:
            raise ServiceError("account_not_postable", f"{account.code} cannot receive entries.", fields={key: ["Not usable."]})
    for key, account in mapping.items():
        AccountMapping.objects.update_or_create(key=key, defaults={"account": account})
    audit.log("mapping.updated", actor=actor, request=request, keys=sorted(mapping))


def mapped_account(key):
    row = AccountMapping.objects.select_related("account").filter(key=key).first()
    if row is None:
        raise ServiceError("mapping_missing", f"No default account is configured for '{key}'.")
    return row.account


@transaction.atomic
def save_tax_rate(*, actor, instance=None, data, request=None):
    for field, expected in (("collected_account", AccountType.LIABILITY), ("paid_account", AccountType.ASSET)):
        acc = data.get(field)
        if acc is not None and (acc.type != expected or not acc.is_postable):
            raise ServiceError("tax_account_type", f"{field} must be a postable {expected} account.", fields={field: [f"Expected {expected}."]})
    if instance is None:
        instance = TaxRate.objects.create(**data)
    else:
        for k, v in data.items():
            setattr(instance, k, v)
        instance.save()
    audit.log("tax_rate.saved", actor=actor, request=request, obj=instance, rate=str(instance.rate))
    return instance


# ================================================================== fiscal periods


@transaction.atomic
def generate_periods(*, actor, year, request=None):
    if FiscalPeriod.objects.filter(start_date__year=year).exists():
        raise ServiceError("year_exists", f"Periods for {year} already exist.")
    periods = [
        FiscalPeriod(name=f"{year}-{m:02d}", start_date=date_cls(year, m, 1),
                     end_date=date_cls(year, m, monthrange(year, m)[1]))
        for m in range(1, 13)
    ]
    FiscalPeriod.objects.bulk_create(periods)
    audit.log("period.generated", actor=actor, request=request, year=year)
    return list(FiscalPeriod.objects.filter(start_date__year=year))


def period_for(on_date, *, require_open=True):
    period = FiscalPeriod.objects.filter(start_date__lte=on_date, end_date__gte=on_date).first()
    if period is None:
        raise ServiceError("period_not_found", f"No fiscal period covers {on_date}.", fields={"date": [str(on_date)]})
    if require_open and period.status != PeriodStatus.OPEN:
        raise ServiceError("period_closed", f"Period {period.name} is closed.", fields={"date": [period.name]})
    return period


def _posted_lines():
    return JournalLine.objects.filter(entry__status__in=[EntryStatus.POSTED, EntryStatus.REVERSED])


def close_checklist(period):
    """Pre-close checks. level "error" blocks closing; "warning" only informs."""
    drafts = JournalEntry.objects.filter(period=period, status=EntryStatus.DRAFT).count()
    totals = _posted_lines().filter(entry__date__lte=period.end_date).aggregate(d=Sum("debit"), c=Sum("credit"))
    debit, credit = totals["d"] or ZERO, totals["c"] or ZERO

    missing = []
    base = fx.get_base_currency()
    used = Currency.objects.filter(pk__in=JournalLine.objects.filter(entry__period=period).values("currency")).exclude(pk=base.pk)
    for currency in used:
        try:
            fx.get_rate(currency, period.end_date)
        except ServiceError:
            missing.append(currency.code)
    return [
        {"code": "no_drafts", "level": "error", "ok": drafts == 0, "detail": {"drafts": drafts}},
        {"code": "trial_balance_balanced", "level": "error", "ok": debit == credit,
         "detail": {"debit": format(debit.quantize(Decimal("0.01")), "f"), "credit": format(credit.quantize(Decimal("0.01")), "f")}},
        {"code": "rates_present", "level": "warning", "ok": not missing, "detail": {"missing": missing}},
    ]


@transaction.atomic
def close_period(*, actor, period, request=None):
    period = FiscalPeriod.objects.select_for_update().get(pk=period.pk)
    if period.status == PeriodStatus.CLOSED:
        raise ServiceError("period_already_closed", f"Period {period.name} is already closed.")
    for check in close_checklist(period):
        if check["level"] == "error" and not check["ok"]:
            fields = {k: [str(v)] for k, v in check["detail"].items()}
            if check["code"] == "no_drafts":
                raise ServiceError("period_has_drafts", f"Period {period.name} still has {check['detail']['drafts']} draft "
                                   "entries. Post or delete them before closing.", fields=fields)
            raise ServiceError("trial_balance_unbalanced", f"The trial balance of {period.name} is out of balance.", fields=fields)
    period.status, period.closed_by, period.closed_at = PeriodStatus.CLOSED, actor, timezone.now()
    period.save(update_fields=["status", "closed_by", "closed_at", "updated_at"])
    audit.log("period.closed", actor=actor, request=request, obj=period, name=period.name)
    return period


@transaction.atomic
def reopen_period(*, actor, period, reason, request=None):
    if not actor.is_superadmin:
        raise ServiceError("forbidden", "Only an admin can reopen a closed period.", status.HTTP_403_FORBIDDEN)
    if not (reason or "").strip():
        raise ServiceError("reason_required", "A reason is required.", fields={"reason": ["This field is required."]})
    if period.status != PeriodStatus.CLOSED:
        raise ServiceError("period_not_closed", f"Period {period.name} is not closed.")
    period.status, period.closed_by, period.closed_at = PeriodStatus.OPEN, None, None
    period.save(update_fields=["status", "closed_by", "closed_at", "updated_at"])
    audit.log("period.reopened", actor=actor, request=request, obj=period, name=period.name, reason=reason.strip())
    return period


# ================================================================== journal entries


def _line_error(index, field, code, message):
    return ServiceError(code, message, fields={"lines": {str(index): {field: [message]}}})


def normalize_lines(lines, entry_date):
    """Validate input lines and compute base amounts. Input per line:
    {account, description?, currency?, side: "debit"|"credit", amount (line currency), rate?}"""
    base = fx.get_base_currency()
    out = []
    for i, ln in enumerate(lines, start=1):
        account = ln["account"]
        if not account.is_active:
            raise _line_error(i, "account", "account_inactive", f"Account {account.code} is inactive.")
        if not account.is_postable:
            raise _line_error(i, "account", "account_not_postable", f"{account.code} is a group account.")
        currency = ln.get("currency") or base
        if not currency.is_active:
            raise _line_error(i, "currency", "currency_inactive", f"{currency.code} is inactive.")
        if account.currency_id and account.currency_id != currency.pk:
            raise _line_error(i, "currency", "currency_mismatch", f"{account.code} only accepts {account.currency.code}.")
        amount = Decimal(ln["amount"])
        if amount <= 0:
            raise _line_error(i, "amount", "invalid_amount", "The amount must be greater than zero.")
        if currency.pk == base.pk:
            rate = Decimal(1)
        else:
            rate = ln.get("rate")
            if rate is None:
                try:
                    rate = fx.get_rate(currency, entry_date)
                except ServiceError as exc:
                    raise _line_error(i, "rate", exc.code, exc.detail)
            rate = Decimal(rate)
            if rate <= 0:
                raise _line_error(i, "rate", "invalid_rate", "The rate must be greater than zero.")
        base_amount = fx.quantize(amount * rate, base)
        if base_amount <= 0:
            raise _line_error(i, "amount", "invalid_amount", "The amount rounds to zero in the base currency.")
        side = ln["side"]
        out.append({
            "line_no": i, "account": account, "description": (ln.get("description") or "")[:255],
            "currency": currency, "amount_currency": fx.quantize(amount, currency), "rate": rate,
            "debit": base_amount if side == "debit" else ZERO,
            "credit": base_amount if side == "credit" else ZERO,
        })
    return out


def _write_lines(entry, normalized):
    JournalLine.objects.bulk_create([JournalLine(entry=entry, **row) for row in normalized])


@transaction.atomic
def create_entry(*, actor, date, memo="", lines=(), kind="journal", request=None):
    period = period_for(date)
    normalized = normalize_lines(lines, date)
    entry = JournalEntry.objects.create(
        number=next_number("journal", "JV-"), date=date, period=period, memo=memo[:255], kind=kind, created_by=actor,
    )
    _write_lines(entry, normalized)
    audit.log("journal.created", actor=actor, request=request, obj=entry, number=entry.number, lines=len(normalized))
    return entry


def _own_draft(actor, entry):
    if entry.status != EntryStatus.DRAFT:
        raise ServiceError("entry_not_draft", "Only draft entries can be changed.")
    if entry.created_by_id != actor.pk and not actor.is_superadmin:
        raise ServiceError("not_entry_owner", "Only the author of a draft can change it.", status.HTTP_403_FORBIDDEN)


@transaction.atomic
def update_entry(*, actor, entry, data, request=None):
    entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)
    _own_draft(actor, entry)
    new_date = data.get("date", entry.date)
    if "date" in data:
        entry.period = period_for(new_date)
        entry.date = new_date
    if "memo" in data:
        entry.memo = data["memo"][:255]
    normalized = normalize_lines(data["lines"], new_date) if "lines" in data else None
    if normalized is None and "date" in data and entry.lines.filter(currency__is_base=False).exists():
        raise ServiceError("lines_required", "Resend the lines when changing the date of a multi-currency entry (rates depend on the date).")
    entry.save()
    if normalized is not None:
        entry.lines.all().delete()
        _write_lines(entry, normalized)
    audit.log("journal.updated", actor=actor, request=request, obj=entry, number=entry.number)
    return entry


@transaction.atomic
def delete_entry(*, actor, entry, request=None):
    entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)
    _own_draft(actor, entry)
    audit.log("journal.deleted", actor=actor, request=request, obj=entry, number=entry.number)
    entry.lines.all().delete()
    entry.delete()


def _validate_for_posting(entry):
    lines = list(entry.lines.select_related("account"))
    if len(lines) < 2:
        raise ServiceError("entry_too_few_lines", "An entry needs at least two lines.")
    for ln in lines:
        if not ln.account.is_active:
            raise _line_error(ln.line_no, "account", "account_inactive", f"Account {ln.account.code} is inactive.")
        if not ln.account.is_postable:
            raise _line_error(ln.line_no, "account", "account_not_postable", f"{ln.account.code} is a group account.")
    debit = sum((ln.debit for ln in lines), ZERO)
    credit = sum((ln.credit for ln in lines), ZERO)
    if debit != credit:
        raise ServiceError(
            "unbalanced_entry", f"The entry is out of balance by {abs(debit - credit)}.",
            fields={"debit": [str(debit)], "credit": [str(credit)], "difference": [str(debit - credit)]},
        )


@transaction.atomic
def post_entry(*, actor, entry, system=False, request=None):
    entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.status != EntryStatus.DRAFT:
        raise ServiceError("entry_already_posted", f"{entry.number} is already {entry.status}.")
    if not system and entry.created_by_id == actor.pk and not actor.is_superadmin:
        raise ServiceError(
            "cannot_post_own_entry", "You cannot post an entry you created; another user must post it.",
            status.HTTP_403_FORBIDDEN,
        )
    period_for(entry.date)  # still open?
    _validate_for_posting(entry)
    entry.status, entry.posted_by, entry.posted_at = EntryStatus.POSTED, actor, timezone.now()
    entry.save(update_fields=["status", "posted_by", "posted_at", "updated_at"])
    audit.log("journal.posted", actor=actor, request=request, obj=entry, number=entry.number, system=system)
    return entry


@transaction.atomic
def create_posted_entry(*, actor, date, memo, lines, source_type, source_id, kind="journal", request=None):
    """For automatic entries from other modules (sales, purchasing, stock): created and posted at once,
    carrying the source document. Maker-checker does not apply to system postings."""
    entry = create_entry(actor=actor, date=date, memo=memo, lines=lines, kind=kind, request=request)
    entry.source_type, entry.source_id = source_type, str(source_id)
    entry.save(update_fields=["source_type", "source_id", "updated_at"])
    return post_entry(actor=actor, entry=entry, system=True, request=request)


@transaction.atomic
def reverse_entry(*, actor, entry, reason, date=None, request=None):
    entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)
    if not (reason or "").strip():
        raise ServiceError("reason_required", "A reason is required.", fields={"reason": ["This field is required."]})
    if entry.status == EntryStatus.REVERSED:
        raise ServiceError("entry_already_reversed", f"{entry.number} was already reversed.")
    if entry.status != EntryStatus.POSTED:
        raise ServiceError("entry_not_posted", "Only posted entries can be reversed.")
    if entry.reversal_of_id:
        raise ServiceError("cannot_reverse_reversal", "A reversal entry cannot be reversed; post a new entry instead.")

    on_date = date or timezone.localdate()
    period = period_for(on_date)
    now = timezone.now()
    reversal = JournalEntry.objects.create(
        number=next_number("journal", "JV-"), date=on_date, period=period, kind=entry.kind,
        memo=f"Reversal of {entry.number}: {reason.strip()}"[:255], reversal_of=entry,
        source_type=entry.source_type, source_id=entry.source_id, created_by=actor,
    )
    JournalLine.objects.bulk_create([
        JournalLine(entry=reversal, line_no=ln.line_no, account=ln.account, description=ln.description,
                    currency=ln.currency, amount_currency=ln.amount_currency, rate=ln.rate,
                    debit=ln.credit, credit=ln.debit, party_type=ln.party_type, party_id=ln.party_id)
        for ln in entry.lines.all()
    ])
    reversal.status, reversal.posted_by, reversal.posted_at = EntryStatus.POSTED, actor, now
    reversal.save(update_fields=["status", "posted_by", "posted_at", "updated_at"])
    entry.status, entry.reversed_at, entry.reverse_reason = EntryStatus.REVERSED, now, reason.strip()[:255]
    entry.save(update_fields=["status", "reversed_at", "reverse_reason", "updated_at"])
    audit.log("journal.reversed", actor=actor, request=request, obj=entry, number=entry.number,
              reversal=reversal.number, reason=reason.strip())
    return reversal
