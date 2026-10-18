from django.urls import path
from rest_framework.routers import SimpleRouter

from . import admin_views, views

router = SimpleRouter()
router.register("categories", views.CategoryViewSet, basename="store-categories")
router.register("brands", views.BrandViewSet, basename="store-brands")
router.register("products", views.ProductViewSet, basename="store-products")

store_patterns = router.urls + [path("about/", views.AboutView.as_view(), name="store-about")]
admin_patterns = [path("store-profile/", admin_views.ShopProfileView.as_view(), name="admin-store-profile")]
