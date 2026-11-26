"""
The Celery broker must actually be reachable with the installed library versions.

celery>=5.3,<6 let a rebuild pick celery/kombu 5.7, whose Redis transport refuses to work with the
pinned redis-py 5.0.4 ("Redis transport requires redis-py versions 6.1.0 or later"). Nothing in the
suite touched the broker, so CI stayed green while every task in production (voice messages first)
failed to be scheduled. This test publishes and reads one message through the real transport.
It is skipped only when no Redis answers (CI has one).
"""
from django.conf import settings
from django.test import SimpleTestCase


class CeleryBrokerTests(SimpleTestCase):

    def test_a_message_goes_through_the_redis_transport(self):
        from kombu import Connection

        try:
            with Connection(settings.CELERY_BROKER_URL) as connection:
                connection.ensure_connection(max_retries=1)
                queue = connection.SimpleQueue("broker-compatibility-check")
                queue.put({"ok": True})
                message = queue.get(timeout=3)
                message.ack()
                queue.close()
        except Exception as exc:
            if type(exc).__name__ == "VersionMismatch":
                self.fail(f"celery/kombu and redis-py do not fit together: {exc}")
            self.skipTest(f"no Redis to talk to here ({type(exc).__name__})")
        self.assertEqual(message.payload, {"ok": True})
