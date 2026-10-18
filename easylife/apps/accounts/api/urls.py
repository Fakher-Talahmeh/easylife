from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("users", views.StaffViewSet, basename="admin-users")
router.register("customers", views.CustomerViewSet, basename="admin-customers")

auth_patterns = [
    path("whatsapp/send-code", views.WhatsAppSendCodeView.as_view()),
    path("whatsapp/verify", views.WhatsAppVerifyView.as_view()),
    path("refresh", views.RefreshView.as_view()),
    path("logout", views.LogoutView.as_view()),
    path("me", views.MeView.as_view()),
]

admin_patterns = [
    path("roles", views.RoleListView.as_view()),
    path("", include(router.urls)),
]
