from django.apps import AppConfig


class CyclesConfig(AppConfig):
    name = "apps.cycles"
    verbose_name = "Financial Cycles"

    def ready(self):
        from . import signals  # noqa: F401
