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
        fewest = result["fewest_suppliers"]

        def scenario_key(s):
            return (s["total_price"], tuple(sorted((p["supplier_id"], p["product_id"]) for p in s["products"])))

        same = scenario_key(cheapest) == scenario_key(fewest)

        def format_scenario(label, s):
            rows = [f"*{label}*"]
            for p in s["products"]:
                rows.append(f"  • {p['product_name']} x{p['quantity']} — {p['supplier_name']} — {p['subtotal']}₪")
            rows.append(f"סה\"כ: {s['total_price']}₪ ({s['supplier_count']} ספקים)")
            return rows

        lines = ["*הזמנה מומלצת — smart-orders*", ""]
        if same:
            lines += format_scenario("אפשרות יחידה: הזול עם הכי פחות ספקים", cheapest)
        else:
            lines += format_scenario("אפשרות א׳ — הזול ביותר", cheapest)
            lines.append("")
            lines += format_scenario("אפשרות ב׳ — הכי פחות ספקים", fewest)

        save_pending_order("+972502106833", cheapest, fewest)

        body = "\n".join(lines)
        if not same:
            body += "\n\nענה *א* או *ב* לאישור."
        else:
            body += "\n\nענה *א* לאישור."
        self.stdout.write(body)
        self.stdout.write("")

        sid = send_whatsapp_message("+972502106833", body)
        self.stdout.write(self.style.SUCCESS(f"Sent! SID: {sid}"))
