"""
Creates the Twilio quick-reply templates the buttons use (suppliers and customers), once:

    python manage.py create_whatsapp_buttons

and prints the SIDs to put in .env.prod (then restart web + worker):

    WHATSAPP_CONTENT_SID_ACTIONS=HX...   buttons  אישור / חסר / ביטול
    WHATSAPP_CONTENT_SID_SHIPPED=HX...   button   יצא למשלוח
    WHATSAPP_CONTENT_SID_ADDITION=HX...  buttons  אישור / חסר       (supplier: an addition to an order)
    WHATSAPP_CONTENT_SID_REDUCTION=HX... buttons  אשר הפחתה / לא להפחית (supplier: a reduction)
    WHATSAPP_CONTENT_SID_YESNO=HX...     buttons  כן / לא          (customer: add to open order, alternative supplier)
    WHATSAPP_CONTENT_SID_CONFIRM=HX...   buttons  אישור / ביטול    (customer: approve the order)
    WHATSAPP_CONTENT_SID_SEND=HX...      buttons  שלח / ביטול      (customer: an order held under a supplier minimum)
    WHATSAPP_CONTENT_SID_CANCELORDER=HX... button  ביטול הזמנה     (customer: under the confirmed order / the order held under a minimum)
    WHATSAPP_CONTENT_SID_RECEIVED=HX...  button   קיבלתי           (customer: the order is on its way / the supplier gave a time)

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
    # Changes to an order a supplier already has
    "WHATSAPP_CONTENT_SID_ADDITION": (
        "smart_orders_supplier_addition",
        [("אישור", "confirm"), ("חסר", "missing")],
    ),
    "WHATSAPP_CONTENT_SID_REDUCTION": (
        "smart_orders_supplier_reduction",
        [("אשר הפחתה", "confirm"), ("לא להפחית", "refuse")],
    ),
    # Customers
    "WHATSAPP_CONTENT_SID_YESNO": (
        "smart_orders_customer_yesno",
        [("כן", "yes"), ("לא", "no")],
    ),
    "WHATSAPP_CONTENT_SID_CONFIRM": (
        "smart_orders_customer_confirm",
        [("אישור", "confirm"), ("ביטול", "cancel")],
    ),
    "WHATSAPP_CONTENT_SID_SEND": (
        "smart_orders_customer_send",
        [("שלח", "send"), ("ביטול", "cancel")],
    ),
    "WHATSAPP_CONTENT_SID_CANCELORDER": (
        "smart_orders_customer_cancel_order",
        [("ביטול הזמנה", "cancel_order")],
    ),
    "WHATSAPP_CONTENT_SID_RECEIVED": (
        "smart_orders_customer_received",
        [("קיבלתי", "received")],
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
