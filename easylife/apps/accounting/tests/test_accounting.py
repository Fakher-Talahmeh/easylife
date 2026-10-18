from datetime import date
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction

from apps.accounting import services
from apps.accounting.models import (
    Account, AccountMapping, EntryStatus, FiscalPeriod, JournalEntry, JournalLine,
)
from apps.core.exceptions import ServiceError
from apps.core.models import AuditLog
from apps.core.testing import bootstrap, client_for, make_admin, make_customer, make_staff
from apps.currencies import services as fx
from apps.currencies.models import Currency

B = "/api/v1/admin/"
Y = date.today().year


def d(month, day=15):
    return date(Y, month, day)


@pytest.fixture(autouse=True)
def _setup(db):
    bootstrap()
    call_command("seed_accounting", year=Y, verbosity=0)


@pytest.fixture
def admin_user():
    return make_admin()


@pytest.fixture
def admin(admin_user):
    return client_for(admin_user)


def acc(code):
    return Account.objects.get(code=code)


def L(code, side, amount, **extra):
    return {"account": str(acc(code).uuid), "side": side, "amount": str(amount), **extra}


def payload(when, lines, memo="test"):
    return {"date": str(when), "memo": memo, "lines": lines}


def make_entry(client, when, lines, memo="test", post=True):
    r = client.post(B + "journal-entries/", payload(when, lines, memo), format="json")
    assert r.status_code == 201, r.data
    if post:
        p = client.post(f"{B}journal-entries/{r.data['uuid']}/post/")
        assert p.status_code == 200, p.data
        return p.data
    return r.data


def ledger(client):
    """Cash 1000 in / Sales 300 / shipping 40 out: a small but complete month of activity."""
    make_entry(client, d(3, 10), [L("1110", "debit", 1000), L("3100", "credit", 1000)], "capital")
    make_entry(client, d(3, 20), [L("1110", "debit", 300), L("4100", "credit", 300)], "sale")
    make_entry(client, d(4, 5), [L("5400", "debit", 40), L("1110", "credit", 40)], "shipping")


# ================================================================== chart of accounts


def test_seed_creates_chart_mappings_and_is_idempotent():
    assert Account.objects.count() == 21 and AccountMapping.objects.count() == 12
    cash = acc("1110")
    assert (cash.name_ar, cash.name_en, cash.name_he) == ("الصندوق", "Cash", "קופה")
    assert acc("1000").is_postable is False and acc("1110").parent == acc("1000")
    assert FiscalPeriod.objects.filter(start_date__year=Y).count() == 12
    assert services.seed_chart() == 0  # nothing created the second time
    call_command("seed_accounting", year=Y, verbosity=0)
    assert Account.objects.count() == 21 and FiscalPeriod.objects.count() == 12


def test_account_list_filters_balances_and_flags(admin):
    ledger(admin)
    r = admin.get(B + "accounts/")
    assert r.status_code == 200 and len(r.data) == 21
    cash = next(a for a in r.data if a["code"] == "1110")
    assert cash["has_entries"] is True and cash["balance"] is None and str(cash["parent"]) == str(acc("1000").uuid)
    assert [a["code"] for a in admin.get(B + "accounts/?type=revenue&postable=true").data] == ["4100", "4200", "4300"]
    assert [a["code"] for a in admin.get(B + "accounts/?search=Cash").data] == ["1110"]
    assert [a["code"] for a in admin.get(B + "accounts/?search=קופה").data] == ["1110"]
    bal = {a["code"]: a["balance"] for a in admin.get(B + "accounts/?with_balance=true").data}
    assert bal["1110"] == "1260.00" and bal["4100"] == "300.00" and bal["5400"] == "40.00" and bal["1120"] == "0.00"
    old = {a["code"]: a["balance"] for a in admin.get(B + f"accounts/?with_balance=true&as_of={d(3, 15)}").data}
    assert old["1110"] == "1000.00" and old["4100"] == "0.00"


def test_create_account_rules(admin):
    body = {"code": "1115", "name_ar": "صندوق فرعي", "name_en": "Petty cash", "name_he": "קופה קטנה",
            "type": "asset", "parent": str(acc("1110").uuid)}
    r = admin.post(B + "accounts/", body, format="json")
    assert r.status_code == 201 and r.data["is_postable"] is True and r.data["has_entries"] is False
    acc("1110").refresh_from_db()
    assert acc("1110").is_postable is False  # the parent became a group
    assert admin.post(B + "accounts/", body, format="json").data["code"] == "code_taken"
    bad_parent = {**body, "code": "9999", "type": "expense"}
    assert admin.post(B + "accounts/", bad_parent, format="json").data["code"] == "parent_type_mismatch"
    assert admin.post(B + "accounts/", {"code": "x"}, format="json").status_code == 400
    assert admin.post(B + "accounts/", {**body, "code": "bad code!"}, format="json").status_code == 400


