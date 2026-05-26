from django.conf import settings
from twilio.rest import Client


def send_whatsapp_message(to_number: str, body: str) -> str:
    """
    Send a WhatsApp message via Twilio Sandbox.
    to_number: Israeli format, e.g. "+972501234567"
    Returns the Twilio message SID.
    """
    client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    from_wa = f"whatsapp:{settings.TWILIO_WHATSAPP_NUMBER}"
    to_wa = f"whatsapp:{to_number}"
    message = client.messages.create(body=body, from_=from_wa, to=to_wa)
    return message.sid


def send_order_to_supplier(supplier, assignments: list) -> str:
    lines = ["שלום, ברצוני להזמין:"]
    for a in assignments:
        lines.append(
            f"- {a['product'].name} x{a['quantity']} {a['product'].get_unit_display()}"
        )
    lines.append("תודה!")
    body = "\n".join(lines)
    return send_whatsapp_message(supplier.whatsapp_number, body)
