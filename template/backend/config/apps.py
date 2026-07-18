from django.apps import AppConfig


class ConfigApp(AppConfig):
    """Installed only so config/checks.py's system checks register."""

    name = "config"

    def ready(self) -> None:
        from config import checks  # noqa: F401