def test_parent_with_entries_cannot_become_a_group(admin):
    ledger(admin)
    body = {"code": "1111", "name_ar": "x", "name_en": "x", "name_he": "x", "type": "asset", "parent": str(acc("1110").uuid)}
    r = admin.post(B + "accounts/", body, format="json")
    assert r.status_code == 400 and r.data["code"] == "parent_has_entries"


def test_update_account_locks_and_guards(admin):
    url = lambda a: f"{B}accounts/{a.uuid}/"  # noqa: E731
    assert admin.patch(url(acc("1110")), {"name_en": "Cash on hand"}, format="json").data["name_en"] == "Cash on hand"
    # system account: type locked even without entries
    assert admin.patch(url(acc("1120")), {"type": "liability"}, format="json").data["code"] == "system_account_locked"
    # cycle: a group under its own descendant
    assert admin.patch(url(acc("1000")), {"parent": str(acc("1110").uuid)}, format="json").data["code"] == "parent_cycle"
    # a group cannot be made postable
    assert admin.patch(url(acc("1000")), {"is_postable": True}, format="json").data["code"] == "account_has_children"
    # mapped accounts cannot be deactivated
    assert admin.patch(url(acc("4100")), {"is_active": False}, format="json").data["code"] == "account_mapped"
    # once used, structure is frozen but names stay editable
    ledger(admin)
    r = admin.patch(url(acc("5400")), {"is_postable": False}, format="json")
    assert r.status_code == 400 and r.data["code"] == "account_in_use"
    assert admin.patch(url(acc("5400")), {"name_he": "משלוחים"}, format="json").status_code == 200


def test_chart_permissions():
    assert client_for(make_customer()).get(B + "accounts/").status_code == 403
    assert client_for(make_staff()).get(B + "accounts/").status_code == 403
    viewer = client_for(make_staff(["accounting.view"]))
    assert viewer.get(B + "accounts/").status_code == 200
    assert viewer.post(B + "accounts/seed/").status_code == 403
    assert viewer.patch(f"{B}accounts/{acc('1110').uuid}/", {"name_en": "x"}, format="json").status_code == 403
    assert client_for(make_staff(["accounting.manage_accounts"])).post(B + "accounts/seed/").status_code == 200


def test_seed_endpoint_restores_a_missing_default(admin):
    r = admin.post(B + "accounts/seed/")
    assert r.status_code == 200 and r.data["created"] == 0 and r.data["total"] == 21


# ================================================================== fiscal periods


def test_generate_periods_and_permissions(admin):
    r = admin.post(B + "fiscal-periods/generate/", {"year": Y + 1}, format="json")
    assert r.status_code == 201 and len(r.data) == 12
    assert r.data[0]["name"] == f"{Y + 1}-01" and r.data[1]["start_date"] == f"{Y + 1}-02-01"
    assert r.data[1]["end_date"] == f"{Y + 1}-02-{29 if (Y + 1) % 4 == 0 else 28}"
    assert admin.post(B + "fiscal-periods/generate/", {"year": Y + 1}, format="json").data["code"] == "year_exists"
    accountant = client_for(make_staff(roles=["accountant"]))
    assert accountant.get(B + "fiscal-periods/").status_code == 200
    assert accountant.post(B + "fiscal-periods/generate/", {"year": Y + 2}, format="json").status_code == 403  # admin-only
    assert len(admin.get(B + f"fiscal-periods/?year={Y}").data) == 12


def test_close_blocked_by_drafts_then_succeeds_and_blocks_posting(admin):
    jan = FiscalPeriod.objects.get(name=f"{Y}-01")
    e = make_entry(admin, d(1, 10), [L("1110", "debit", 50), L("3100", "credit", 50)], post=False)
    chk = admin.get(f"{B}fiscal-periods/{jan.uuid}/close-checklist/").data
    assert chk["can_close"] is False and chk["checks"][0] == {"code": "no_drafts", "level": "error", "ok": False, "detail": {"drafts": 1}}
    r = admin.post(f"{B}fiscal-periods/{jan.uuid}/close/")
    assert r.status_code == 400 and r.data["code"] == "period_has_drafts"
    admin.post(f"{B}journal-entries/{e['uuid']}/post/")
    chk = admin.get(f"{B}fiscal-periods/{jan.uuid}/close-checklist/").data
    assert chk["can_close"] is True and {c["code"]: c["ok"] for c in chk["checks"]} == {"no_drafts": True, "trial_balance_balanced": True, "rates_present": True}
    closed = admin.post(f"{B}fiscal-periods/{jan.uuid}/close/")
    assert closed.status_code == 200 and closed.data["status"] == "closed" and closed.data["posted_entries"] == 1
    assert admin.post(f"{B}fiscal-periods/{jan.uuid}/close/").data["code"] == "period_already_closed"
    # nothing can be created or dated into a closed period
    r = admin.post(B + "journal-entries/", payload(d(1, 20), [L("1110", "debit", 1), L("3100", "credit", 1)]), format="json")
    assert r.status_code == 400 and r.data["code"] == "period_closed"
    other = make_entry(admin, d(2, 10), [L("1110", "debit", 5), L("3100", "credit", 5)], post=False)
    r = admin.patch(f"{B}journal-entries/{other['uuid']}/", {"date": str(d(1, 25))}, format="json")
    assert r.data["code"] == "period_closed"
    assert AuditLog.objects.filter(action="period.closed").exists()


