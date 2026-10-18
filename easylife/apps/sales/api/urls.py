from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("pos/sales", views.SaleViewSet, basename="admin-pos-sales")
router.register("pos/shifts", views.ShiftViewSet, basename="admin-pos-shifts")

admin_patterns = [
    path("reports/sales/", views.SalesReportView.as_view()),
    path("pos/config/", views.ConfigView.as_view()),
    path("pos/products/", views.ProductSearchView.as_view()),
    path("pos/shift/", views.ShiftCurrentView.as_view()),
    path("pos/shift/open/", views.ShiftOpenView.as_view()),
    path("pos/shift/close/", views.ShiftCloseView.as_view()),
    path("customers/<uuid:uuid>/account/", views.CustomerAccountView.as_view()),
    path("customers/<uuid:uuid>/invoices/", views.CustomerInvoicesView.as_view()),
    path("customers/<uuid:uuid>/payments/", views.CustomerDebtPaymentsView.as_view()),
    *router.urls,
]

# the signed-in customer's own account (debts and invoices)
account_patterns = [
    path("summary/", views.MyAccountView.as_view()),
    path("invoices/", views.MyInvoicesView.as_view()),
    path("invoices/<uuid:uuid>/", views.MyInvoiceView.as_view()),
    path("payments/", views.MyDebtPaymentsView.as_view()),
]
