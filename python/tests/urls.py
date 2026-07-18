from django.urls import path

from drf_foundation.auth import LoginView, RefreshView, logout
from drf_foundation.views import health_check

urlpatterns = [
    path("api/health", health_check, name="health-check"),
    path("api/auth/login", LoginView.as_view(), name="auth-login"),
    path("api/auth/refresh", RefreshView.as_view(), name="auth-refresh"),
    path("api/auth/logout", logout, name="auth-logout"),
]