def test_post_into_a_period_closed_after_drafting_is_refused(admin_user):
    draft = services.create_entry(actor=admin_user, date=d(5, 10), lines=[
        {"account": acc("1110"), "side": "debit", "amount": 10}, {"account": acc("3100"), "side": "credit", "amount": 10}])
    period = FiscalPeriod.objects.get(name=f"{Y}-05")
    period.status = "closed"
    period.save()
    with pytest.raises(ServiceError) as e:
        services.post_entry(actor=admin_user, entry=draft)
    assert e.value.code == "period_closed"


def test_reopen_needs_admin_and_reason(admin):
    mar = FiscalPeriod.objects.get(name=f"{Y}-03")
    admin.post(f"{B}fiscal-periods/{mar.uuid}/close/")
    accountant = client_for(make_staff(["accounting.close_period"]))
    r = accountant.post(f"{B}fiscal-periods/{mar.uuid}/reopen/", {"reason": "fix"}, format="json")
    assert r.status_code == 403 and r.data["code"] == "forbidden"
    assert admin.post(f"{B}fiscal-periods/{mar.uuid}/reopen/", {}, format="json").data["code"] == "reason_required"
    ok = admin.post(f"{B}fiscal-periods/{mar.uuid}/reopen/", {"reason": "late invoice"}, format="json")
    assert ok.status_code == 200 and ok.data["status"] == "open"
    assert admin.post(f"{B}fiscal-periods/{mar.uuid}/reopen/", {"reason": "again"}, format="json").data["code"] == "period_not_closed"
    log = AuditLog.objects.get(action="period.reopened")
    assert log.data["reason"] == "late invoice"


def test_dates_outside_generated_periods_are_rejected(admin):
    r = admin.post(B + "journal-entries/", payload(date(Y + 5, 1, 1), [L("1110", "debit", 1), L("3100", "credit", 1)]), format="json")
    assert r.status_code == 400 and r.data["code"] == "period_not_found"


# ================================================================== journal entries


def test_create_draft_numbers_and_shape(admin):
    r = admin.post(B + "journal-entries/", payload(d(3, 1), [L("1110", "debit", "250.5", description="cash in"),
                                                              L("3100", "credit", "250.50")]), format="json")
    assert r.status_code == 201, r.data
    assert r.data["number"] == "JV-000001" and r.data["status"] == "draft" and r.data["period"] == f"{Y}-03"
    assert r.data["source"] == {"type": "manual", "id": None} and r.data["created_by"] == "Admin"
    assert r.data["totals"] == {"debit": "250.50", "credit": "250.50", "difference": "0.00", "balanced": True}
    line = r.data["lines"][0]
    assert line["account"]["code"] == "1110" and line["debit"] == "250.50" and line["credit"] == "0.00"
    assert line["currency"] == "ILS" and line["rate"] == "1.00000000" and line["description"] == "cash in"
    second = admin.post(B + "journal-entries/", payload(d(3, 2), []), format="json")
    assert second.status_code == 201 and second.data["number"] == "JV-000002" and second.data["lines"] == []
    assert second.data["totals"]["balanced"] is False


def test_line_validation_errors_point_at_the_line(admin):
    def err(lines):
        return admin.post(B + "journal-entries/", payload(d(3, 1), lines), format="json")

    r = err([L("1110", "debit", 5), L("1000", "credit", 5)])  # 1000 is a group account
    assert r.status_code == 400 and r.data["code"] == "account_not_postable" and "2" in r.data["fields"]["lines"]
    Account.objects.filter(code="5400").update(is_active=False)
    assert err([L("5400", "debit", 5), L("1110", "credit", 5)]).data["code"] == "account_inactive"
    assert err([L("1110", "debit", 0), L("3100", "credit", 5)]).data["code"] == "invalid_amount"
    assert err([L("1110", "debit", -3), L("3100", "credit", 3)]).data["code"] == "invalid_amount"
    assert err([L("1110", "bogus", 5)]).status_code == 400
    usd = Currency.objects.get(code="USD")
    Account.objects.filter(code="1120").update(currency=usd)
    r = err([L("1120", "debit", 5, currency="ILS"), L("3100", "credit", 5)])
    assert r.data["code"] == "currency_mismatch"
    r = err([L("1110", "debit", 5, currency="USD"), L("3100", "credit", 5)])
    assert r.data["code"] == "rate_missing"  # no USD rate has been entered
    assert JournalEntry.objects.count() == 0  # nothing half-saved


