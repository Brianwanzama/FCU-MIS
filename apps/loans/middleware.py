import logging
from datetime import date

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

_checked_on: date | None = None


class DailyLoanRecoveryMiddleware:
    """
    Runs the automatic loan recovery check the first time anyone signs in and uses
    the system each day, so recovery happens with no scheduler. The work is
    recorded once per day in the database, so any number of servers or visits
    still produce one check.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        global _checked_on

        response = self.get_response(request)

        if settings.FCU_LOAN_RECOVERY_ON_REQUEST and request.user.is_authenticated:
            today = timezone.localdate()

            if _checked_on != today:
                _checked_on = today

                try:
                    from .services import run_daily_recovery_if_due

                    run_daily_recovery_if_due(today)
                except Exception:
                    logger.exception("Daily loan recovery check failed")

        return response
