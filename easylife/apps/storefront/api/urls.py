from django.urls import path
from rest_framework.routers import SimpleRouter

from . import admin_views, views

router = SimpleRouter()
router.register("categories", views.CategoryViewSet, basename="store-categories")
router.register("products", views.ProductViewSet, basename="store-products")
router.register("offers", views.OfferViewSet, basename="store-offers")
router.register("delivery-areas", views.DeliveryAreaViewSet, basename="store-delivery-areas")

store_patterns = router.urls + [path("about/", views.AboutView.as_view(), name="store-about"),
                                path("ads/", views.StoreAdListView.as_view()),
                                path("orders/", views.OnlineOrderCreateView.as_view()),
                                path("orders/<uuid:token>/send-invoice/", views.OnlineOrderSendInvoiceView.as_view()),
                                path("orders/<uuid:token>/", views.OnlineOrderView.as_view())]
account_patterns = [path("favorites/", views.MyFavoritesView.as_view()),
                    path("favorites/<slug:slug>/", views.FavoriteToggleView.as_view()),
                    path("online-orders/", views.MyOnlineOrdersView.as_view()),
                    path("online-orders/<uuid:uuid>/cancel/", views.MyOnlineOrderCancelView.as_view())]
admin_patterns = [path("store-profile/", admin_views.ShopProfileView.as_view(), name="admin-store-profile"),
                  path("ads/", admin_views.StoreAdListCreateView.as_view()),
                  path("ads/<uuid:uuid>/", admin_views.StoreAdDetailView.as_view()),
                  path("delivery-areas/", admin_views.DeliveryAreaListCreateView.as_view()),
                  path("delivery-areas/<uuid:uuid>/", admin_views.DeliveryAreaDetailView.as_view()),
                  path("online-orders/", admin_views.OnlineOrderListView.as_view()),
                  path("online-orders/<uuid:uuid>/", admin_views.OnlineOrderDetailView.as_view())]
