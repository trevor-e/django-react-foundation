"""WSGI entrypoint — not served by any process role (granian serves config.asgi,
blueprint §11a), kept stock for tooling that expects it."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()
