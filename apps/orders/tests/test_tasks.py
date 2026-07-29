from unittest.mock import patch

from celery.exceptions import Retry
from django.test import TestCase

from apps.orders.tasks import send_supplier_order_notification_task


class SendSupplierOrderNotificationTaskTests(TestCase):

    @patch("apps.orders.tasks.send_whatsapp_message")
    def test_sends_message_to_supplier(self, mock_send):
        send_supplier_order_notification_task(phone="+972501234567", message="הזמנה חדשה")

        mock_send.assert_called_once_with("+972501234567", "הזמנה חדשה")

    @patch("apps.orders.tasks.send_whatsapp_message")
    def test_retries_on_send_failure(self, mock_send):
        """A Twilio failure calls self.retry() instead of being swallowed silently."""
        mock_send.side_effect = Exception("twilio down")

        with self.assertRaises(Retry):
            send_supplier_order_notification_task.apply(
                args=("+972501234567", "הזמנה חדשה"), throw=True
            )

        mock_send.assert_called_once_with("+972501234567", "הזמנה חדשה")
