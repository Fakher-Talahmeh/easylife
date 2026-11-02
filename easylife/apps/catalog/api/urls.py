from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("categories", views.CategoryViewSet, basename="admin-categories")
router.register("price-lists", views.PriceListViewSet, basename="admin-price-lists")
router.register("offers", views.OfferViewSet, basename="admin-offers")
router.register("products", views.ProductViewSet, basename="admin-products")
router.register("variants", views.VariantViewSet, basename="admin-variants")
router.register("images", views.ImageViewSet, basename="admin-images")

admin_patterns = router.urls
