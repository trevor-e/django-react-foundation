from django.urls import path

from accounts import views

urlpatterns = [
    path("auth/register", views.register, name="auth-register"),
    path("auth/login", views.LoginView.as_view(), name="auth-login"),
    path("auth/refresh", views.RefreshView.as_view(), name="auth-refresh"),
    path("auth/logout", views.logout, name="auth-logout"),
    path("me", views.me, name="me"),
]