def test_multi_currency_lines_use_rates_and_rounding(admin_user, admin):
    usd, jod = Currency.objects.get(code="USD"), Currency.objects.get(code="JOD")
    fx.set_exchange_rate(actor=admin_user, currency=usd, date=d(3, 1), rate_to_base=Decimal("3.70"))
    fx.set_exchange_rate(actor=admin_user, currency=jod, date=d(3, 1), rate_to_base=Decimal("5.2"))
    r = admin.post(B + "journal-entries/", payload(d(3, 10), [
        L("1110", "debit", "100", currency="USD"),
        L("1120", "debit", "10", currency="JOD"),
        L("3100", "credit", "422.00"),
    ]), format="json")
    assert r.status_code == 201, r.data
    usd_line, jod_line, base_line = r.data["lines"]
    assert (usd_line["amount_currency"], usd_line["rate"], usd_line["debit"]) == ("100.00", "3.70000000", "370.00")
    assert (jod_line["currency"], jod_line["amount_currency"], jod_line["debit"]) == ("JOD", "10.000", "52.00")
    assert r.data["totals"]["balanced"] is True
    # a manual rate overrides the table; base amount is rounded to 2 places
    r = admin.post(B + "journal-entries/", payload(d(3, 11), [
        L("1110", "debit", "33.33", currency="USD", rate="3.456"), L("3100", "credit", "115.19")]), format="json")
    assert r.data["lines"][0]["debit"] == "115.19" and r.data["totals"]["balanced"] is True
    # the rate used is the latest one on/before the entry date
    fx.set_exchange_rate(actor=admin_user, currency=usd, date=d(3, 20), rate_to_base=Decimal("4"))
    r = admin.post(B + "journal-entries/", payload(d(3, 12), [
        L("1110", "debit", "10", currency="USD"), L("3100", "credit", "37.00")]), format="json")
    assert r.data["lines"][0]["rate"] == "3.70000000"


def test_post_requires_balance_and_two_lines(admin):
    one = make_entry(admin, d(3, 1), [L("1110", "debit", 5)], post=False)
    assert admin.post(f"{B}journal-entries/{one['uuid']}/post/").data["code"] == "entry_too_few_lines"
    unb = make_entry(admin, d(3, 1), [L("1110", "debit", 100), L("3100", "credit", 90)], post=False)
    r = admin.post(f"{B}journal-entries/{unb['uuid']}/post/")
    assert r.status_code == 400 and r.data["code"] == "unbalanced_entry"
    assert r.data["fields"] == {"debit": ["100.0000"], "credit": ["90.0000"], "difference": ["10.0000"]}
    assert JournalEntry.objects.get(uuid=unb["uuid"]).status == "draft"
    fixed = admin.patch(f"{B}journal-entries/{unb['uuid']}/", {"lines": [L("1110", "debit", 100), L("3100", "credit", 100)]}, format="json")
    assert fixed.data["totals"]["balanced"] is True and len(fixed.data["lines"]) == 2
    done = admin.post(f"{B}journal-entries/{unb['uuid']}/post/")
    assert done.status_code == 200 and done.data["status"] == "posted" and done.data["posted_by"] == "Admin"
    assert done.data["posted_at"] and admin.post(f"{B}journal-entries/{unb['uuid']}/post/").data["code"] == "entry_already_posted"


def test_posted_entries_are_immutable(admin):
    e = make_entry(admin, d(3, 1), [L("1110", "debit", 5), L("3100", "credit", 5)])
    uuid = e["uuid"]
    assert admin.patch(f"{B}journal-entries/{uuid}/", {"memo": "hack"}, format="json").data["code"] == "entry_not_draft"
    assert admin.delete(f"{B}journal-entries/{uuid}/").data["code"] == "entry_not_draft"
    entry = JournalEntry.objects.get(uuid=uuid)
    entry.memo = "tampered"
    with pytest.raises(PermissionError):
        entry.save()
    with pytest.raises(PermissionError):
        entry.delete()
    line = entry.lines.first()
    line.description = "tampered"
    with pytest.raises(PermissionError):
        line.save()
    with pytest.raises(PermissionError):
        line.delete()
    with pytest.raises(PermissionError):  # lines cannot be added to a posted entry either
        JournalLine.objects.create(entry=entry, line_no=9, account=acc("1110"), currency=Currency.objects.get(code="ILS"),
                                   amount_currency=1, rate=1, debit=1, credit=0)


