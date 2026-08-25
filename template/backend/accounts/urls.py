from django.urls import path
from drf_foundation.session_auth import LoginView, csrf_token, logout

from accounts import views

urlpatterns = [
    # The CSRF bootstrap a cross-origin SPA needs before its first unsafe request:
    # under CSRF_USE_SESSIONS there is no readable cookie to lift the token from.
    path("auth/csrf", csrf_token, name="auth-csrf"),
    path("auth/register", views.register, name="auth-register"),
    path("auth/login", LoginView.as_view(), name="auth-login"),
    path("auth/logout", logout, name="auth-logout"),
    path("me", views.me, name="me"),
]
