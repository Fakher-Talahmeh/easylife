from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("warehouses", views.WarehouseViewSet, basename="admin-warehouses")
router.register("stock/movements", views.MovementViewSet, basename="admin-stock-movements")
router.register("stock/adjustments", views.AdjustmentViewSet, basename="admin-stock-adjustments")
router.register("stock/transfers", views.TransferViewSet, basename="admin-stock-transfers")
router.register("stock/count-sheet", views.CountSheetViewSet, basename="admin-stock-count-sheet")
router.register("stock", views.StockViewSet, basename="admin-stock")

admin_patterns = router.urls
