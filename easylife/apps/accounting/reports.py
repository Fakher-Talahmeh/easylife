"""Read-only financial reports. They only ever look at POSTED (and later REVERSED) entries:
drafts never affect a balance. All amounts are in the BASE currency and returned as 2-decimal strings."""

from datetime import timedelta
from decimal import Decimal

from django.db.models import F, Sum, Window
from django.utils import timezone

from apps.currencies.models import Currency, ExchangeRate
from apps.currencies.services import get_base_currency, quantize

from .models import (
    Account, AccountMapping, AccountType, EntryStatus, FiscalPeriod, JournalEntry, JournalLine,
    PeriodStatus,
)

ZERO = Decimal("0")
CENT = Decimal("0.01")


def m(value):
    """Money string with 2 decimals (base currency)."""
    return format(Decimal(value).quantize(CENT), "f")


def posted_lines():
    return JournalLine.objects.filter(entry__status__in=[EntryStatus.POSTED, EntryStatus.REVERSED])


def _by_account(qs):
    rows = qs.values("account_id").annotate(d=Sum("debit"), c=Sum("credit"))
    return {r["account_id"]: (r["d"] or ZERO, r["c"] or ZERO) for r in rows}


def _account_ref(account, lang):
    return {"uuid": str(account.uuid), "code": account.code, "name": account.name_for(lang)}


def _split(net):
    """Net (debit - credit) -> {"debit", "credit"} with the amount on its natural side."""
    return {"debit": m(net if net > 0 else ZERO), "credit": m(-net if net < 0 else ZERO)}


def balance_of(account_ids, as_of=None, debit_side=True):
    qs = posted_lines().filter(account_id__in=account_ids)
    if as_of:
        qs = qs.filter(entry__date__lte=as_of)
    t = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
    net = (t["d"] or ZERO) - (t["c"] or ZERO)
    return net if debit_side else -net


# ------------------------------------------------------------------ trial balance


def trial_balance(*, from_date, to_date, lang="ar", hide_zero=True):
    opening = _by_account(posted_lines().filter(entry__date__lt=from_date))
    movement = _by_account(posted_lines().filter(entry__date__gte=from_date, entry__date__lte=to_date))
    ids = set(opening) | set(movement)
    accounts = Account.objects.filter(pk__in=ids) if hide_zero else Account.objects.filter(is_postable=True)
    rows, tot = [], {k: [ZERO, ZERO] for k in ("opening", "movement", "closing")}
    for acc in accounts.order_by("code"):
        od, oc = opening.get(acc.pk, (ZERO, ZERO))
        md, mc = movement.get(acc.pk, (ZERO, ZERO))
        open_net, close_net = od - oc, od - oc + md - mc
        if hide_zero and open_net == 0 and md == 0 and mc == 0 and close_net == 0:
            continue
        rows.append({
            "account": _account_ref(acc, lang), "type": acc.type,
            "opening": _split(open_net), "movement": {"debit": m(md), "credit": m(mc)}, "closing": _split(close_net),
        })
        for key, (d, c) in (("opening", _split_raw(open_net)), ("movement", (md, mc)), ("closing", _split_raw(close_net))):
            tot[key][0] += d
            tot[key][1] += c
    totals = {k: {"debit": m(v[0]), "credit": m(v[1])} for k, v in tot.items()}
    return {
        "from": str(from_date), "to": str(to_date), "currency": get_base_currency().code,
        "rows": rows, "totals": totals,
        "balanced": tot["closing"][0] == tot["closing"][1] and tot["movement"][0] == tot["movement"][1],
    }


def _split_raw(net):
    return (net if net > 0 else ZERO, -net if net < 0 else ZERO)


# ------------------------------------------------------------------ general ledger (account statement)


def general_ledger(*, account, from_date, to_date, lang="ar", offset=0, limit=100):
    sign = 1 if account.debit_normal else -1
    before = posted_lines().filter(account=account, entry__date__lt=from_date).aggregate(d=Sum("debit"), c=Sum("credit"))
    opening = sign * ((before["d"] or ZERO) - (before["c"] or ZERO))

    qs = posted_lines().filter(account=account, entry__date__gte=from_date, entry__date__lte=to_date)
    order = [F("entry__date").asc(), F("entry__number").asc(), F("line_no").asc()]
    running = qs.annotate(run=Window(expression=Sum(F("debit") - F("credit")), order_by=order)).select_related(
        "entry", "currency").order_by(*order)
    count = qs.count()
    page = list(running[offset:offset + limit])
    rows = [{
        "date": str(ln.entry.date),
        "entry": {"uuid": str(ln.entry.uuid), "number": ln.entry.number},
        "description": ln.description or ln.entry.memo,
        "source": {"type": ln.entry.source_type or "manual", "id": ln.entry.source_id or None},
        "currency": ln.currency.code, "amount_currency": format(quantize(ln.amount_currency, ln.currency), "f"),
        "rate": format(ln.rate, "f"),
        "debit": m(ln.debit), "credit": m(ln.credit),
        "balance": m(opening + sign * ln.run),
    } for ln in page]
    total = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
    td, tc = total["d"] or ZERO, total["c"] or ZERO
    return {
        "account": _account_ref(account, lang), "from": str(from_date), "to": str(to_date),
        "currency": get_base_currency().code, "balance_side": "debit" if account.debit_normal else "credit",
        "opening_balance": m(opening), "rows": rows, "totals": {"debit": m(td), "credit": m(tc)},
        "closing_balance": m(opening + sign * (td - tc)), "count": count,
    }


