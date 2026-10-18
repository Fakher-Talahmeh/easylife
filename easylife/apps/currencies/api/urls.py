from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("currencies", views.CurrencyViewSet, basename="admin-currencies")
router.register("exchange-rates", views.ExchangeRateViewSet, basename="admin-exchange-rates")

admin_patterns = router.urls
