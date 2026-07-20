"""
Business services for cycle management.
"""

from apps.cycles.models import Cycle


def get_active_cycle():
    """
    Return the current ACTIVE cycle.

    Returns
    -------
    Cycle | None
        The active cycle if one exists, otherwise None.
    """
    return (
        Cycle.objects
        .filter(status=Cycle.Status.ACTIVE)
        .first()
    )