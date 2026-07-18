from django.urls import path
from drf_foundation.auth import LoginView, RefreshView, logout

from accounts import views

urlpatterns = [
    path("auth/register", views.register, name="auth-register"),
    path("auth/login", LoginView.as_view(), name="auth-login"),
    path("auth/refresh", RefreshView.as_view(), name="auth-refresh"),
    path("auth/logout", logout, name="auth-logout"),
    path("me", views.me, name="me"),
]