def test_database_rejects_a_line_with_both_sides_or_none(admin_user):
    entry = services.create_entry(actor=admin_user, date=d(3, 1), lines=[])
    base = dict(entry=entry, account=acc("1110"), currency=Currency.objects.get(code="ILS"), amount_currency=1, rate=1)
    for debit, credit in ((5, 5), (0, 0), (-1, 0)):
        with pytest.raises(IntegrityError), transaction.atomic():
            JournalLine.objects.create(line_no=1, debit=debit, credit=credit, **base)


def test_maker_checker(admin):
    maker = make_staff(roles=["accountant"])
    checker = make_staff(roles=["accountant"])
    mc, cc = client_for(maker), client_for(checker)
    e = make_entry(mc, d(3, 1), [L("1110", "debit", 80), L("3100", "credit", 80)], post=False)
    r = mc.post(f"{B}journal-entries/{e['uuid']}/post/")
    assert r.status_code == 403 and r.data["code"] == "cannot_post_own_entry"
    assert JournalEntry.objects.get(uuid=e["uuid"]).status == "draft"
    ok = cc.post(f"{B}journal-entries/{e['uuid']}/post/")
    assert ok.status_code == 200 and ok.data["created_by"] == maker.full_name and ok.data["posted_by"] == checker.full_name
    # admins are exempt
    own = make_entry(admin, d(3, 2), [L("1110", "debit", 1), L("3100", "credit", 1)])
    assert own["status"] == "posted"


def test_only_the_author_edits_or_deletes_a_draft(admin):
    maker = client_for(make_staff(roles=["accountant"]))
    other = client_for(make_staff(roles=["accountant"]))
    e = make_entry(maker, d(3, 1), [L("1110", "debit", 5), L("3100", "credit", 5)], post=False)
    url = f"{B}journal-entries/{e['uuid']}/"
    assert other.patch(url, {"memo": "x"}, format="json").data["code"] == "not_entry_owner"
    assert other.delete(url).status_code == 403
    assert maker.patch(url, {"memo": "mine", "date": str(d(4, 3))}, format="json").data["period"] == f"{Y}-04"
    assert admin.patch(url, {"memo": "admin edit"}, format="json").data["memo"] == "admin edit"  # admin may
    assert maker.delete(url).status_code == 204 and not JournalEntry.objects.filter(uuid=e["uuid"]).exists()
    assert JournalLine.objects.count() == 0


def test_reverse_entry(admin):
    e = make_entry(admin, d(3, 1), [L("1110", "debit", 100), L("3100", "credit", 100)])
    url = f"{B}journal-entries/{e['uuid']}/reverse/"
    assert admin.post(url, {}, format="json").status_code == 400  # reason required
    r = admin.post(url, {"reason": "wrong account", "date": str(d(3, 5))}, format="json")
    assert r.status_code == 201, r.data
    assert r.data["status"] == "posted" and r.data["reversal_of"]["number"] == e["number"]
    assert r.data["memo"].startswith(f"Reversal of {e['number']}")
    flipped = {ln["account"]["code"]: (ln["debit"], ln["credit"]) for ln in r.data["lines"]}
    assert flipped == {"1110": ("0.00", "100.00"), "3100": ("100.00", "0.00")}
    orig = admin.get(f"{B}journal-entries/{e['uuid']}/").data
    assert orig["status"] == "reversed" and orig["reversed_by"]["number"] == r.data["number"] and orig["reverse_reason"] == "wrong account"
    assert admin.post(url, {"reason": "again"}, format="json").data["code"] == "entry_already_reversed"
    assert admin.post(f"{B}journal-entries/{r.data['uuid']}/reverse/", {"reason": "undo undo"}, format="json").data["code"] == "cannot_reverse_reversal"
    draft = make_entry(admin, d(3, 1), [L("1110", "debit", 1), L("3100", "credit", 1)], post=False)
    assert admin.post(f"{B}journal-entries/{draft['uuid']}/reverse/", {"reason": "x"}, format="json").data["code"] == "entry_not_posted"
    # net effect on the books is zero
    tb = admin.get(B + f"reports/trial-balance/?from={Y}-01-01&to={Y}-12-31").data
    assert tb["rows"] == [] or all(r["closing"] == {"debit": "0.00", "credit": "0.00"} for r in tb["rows"])


def test_reversal_needs_an_open_period(admin):
    e = make_entry(admin, d(3, 1), [L("1110", "debit", 100), L("3100", "credit", 100)])
    apr = FiscalPeriod.objects.get(name=f"{Y}-04")
    admin.post(f"{B}fiscal-periods/{apr.uuid}/close/")
    r = admin.post(f"{B}journal-entries/{e['uuid']}/reverse/", {"reason": "x", "date": str(d(4, 10))}, format="json")
    assert r.status_code == 400 and r.data["code"] == "period_closed"
    assert JournalEntry.objects.get(uuid=e["uuid"]).status == "posted"


