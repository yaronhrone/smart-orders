from django.core.management.base import BaseCommand
from apps.orders.whatsapp import send_whatsapp_message


class Command(BaseCommand):
    help = "Send a test WhatsApp message via Twilio Sandbox"

    def add_arguments(self, parser):
        parser.add_argument("phone", help="Destination number, e.g. +972501234567")

    def handle(self, *args, **options):
        phone = options["phone"]
        sid = send_whatsapp_message(phone, "בדיקה מ-smart-orders! הכל עובד.")
        self.stdout.write(self.style.SUCCESS(f"Message sent. SID: {sid}"))