# ------------------------------------------------------------------ income statement & balance sheet


def _movement_rows(types, from_date, to_date, lang, credit_normal):
    qs = posted_lines().filter(account__type__in=types, entry__date__lte=to_date)
    if from_date:
        qs = qs.filter(entry__date__gte=from_date)
    sums = _by_account(qs)
    rows = []
    for acc in Account.objects.filter(pk__in=sums).order_by("code"):
        d, c = sums[acc.pk]
        amount = (c - d) if credit_normal(acc) else (d - c)
        if amount != 0:
            rows.append((acc, amount))
    return rows


def income_statement(*, from_date, to_date, lang="ar"):
    cogs_ids = set(AccountMapping.objects.filter(key="cogs").values_list("account_id", flat=True))
    revenue = _movement_rows([AccountType.REVENUE], from_date, to_date, lang, lambda a: True)
    expense = _movement_rows([AccountType.EXPENSE], from_date, to_date, lang, lambda a: False)
    cogs = [(a, v) for a, v in expense if a.pk in cogs_ids]
    other = [(a, v) for a, v in expense if a.pk not in cogs_ids]

    def section(rows):
        return {"rows": [{"account": _account_ref(a, lang), "amount": m(v)} for a, v in rows],
                "total": m(sum((v for _, v in rows), ZERO))}

    rev_t = sum((v for _, v in revenue), ZERO)
    cogs_t = sum((v for _, v in cogs), ZERO)
    oth_t = sum((v for _, v in other), ZERO)
    return {
        "from": str(from_date), "to": str(to_date), "currency": get_base_currency().code,
        "revenue": section(revenue), "cost_of_goods": section(cogs),
        "gross_profit": m(rev_t - cogs_t), "expenses": section(other), "net_profit": m(rev_t - cogs_t - oth_t),
    }


def balance_sheet(*, as_of, lang="ar"):
    sums = _by_account(posted_lines().filter(entry__date__lte=as_of))
    sections = {t: [] for t in (AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY)}
    earnings = ZERO
    for acc in Account.objects.filter(pk__in=sums).order_by("code"):
        d, c = sums[acc.pk]
        if acc.type == AccountType.REVENUE:
            earnings += c - d
        elif acc.type == AccountType.EXPENSE:
            earnings -= d - c
        else:
            amount = (d - c) if acc.type == AccountType.ASSET else (c - d)
            if amount != 0:
                sections[acc.type].append((acc, amount))

    def section(rows):
        return {"rows": [{"account": _account_ref(a, lang), "amount": m(v)} for a, v in rows],
                "total": m(sum((v for _, v in rows), ZERO))}

    assets = sum((v for _, v in sections[AccountType.ASSET]), ZERO)
    liabilities = sum((v for _, v in sections[AccountType.LIABILITY]), ZERO)
    equity = sum((v for _, v in sections[AccountType.EQUITY]), ZERO)
    return {
        "as_of": str(as_of), "currency": get_base_currency().code,
        "assets": section(sections[AccountType.ASSET]), "liabilities": section(sections[AccountType.LIABILITY]),
        "equity": {**section(sections[AccountType.EQUITY]), "current_earnings": m(earnings),
                   "total_with_earnings": m(equity + earnings)},
        "balanced": assets == liabilities + equity + earnings,
    }


# ------------------------------------------------------------------ dashboard summary


def summary(user):
    today = timezone.localdate()
    period = FiscalPeriod.objects.filter(start_date__lte=today, end_date__gte=today).first()
    maps = {r.key: r.account_id for r in AccountMapping.objects.all()}

    def bal(keys, debit_side):
        ids = [maps[k] for k in keys if k in maps]
        return m(balance_of(ids, debit_side=debit_side)) if ids else m(ZERO)

    base = get_base_currency()
    stale = []
    for cur in Currency.objects.filter(is_active=True).exclude(pk=base.pk):
        last = ExchangeRate.objects.filter(currency=cur).order_by("-date").first()
        if last is None or last.date < today - timedelta(days=7):
            stale.append({"currency": cur.code, "last_date": str(last.date) if last else None})
    drafts = JournalEntry.objects.filter(status=EntryStatus.DRAFT)
    past_open = FiscalPeriod.objects.filter(status=PeriodStatus.OPEN)
    if period:
        past_open = past_open.filter(end_date__lt=period.start_date)
    else:
        past_open = past_open.filter(end_date__lt=today)
    return {
        "currency": base.code,
        "current_period": {"name": period.name, "status": period.status} if period else None,
        "balances": {"cash_bank": bal(["cash", "bank"], True), "receivables": bal(["receivable"], True),
                     "payables": bal(["payable"], False)},
        "alerts": {"draft_entries": drafts.count(), "own_drafts": drafts.filter(created_by=user).count(),
                   "unclosed_past_periods": past_open.count(), "stale_rates": stale},
    }
