from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

from apps.accounting.api.urls import admin_patterns as accounting_admin
from apps.accounts.api.urls import admin_patterns, auth_patterns
from apps.catalog.api.urls import admin_patterns as catalog_admin
from apps.currencies.api.urls import admin_patterns as currencies_admin
from apps.inventory.api.urls import admin_patterns as inventory_admin
from apps.sales.api.urls import account_patterns
from apps.sales.api.urls import admin_patterns as sales_admin
from apps.storefront.api.urls import admin_patterns as storefront_admin
from apps.storefront.api.urls import store_patterns

urlpatterns = [
    path("api/v1/auth/", include(auth_patterns)),
    path("api/v1/admin/", include(admin_patterns)),
    path("api/v1/admin/", include(currencies_admin)),
    path("api/v1/admin/", include(catalog_admin)),
    path("api/v1/admin/", include(accounting_admin)),
    path("api/v1/admin/", include(inventory_admin)),
    path("api/v1/admin/", include(sales_admin)),
    path("api/v1/admin/", include(storefront_admin)),
    path("api/v1/store/", include(store_patterns)),
    path("api/v1/account/", include(account_patterns)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
]

if settings.DEBUG:  # uploaded product images are served by Django only in development
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