def test_list_filters(admin):
    ledger(admin)
    draft = make_entry(admin, d(5, 1), [L("1110", "debit", 7), L("3100", "credit", 7)], "needle memo", post=False)
    assert admin.get(B + "journal-entries/").data["count"] == 4
    assert admin.get(B + "journal-entries/?status=draft").data["count"] == 1
    assert admin.get(B + f"journal-entries/?from={d(3, 15)}&to={d(4, 30)}").data["count"] == 2
    assert admin.get(B + "journal-entries/?source=manual").data["count"] == 4
    assert admin.get(B + "journal-entries/?search=needle").data["results"][0]["uuid"] == draft["uuid"]
    assert admin.get(B + f"journal-entries/?account={acc('5400').uuid}").data["count"] == 1
    row = admin.get(B + "journal-entries/?search=JV-000001").data["results"][0]
    assert row["total"] == "1000.00" and row["status"] == "posted" and "lines" not in row


def test_system_entries_post_at_once_with_their_source(admin_user, admin):
    entry = services.create_posted_entry(
        actor=admin_user, date=d(3, 3), memo="Order #7", source_type="order", source_id=7, lines=[
            {"account": acc("1130"), "side": "debit", "amount": 118}, {"account": acc("4100"), "side": "credit", "amount": 100},
            {"account": acc("2120"), "side": "credit", "amount": 18}])
    assert entry.status == EntryStatus.POSTED and entry.source_type == "order" and entry.source_id == "7"
    r = admin.get(B + "journal-entries/?source=order")
    assert r.data["count"] == 1 and r.data["results"][0]["source"] == {"type": "order", "id": "7"}
    maker_made = services.create_entry(actor=admin_user, date=d(3, 4), lines=[
        {"account": acc("1110"), "side": "debit", "amount": 1}, {"account": acc("3100"), "side": "credit", "amount": 1}])
    assert maker_made.created_by_id == admin_user.pk  # maker-checker is not applied to system postings
    with pytest.raises(ServiceError):  # an unbalanced system entry is refused as well
        services.create_posted_entry(actor=admin_user, date=d(3, 3), memo="bad", source_type="order", source_id=8, lines=[
            {"account": acc("1130"), "side": "debit", "amount": 5}, {"account": acc("4100"), "side": "credit", "amount": 4}])
    assert not JournalEntry.objects.filter(source_id="8").exists()


def test_journal_permissions():
    ledger_user = client_for(make_staff(["accounting.view"]))
    assert ledger_user.get(B + "journal-entries/").status_code == 200
    body = payload(d(3, 1), [L("1110", "debit", 1), L("3100", "credit", 1)])
    assert ledger_user.post(B + "journal-entries/", body, format="json").status_code == 403
    creator = client_for(make_staff(["accounting.view", "accounting.create_entry"]))
    e = creator.post(B + "journal-entries/", body, format="json")
    assert e.status_code == 201
    assert creator.post(f"{B}journal-entries/{e.data['uuid']}/post/").status_code == 403  # no post_entry permission
    assert creator.post(f"{B}journal-entries/{e.data['uuid']}/reverse/", {"reason": "x"}, format="json").status_code == 403
    assert client_for(make_customer()).get(B + "journal-entries/").status_code == 403


def test_every_action_is_audited(admin):
    e = make_entry(admin, d(3, 1), [L("1110", "debit", 5), L("3100", "credit", 5)])
    admin.post(f"{B}journal-entries/{e['uuid']}/reverse/", {"reason": "oops"}, format="json")
    actions = set(AuditLog.objects.values_list("action", flat=True))
    assert {"journal.created", "journal.posted", "journal.reversed"} <= actions


# ================================================================== reports


def test_trial_balance(admin):
    ledger(admin)
    make_entry(admin, d(5, 1), [L("1110", "debit", 999), L("3100", "credit", 999)], post=False)  # drafts never count
    tb = admin.get(B + f"reports/trial-balance/?from={Y}-04-01&to={Y}-04-30").data
    assert tb["currency"] == "ILS" and tb["balanced"] is True
    rows = {r["account"]["code"]: r for r in tb["rows"]}
    assert set(rows) == {"1110", "3100", "4100", "5400"}
    assert rows["1110"]["opening"] == {"debit": "1300.00", "credit": "0.00"}
    assert rows["1110"]["movement"] == {"debit": "0.00", "credit": "40.00"}
    assert rows["1110"]["closing"] == {"debit": "1260.00", "credit": "0.00"}
    assert rows["3100"]["closing"] == {"debit": "0.00", "credit": "1000.00"} and rows["4100"]["opening"]["credit"] == "300.00"
    assert rows["5400"]["movement"]["debit"] == "40.00" and rows["5400"]["opening"] == {"debit": "0.00", "credit": "0.00"}
    assert tb["totals"]["closing"] == {"debit": "1300.00", "credit": "1300.00"}
    assert tb["totals"]["movement"] == {"debit": "40.00", "credit": "40.00"}
    assert tb["rows"][0]["account"]["name"] == "الصندوق"
    assert admin.get(B + f"reports/trial-balance/?from={Y}-04-01&to={Y}-04-30&lang=he").data["rows"][0]["account"]["name"] == "קופה"
    full = admin.get(B + f"reports/trial-balance/?from={Y}-01-01&to={Y}-12-31&include_zero=true").data
    assert len(full["rows"]) > 4  # every postable account listed
    assert admin.get(B + "reports/trial-balance/?from=2026-13-01").data["code"] == "invalid_date"
    assert admin.get(B + f"reports/trial-balance/?from={Y}-05-01&to={Y}-04-01").data["code"] == "invalid_range"


