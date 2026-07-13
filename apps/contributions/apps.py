from django.apps import AppConfig


class ContributionsConfig(AppConfig):
    """
    Contribution management.

    Stores every financial contribution made by members.
    Higher-level summaries (balances, months contributed,
    outstanding amounts, etc.) are derived from these
    transactions rather than stored directly.
    """

    default_auto_field = "django.db.models.BigAutoField"

    name = "apps.contributions"

    verbose_name = "Contributions"