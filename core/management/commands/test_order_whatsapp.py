from django.core.management.base import BaseCommand
from apps.catalog.models import Product
from apps.orders.services import suggest_order
from apps.orders.whatsapp import send_whatsapp_message
from apps.orders.whatsapp import save_pending_order
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = "Run order algorithm and send WhatsApp with recommendations"

    def handle(self, *args, **options):
        User = get_user_model()
        user = User.objects.first()

        products = [
            {"product": Product.objects.get(name="עגבנייה"), "quantity": 10},
            {"product": Product.objects.get(name="מלפפון"), "quantity": 5},
            {"product": Product.objects.get(name="חציל"), "quantity": 8},
            {"product": Product.objects.get(name="פלפל"), "quantity": 3},
        ]

        result = suggest_order(user, "center", products)
        cheapest = result["cheapest"]

        def format_scenario(label, s):
            rows = [f"*{label}*"]
            for p in s["products"]:
                rows.append(f"  • {p['product_name']} x{p['quantity']} — {p['supplier_name']} — {p['subtotal']}₪")
            rows.append(f"סה\"כ: {s['total_price']}₪ ({s['supplier_count']} ספקים)")
            return rows

        lines = ["*הזמנה מומלצת — smart-orders*", ""]
        lines += format_scenario("ההזמנה הזולה ביותר", cheapest)

        save_pending_order("+972502106833", cheapest)

        body = "\n".join(lines) + "\n\nענה *כן* לאישור."
        self.stdout.write(body)
        self.stdout.write("")

        sid = send_whatsapp_message("+972502106833", body)
        self.stdout.write(self.style.SUCCESS(f"Sent! SID: {sid}"))
