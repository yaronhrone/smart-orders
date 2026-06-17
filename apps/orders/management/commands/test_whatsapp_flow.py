"""
Simulates the full WhatsApp fallback flow without actually sending messages.
Prints every message that WOULD be sent to WhatsApp.

Usage:
    python manage.py test_whatsapp_flow
    python manage.py test_whatsapp_flow --step=2   # jump to customer approval
    python manage.py test_whatsapp_flow --cleanup  # delete test orders
"""
from collections import defaultdict
from decimal import Decimal
from unittest.mock import patch

from django.core.management.base import BaseCommand
from django.core.cache import cache


CUSTOMER_PHONE = "0502555555"   # itay
SUPPLIER_A_PHONE = "+972521234567"  # אבי ירקות
SUPPLIER_A_NAME = "אבי ירקות - שוק הכרמל"


class Command(BaseCommand):
    help = "Test WhatsApp fallback flow without sending real messages"

    def add_arguments(self, parser):
        parser.add_argument("--step", type=int, default=1,
                            help="1=full flow, 2=customer approval only, 3=show cache state, 4=auto-transfer test")
        parser.add_argument("--cleanup", action="store_true",
                            help="Delete test orders and clear cache")
        parser.add_argument("--missing", default="חסר עגבניות, שאר אישור",
                            help="Supplier reply message to simulate")
        parser.add_argument("--customer-reply", default="כן",
                            help="Customer reply to fallback offer (כן / לא)")

    def handle(self, *args, **options):
        if options["cleanup"]:
            self._cleanup()
            return

        step = options["step"]
        if step == 3:
            self._show_cache()
            return
        if step == 4:
            self._test_auto_transfer()
            return
        if step == 2:
            self._step2_customer_approval(options["customer_reply"])
            return

        # Full flow: step 1 → step 2
        order = self._step1_supplier_missing(options["missing"])
        if order:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("─" * 60))
            self.stdout.write(self.style.WARNING("עכשיו מדמה את תגובת הלקוח..."))
            self.stdout.write(self.style.WARNING("─" * 60))
            self._step2_customer_approval(options["customer_reply"])

    # ── Step 1: create order + supplier says "חסר" ──────────────────

    def _step1_supplier_missing(self, supplier_body: str):
        from apps.catalog.models import Supplier, Product
        from apps.orders.models import OrderRequest, OrderRequestProduct
        from apps.users.models import Profile
        from apps.orders.whatsapp_webhook import save_supplier_pending_order

        self.stdout.write(self.style.SUCCESS("\n══ שלב 1: יצירת הזמנה ← ספק מדווח על חסר ══\n"))

        # Resolve user
        profile = Profile.objects.filter(phone=CUSTOMER_PHONE).select_related("user").first()
        if not profile:
            self.stderr.write(f"לא נמצא פרופיל עם מספר {CUSTOMER_PHONE}")
            return None
        user = profile.user
        self.stdout.write(f"  לקוח: {profile.company_name} ({user.email})")

        # Resolve suppliers
        try:
            supplier_a = Supplier.objects.get(whatsapp_number=SUPPLIER_A_PHONE)
        except Supplier.DoesNotExist:
            self.stderr.write(f"לא נמצא ספק {SUPPLIER_A_NAME}")
            return None
        self.stdout.write(f"  ספק A: {supplier_a.name} (מינימום ₪{supplier_a.minimum_order})")

        # Resolve product
        tomato = Product.objects.filter(name="עגבניה").first()
        if not tomato:
            self.stderr.write("לא נמצא מוצר 'עגבניה'")
            return None

        # Create a SENT order with 100kg tomatoes from supplier A
        quantity = Decimal("100")
        unit_price = Decimal("4.90")
        order = OrderRequest.objects.create(
            user=user,
            total_price=quantity * unit_price,
            status=OrderRequest.Status.SENT,
        )
        orp = OrderRequestProduct.objects.create(
            order_request=order,
            product=tomato,
            supplier=supplier_a,
            quantity=quantity,
            unit_price=unit_price,
        )
        self.stdout.write(f"  הזמנה #{order.id}: {quantity}ק\"ג עגבניה × {unit_price}₪ = {order.total_price}₪")
        self.stdout.write(f"  סטטוס: {order.status}")

        # Put supplier pending in cache
        save_supplier_pending_order(
            supplier_phone=SUPPLIER_A_PHONE,
            order_request_id=order.id,
            products=[{
                "orp_id": orp.id,
                "product_name": tomato.name,
                "quantity": str(quantity),
                "unit": tomato.get_unit_display(),
            }],
        )

        # Simulate supplier reply
        self.stdout.write(f"\n  📱 ספק A שולח: \"{supplier_body}\"\n")
        messages = self._run_with_mock(
            lambda: self._call_supplier_webhook(SUPPLIER_A_PHONE, supplier_a, supplier_body)
        )
        self._print_messages(messages)
        return order

    def _call_supplier_webhook(self, phone, supplier, body):
        from apps.orders.whatsapp_webhook import _handle_supplier_flow
        _handle_supplier_flow(phone=phone, supplier=supplier, body=body)

    # ── Step 2: customer approves fallback ───────────────────────────

    def _step2_customer_approval(self, customer_reply: str):
        from apps.orders.whatsapp_webhook import _handle_user_flow

        self.stdout.write(self.style.SUCCESS("\n══ שלב 2: לקוח עונה על הצעת הספק החלופי ══\n"))

        # Check fallback state
        raw = cache.get(f"whatsapp_fallback:{CUSTOMER_PHONE}")
        if not raw:
            self.stdout.write(self.style.WARNING("  אין מצב fallback פעיל בקאש. הרץ שלב 1 קודם."))
            return

        self.stdout.write(f"  📱 לקוח שולח: \"{customer_reply}\"\n")
        messages = self._run_with_mock(
            lambda: _handle_user_flow(phone=CUSTOMER_PHONE, body=customer_reply)
        )
        self._print_messages(messages)

    # ── Helpers ──────────────────────────────────────────────────────

    def _run_with_mock(self, fn):
        messages = []

        def capture(to, body):
            messages.append({"to": to, "body": body})
            return "mock_sid"

        with patch("apps.orders.whatsapp_webhook.send_whatsapp_message", side_effect=capture):
            try:
                fn()
            except Exception as exc:
                self.stderr.write(f"שגיאה: {exc}")
        return messages

    def _print_messages(self, messages):
        if not messages:
            self.stdout.write(self.style.WARNING("  לא נשלחו הודעות."))
            return
        for msg in messages:
            self.stdout.write(self.style.HTTP_INFO(f"  📤 אל: {msg['to']}"))
            self.stdout.write("  " + "─" * 50)
            for line in msg["body"].split("\n"):
                self.stdout.write(f"  {line}")
            self.stdout.write("")

    def _test_auto_transfer(self):
        """
        Scenario: order has 2 products from supplier A.
        Supplier A says 'חסר עגבניות'. Customer says 'לא'.
        After removal, supplier A drops below minimum → auto-transfer remaining items to B.
        """
        from apps.catalog.models import Supplier, Product
        from apps.orders.models import OrderRequest, OrderRequestProduct
        from apps.users.models import Profile
        from apps.orders.whatsapp_webhook import save_supplier_pending_order

        self.stdout.write(self.style.SUCCESS("\n══ בדיקת Auto-Transfer ══\n"))

        profile = Profile.objects.filter(phone=CUSTOMER_PHONE).select_related("user").first()
        supplier_a = Supplier.objects.get(whatsapp_number=SUPPLIER_A_PHONE)
        tomato = Product.objects.get(name="עגבניה")
        carrot = Product.objects.get(name="גזר")

        # Order: 20kg tomatoes + 20kg carrot from supplier A
        # Total = 20*4.90 + 20*3.50 = 98 + 70 = 168₪ (below 500₪ minimum)
        # After removing tomatoes: 20*3.50 = 70₪ (still below 500₪ → auto-transfer גזר to B)
        order = OrderRequest.objects.create(
            user=profile.user,
            total_price=168,
            status=OrderRequest.Status.SENT,
        )
        orp_t = OrderRequestProduct.objects.create(
            order_request=order, product=tomato, supplier=supplier_a,
            quantity=Decimal("20"), unit_price=Decimal("4.90"),
        )
        orp_c = OrderRequestProduct.objects.create(
            order_request=order, product=carrot, supplier=supplier_a,
            quantity=Decimal("20"), unit_price=Decimal("3.50"),
        )
        self.stdout.write(f"  הזמנה #{order.id}: 20ק\"ג עגבניה + 20ק\"ג גזר מ-{supplier_a.name}")
        self.stdout.write(f"  סה\"כ: 168₪ (מינימום ספק: {supplier_a.minimum_order}₪)\n")

        save_supplier_pending_order(
            supplier_phone=SUPPLIER_A_PHONE,
            order_request_id=order.id,
            products=[
                {"orp_id": orp_t.id, "product_name": tomato.name, "quantity": "20", "unit": tomato.get_unit_display()},
                {"orp_id": orp_c.id, "product_name": carrot.name, "quantity": "20", "unit": carrot.get_unit_display()},
            ],
        )

        # Step 1: supplier says "חסר עגבניות, גזר אישור"
        self.stdout.write(f"  📱 ספק A: \"חסר עגבניות, גזר אישור\"\n")
        msgs1 = self._run_with_mock(
            lambda: self._call_supplier_webhook(SUPPLIER_A_PHONE, supplier_a, "חסר עגבניות, גזר אישור")
        )
        self._print_messages(msgs1)

        # Step 2: customer says "לא"
        self.stdout.write(self.style.WARNING("─" * 60))
        self.stdout.write(f"  📱 לקוח: \"לא\"\n")
        msgs2 = self._run_with_mock(
            lambda: self._call_user_webhook(CUSTOMER_PHONE, "לא")
        )
        self._print_messages(msgs2)

        # Cleanup
        order.refresh_from_db()
        self.stdout.write(f"  ניקוי הזמנה #{order.id}...")
        OrderRequestProduct.objects.filter(order_request=order).delete()
        order.delete()

    def _call_user_webhook(self, phone, body):
        from apps.orders.whatsapp_webhook import _handle_user_flow
        _handle_user_flow(phone=phone, body=body)

    def _show_cache(self):
        import json
        self.stdout.write(self.style.SUCCESS("\n══ מצב קאש ══\n"))
        for key_suffix in [
            f"whatsapp_fallback:{CUSTOMER_PHONE}",
            f"whatsapp_supplier_pending:{SUPPLIER_A_PHONE}",
            f"whatsapp_order:{CUSTOMER_PHONE}",
        ]:
            val = cache.get(key_suffix)
            if val:
                self.stdout.write(f"  {key_suffix}:")
                try:
                    self.stdout.write("  " + json.dumps(json.loads(val), indent=2, ensure_ascii=False))
                except Exception:
                    self.stdout.write(f"  {val}")
            else:
                self.stdout.write(f"  {key_suffix}: (ריק)")
        self.stdout.write("")

    def _cleanup(self):
        from apps.orders.models import OrderRequest
        from apps.users.models import Profile

        profile = Profile.objects.filter(phone=CUSTOMER_PHONE).select_related("user").first()
        if profile:
            deleted, _ = OrderRequest.objects.filter(
                user=profile.user, status=OrderRequest.Status.SENT
            ).delete()
            self.stdout.write(f"נמחקו {deleted} הזמנות בסטטוס SENT")

        cache.delete(f"whatsapp_fallback:{CUSTOMER_PHONE}")
        cache.delete(f"whatsapp_supplier_pending:{SUPPLIER_A_PHONE}")
        cache.delete(f"whatsapp_order:{CUSTOMER_PHONE}")
        self.stdout.write("קאש נוקה")
