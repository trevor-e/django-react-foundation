from django.contrib import admin
from django.urls import include, path

from config.views import health_check

urlpatterns = [
    path("admin/", admin.site.urls),
    # Also the Railway deploy healthcheck target (blueprint §11b) — the path is
    # exempted from the production SSL redirect in settings.py; keep the two in sync.
    path("api/health", health_check, name="health-check"),
    path("api/", include("accounts.urls")),
]
