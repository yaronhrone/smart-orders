# apps/users/signals.py

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .models import Profile

User = get_user_model()


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created and instance.is_superuser:

            Profile.objects.create(
                user=instance,
                region="jerusalem",
                company_name="Admin",
                company_phone="0502106833",
                company_address="Tzora",
                phone="0502106833",
                position="admin",
            )
            return
