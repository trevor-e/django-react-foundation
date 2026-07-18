from django.contrib import admin
from django.urls import include, path
from drf_foundation.views import health_check

urlpatterns = [
    path("admin/", admin.site.urls),
    # Also the Railway deploy healthcheck target (blueprint §11b) — the package's
    # production_security_settings exempts exactly this path from the SSL redirect.
    path("api/health", health_check, name="health-check"),
    path("api/", include("accounts.urls")),
]
