"""
Creates the two Twilio quick-reply templates the supplier buttons use, once:

    python manage.py create_whatsapp_buttons

and prints the two SIDs to put in .env.prod (then restart web + worker):

    WHATSAPP_CONTENT_SID_ACTIONS=HX...   buttons  אישור / חסר / ביטול
    WHATSAPP_CONTENT_SID_SHIPPED=HX...   button   יצא למשלוח

The template body is one variable ({{1}}): the whole message text. A message that
opens a conversation outside WhatsApp's 24-hour window must be a template Meta
approved (see meta-templates-submission.md); inside a conversation the supplier
just answered, these send as they are. When Twilio refuses one, the supplier gets
the plain text with typed instructions instead (validators.send_with_buttons).
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from twilio.rest import Client
from twilio.rest.content.v1.content import ContentList

TEMPLATES = {
    "WHATSAPP_CONTENT_SID_ACTIONS": (
        "smart_orders_supplier_actions",
        [("אישור", "confirm"), ("חסר", "missing"), ("ביטול", "cancel")],
    ),
    "WHATSAPP_CONTENT_SID_SHIPPED": (
        "smart_orders_supplier_shipped",
        [("יצא למשלוח", "shipped")],
    ),
}


class Command(BaseCommand):
    help = "Create the Twilio quick-reply button templates for suppliers and print their SIDs."

    def handle(self, *args, **options):
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        for env_name, (friendly_name, buttons) in TEMPLATES.items():
            content = client.content.v1.contents.create(
                content_create_request=ContentList.ContentCreateRequest({
                    "friendly_name": friendly_name,
                    "language": "he",
                    "variables": {"1": "הודעה"},
                    "types": {"twilio/quick-reply": {
                        "body": "{{1}}",
                        "actions": [{"title": title, "id": button_id} for title, button_id in buttons],
                    }},
                })
            )
            self.stdout.write(f"{env_name}={content.sid}")
