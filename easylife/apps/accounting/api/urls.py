from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("accounts", views.AccountViewSet, basename="admin-accounts")
router.register("fiscal-periods", views.FiscalPeriodViewSet, basename="admin-fiscal-periods")
router.register("tax-rates", views.TaxRateViewSet, basename="admin-tax-rates")
router.register("journal-entries", views.JournalEntryViewSet, basename="admin-journal-entries")

admin_patterns = [
    path("account-mappings/", views.MappingViewSet.as_view({"get": "list", "put": "replace"})),
    path("accounting/summary/", views.SummaryView.as_view()),
    path("reports/trial-balance/", views.TrialBalanceView.as_view()),
    path("reports/general-ledger/", views.GeneralLedgerView.as_view()),
    path("reports/income-statement/", views.IncomeStatementView.as_view()),
    path("reports/balance-sheet/", views.BalanceSheetView.as_view()),
    *router.urls,
]