def test_general_ledger_running_balance_and_pagination(admin):
    ledger(admin)
    cash = acc("1110")
    r = admin.get(B + f"reports/general-ledger/?account={cash.uuid}&from={Y}-03-01&to={Y}-04-30").data
    assert r["balance_side"] == "debit" and r["opening_balance"] == "0.00" and r["count"] == 3
    assert [x["balance"] for x in r["rows"]] == ["1000.00", "1300.00", "1260.00"]
    assert r["rows"][0]["entry"]["number"] == "JV-000001" and r["rows"][0]["description"] == "capital"
    assert r["totals"] == {"debit": "1300.00", "credit": "40.00"} and r["closing_balance"] == "1260.00"
    p2 = admin.get(B + f"reports/general-ledger/?account={cash.uuid}&from={Y}-03-01&to={Y}-04-30&page=2&page_size=2").data
    assert [x["balance"] for x in p2["rows"]] == ["1260.00"] and p2["page"] == 2  # running total stays correct
    later = admin.get(B + f"reports/general-ledger/?account={cash.uuid}&from={Y}-04-01&to={Y}-04-30").data
    assert later["opening_balance"] == "1300.00" and later["rows"][0]["balance"] == "1260.00"
    sales = admin.get(B + f"reports/general-ledger/?account={acc('4100').uuid}&from={Y}-01-01&to={Y}-12-31").data
    assert sales["balance_side"] == "credit" and sales["rows"][0]["balance"] == "300.00"
    assert admin.get(B + "reports/general-ledger/").data["code"] == "account_required"
    assert admin.get(B + "reports/general-ledger/?account=00000000-0000-0000-0000-000000000000").status_code == 404


def test_income_statement_and_balance_sheet(admin):
    ledger(admin)
    make_entry(admin, d(4, 10), [L("5100", "debit", 100), L("1110", "credit", 100)], "cogs")
    inc = admin.get(B + f"reports/income-statement/?from={Y}-03-01&to={Y}-04-30").data
    assert inc["revenue"]["total"] == "300.00" and inc["cost_of_goods"]["total"] == "100.00"
    assert inc["gross_profit"] == "200.00" and inc["expenses"]["total"] == "40.00" and inc["net_profit"] == "160.00"
    assert inc["revenue"]["rows"][0]["account"]["code"] == "4100" and inc["expenses"]["rows"][0]["amount"] == "40.00"
    bs = admin.get(B + f"reports/balance-sheet/?as_of={Y}-04-30").data
    assert bs["assets"]["total"] == "1160.00" and bs["liabilities"]["total"] == "0.00"
    assert bs["equity"]["total"] == "1000.00" and bs["equity"]["current_earnings"] == "160.00"
    assert bs["equity"]["total_with_earnings"] == "1160.00" and bs["balanced"] is True
    early = admin.get(B + f"reports/balance-sheet/?as_of={Y}-03-15").data
    assert early["assets"]["total"] == "1000.00" and early["equity"]["current_earnings"] == "0.00" and early["balanced"] is True


def test_summary(admin_user, admin):
    ledger(admin)
    make_entry(admin, d(5, 1), [L("1110", "debit", 5), L("3100", "credit", 5)], post=False)
    s = admin.get(B + "accounting/summary/").data
    assert s["currency"] == "ILS" and s["balances"]["cash_bank"] == "1260.00"
    assert s["balances"]["receivables"] == "0.00" and s["balances"]["payables"] == "0.00"
    assert s["alerts"]["draft_entries"] == 1 and s["alerts"]["own_drafts"] == 1
    assert {a["currency"] for a in s["alerts"]["stale_rates"]} == {"USD", "JOD"}  # no rates entered yet
    assert len(s["recent_entries"]) == 4 and s["current_period"]["status"] == "open"
    usd = Currency.objects.get(code="USD")
    fx.set_exchange_rate(actor=admin_user, currency=usd, date=date.today(), rate_to_base=Decimal("3.7"))
    assert {a["currency"] for a in admin.get(B + "accounting/summary/").data["alerts"]["stale_rates"]} == {"JOD"}


