"""
Keep the active cycle's membership record in step with member status changes.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.members.models import Member

from .models import Cycle, CycleMembership


@receiver(post_save, sender=Member)
def sync_active_cycle_membership(sender, instance, **kwargs):
    cycle = Cycle.objects.filter(status=Cycle.Status.ACTIVE).first()

    if cycle is None:
        return

    today = timezone.localdate()

    if instance.status == Member.Status.ACTIVE:
        # Joined (or became Active again) during the cycle: owes the full amount.
        CycleMembership.objects.get_or_create(
            cycle=cycle, member=instance, defaults={"joined_on": today}
        )
    elif instance.status == Member.Status.WITHDRAWN:
        CycleMembership.objects.filter(
            cycle=cycle, member=instance, left_on__isnull=True
        ).update(left_on=today)