def test_csv_exports(admin):
    ledger(admin)
    r = admin.get(B + f"reports/trial-balance/?from={Y}-01-01&to={Y}-12-31&export=csv")
    assert r.status_code == 200 and r["Content-Type"].startswith("text/csv")
    text = r.content.decode("utf-8")
    assert text.startswith("﻿") and "الصندوق" in text and "TOTAL" in text
    cash = acc("1110")
    g = admin.get(B + f"reports/general-ledger/?account={cash.uuid}&from={Y}-01-01&to={Y}-12-31&export=csv")
    assert g.status_code == 200 and "JV-000001" in g.content.decode("utf-8")
    assert admin.get(B + f"reports/income-statement/?export=csv").data["code"] == "export_not_supported"
    no_export = client_for(make_staff(["reports.financial"]))
    assert no_export.get(B + "reports/trial-balance/").status_code == 200
    assert no_export.get(B + "reports/trial-balance/?export=csv").status_code == 403
    assert client_for(make_staff(["reports.financial", "reports.export"])).get(B + "reports/trial-balance/?export=csv").status_code == 200


def test_report_permissions():
    for path in ("trial-balance", "general-ledger", "income-statement", "balance-sheet"):
        assert client_for(make_customer()).get(B + f"reports/{path}/").status_code == 403
        assert client_for(make_staff(["accounting.view"])).get(B + f"reports/{path}/").status_code == 403
    assert client_for(make_staff()).get(B + "accounting/summary/").status_code == 403
    assert client_for(make_staff(["accounting.view"])).get(B + "accounting/summary/").status_code == 200


# ================================================================== tax rates & mappings


def test_tax_rates(admin):
    body = {"name": "VAT", "rate": "16", "collected_account": str(acc("2120").uuid), "paid_account": str(acc("1150").uuid)}
    r = admin.post(B + "tax-rates/", body, format="json")
    assert r.status_code == 201 and r.data["rate"] == "16.0000" and r.data["is_active"] is True
    assert admin.post(B + "tax-rates/", body, format="json").status_code == 400  # duplicate name
    swapped = {**body, "name": "Bad", "collected_account": str(acc("4100").uuid)}
    assert admin.post(B + "tax-rates/", swapped, format="json").data["code"] == "tax_account_type"
    assert admin.post(B + "tax-rates/", {**body, "name": "X", "rate": "120"}, format="json").status_code == 400
    assert admin.patch(f"{B}tax-rates/{r.data['uuid']}/", {"rate": "18", "is_active": False}, format="json").data["rate"] == "18.0000"
    assert len(admin.get(B + "tax-rates/").data) == 1
    viewer = client_for(make_staff(["accounting.view"]))
    assert viewer.get(B + "tax-rates/").status_code == 200
    assert viewer.post(B + "tax-rates/", {**body, "name": "Y"}, format="json").status_code == 403
    assert client_for(make_staff(["accounting.manage_tax"])).post(B + "tax-rates/", {**body, "name": "Z"}, format="json").status_code == 201


def test_account_mappings(admin):
    r = admin.get(B + "account-mappings/").data
    assert r["complete"] is True and r["filled"] == r["total"] == 12
    sales = next(i for i in r["items"] if i["key"] == "sales")
    assert sales["expected_type"] == "revenue" and sales["account"]["code"] == "4100"
    new_revenue = admin.post(B + "accounts/", {"code": "4150", "name_ar": "مبيعات الإنترنت", "name_en": "Online sales",
                                               "name_he": "מכירות אונליין", "type": "revenue", "parent": str(acc("4000").uuid)}, format="json").data
    ok = admin.put(B + "account-mappings/", {"mappings": {"sales": new_revenue["uuid"]}}, format="json")
    assert ok.status_code == 200 and next(i for i in ok.data["items"] if i["key"] == "sales")["account"]["code"] == "4150"
    wrong = admin.put(B + "account-mappings/", {"mappings": {"sales": str(acc("1110").uuid)}}, format="json")
    assert wrong.status_code == 400 and wrong.data["code"] == "mapping_type_mismatch"
    group = admin.put(B + "account-mappings/", {"mappings": {"cash": str(acc("1000").uuid)}}, format="json")
    assert group.data["code"] == "account_not_postable"
    assert admin.put(B + "account-mappings/", {"mappings": {"nope": str(acc("1110").uuid)}}, format="json").status_code == 400
    assert admin.put(B + "account-mappings/", {"mappings": {}}, format="json").status_code == 400
    assert client_for(make_staff(["accounting.view"])).put(B + "account-mappings/", {"mappings": {"sales": new_revenue["uuid"]}}, format="json").status_code == 403
    assert client_for(make_staff(["accounting.view"])).get(B + "account-mappings/").status_code == 200
    AccountMapping.objects.filter(key="fx_loss").delete()
    assert admin.get(B + "account-mappings/").data["complete"] is False
    with pytest.raises(ServiceError):
        services.mapped_account("fx_loss")
