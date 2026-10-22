import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api  # noqa: E402
import drafts  # noqa: E402
import server  # noqa: E402


def ctx(token="so_alice"):
    request = SimpleNamespace(headers={"authorization": f"Bearer {token}"})
    return SimpleNamespace(request_context=SimpleNamespace(request=request))


SUGGEST = {
    "cheapest": {
        "total_price": "50.00",
        "products": [
            {"product_name": "עגבנייה", "unit": "ק״ג", "quantity": "10", "unit_price": "5", "subtotal": "50",
             "supplier_name": "ספק א"},
        ],
    },
    "minimum_issues": {"cheapest": []},
    "recommended": "cheapest",
    "unavailable_products": [],
}
PLACED = {"batch_id": 7, "total_price": "50.00", "merged": False, "not_added": [],
          "orders": [{"order_id": 1, "supplier_name": "ספק א", "status": "sent", "total_price": "50.00"}]}
ITEMS = [{"product_name": "עגבנייה", "quantity": 10}]


class ServerTests(unittest.TestCase):

    def setUp(self):
        drafts._drafts.clear()
        drafts._sent.clear()

    def _draft(self, token="so_alice"):
        with mock.patch.object(api, "call", return_value=SUGGEST):
            return server.draft_order(ITEMS, ctx(token))

    def test_draft_sends_nothing_and_groups_by_supplier(self):
        with mock.patch.object(api, "call", return_value=SUGGEST) as call:
            out = server.draft_order(ITEMS, ctx())
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.args[2], "/api/orders/suggest/")
        self.assertEqual(out["basket"]["suppliers"][0]["supplier"], "ספק א")
        self.assertIn("draft_id", out)

    def test_confirm_sends_once_then_draft_is_gone(self):
        draft_id = self._draft()["draft_id"]
        with mock.patch.object(api, "call", return_value=PLACED) as call:
            first = server.confirm_order(draft_id, ctx())
            second = server.confirm_order(draft_id, ctx())
        self.assertTrue(first["sent"])
        self.assertIn("error", second)
        self.assertEqual(call.call_count, 1)

    def test_another_users_draft_is_rejected_and_not_consumed(self):
        draft_id = self._draft("so_alice")["draft_id"]
        with mock.patch.object(api, "call", return_value=PLACED) as call:
            out = server.confirm_order(draft_id, ctx("so_bob"))
        self.assertIn("error", out)
        call.assert_not_called()
        with mock.patch.object(api, "call", return_value=PLACED):
            self.assertTrue(server.confirm_order(draft_id, ctx("so_alice"))["sent"])

    def test_expired_draft_rejected(self):
        draft_id = self._draft()["draft_id"]
        drafts._drafts[draft_id]["expires"] = 0
        with mock.patch.object(api, "call", return_value=PLACED) as call:
            self.assertIn("error", server.confirm_order(draft_id, ctx()))
        call.assert_not_called()

    def test_conflict_keeps_draft_usable(self):
        draft_id = self._draft()["draft_id"]
        with mock.patch.object(api, "call", side_effect=api.ApiError(409, {"detail": "יש הזמנה פתוחה"})):
            out = server.confirm_order(draft_id, ctx())
        self.assertTrue(out["draft_still_valid"])
        with mock.patch.object(api, "call", return_value=PLACED):
            self.assertTrue(server.confirm_order(draft_id, ctx())["sent"])

    def test_server_error_consumes_draft(self):
        draft_id = self._draft()["draft_id"]
        with mock.patch.object(api, "call", side_effect=api.ApiError(502, {"detail": "נכשל"})):
            out = server.confirm_order(draft_id, ctx())
        self.assertFalse(out["draft_still_valid"])
        with mock.patch.object(api, "call", return_value=PLACED) as call:
            self.assertIn("error", server.confirm_order(draft_id, ctx()))
        call.assert_not_called()

    def test_daily_quota(self):
        with mock.patch.object(server, "MAX_ORDERS_PER_DAY", 1):
            for expected in (True, False):
                draft_id = self._draft()["draft_id"]
                with mock.patch.object(api, "call", return_value=PLACED):
                    out = server.confirm_order(draft_id, ctx())
                self.assertEqual(out.get("sent", False), expected)

    def test_merge_uses_open_batch(self):
        draft_id = self._draft()["draft_id"]
        responses = [{"open_batch": {"batch_id": 3}}, PLACED]
        with mock.patch.object(api, "call", side_effect=responses) as call:
            server.confirm_order(draft_id, ctx(), add_to_open_order=True)
        self.assertEqual(call.call_args.kwargs["json"]["merge_into_batch"], 3)

    def test_bad_items_rejected_before_any_call(self):
        with mock.patch.object(api, "call") as call:
            self.assertIn("error", server.draft_order([{"product_name": "x", "quantity": -1}], ctx()))
            self.assertIn("error", server.draft_order([], ctx()))
        call.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class GateTests(unittest.TestCase):

    def setUp(self):
        import gate
        self.gate = gate
        gate._cache.clear()
        calls = []

        async def inner(scope, receive, send):
            calls.append(scope["path"])
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        self.calls = calls
        from starlette.testclient import TestClient
        self.client = TestClient(gate.AuthGate(inner))

    def test_no_token_gets_oauth_challenge(self):
        res = self.client.post("/mcp")
        self.assertEqual(res.status_code, 401)
        self.assertIn("resource_metadata=", res.headers["www-authenticate"])
        self.assertEqual(self.calls, [])

    def test_resource_metadata_is_public(self):
        res = self.client.get("/.well-known/oauth-protected-resource")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["resource"], f"{self.gate.PUBLIC_BASE}/mcp")
        self.assertEqual(self.client.get("/.well-known/oauth-protected-resource/mcp").status_code, 200)

    def test_bad_token_gets_invalid_token(self):
        async def nobody(token):
            return None
        with mock.patch.object(self.gate, "validate", nobody):
            res = self.client.post("/mcp", headers={"Authorization": "Bearer so_bad"})
        self.assertEqual(res.status_code, 401)
        self.assertIn('error="invalid_token"', res.headers["www-authenticate"])
        self.assertEqual(self.calls, [])

    def test_good_token_reaches_the_app(self):
        async def somebody(token):
            return "a@x.com"
        with mock.patch.object(self.gate, "validate", somebody):
            res = self.client.post("/mcp", headers={"Authorization": "Bearer so_ok"})
        self.assertEqual((res.status_code, self.calls), (200, ["/mcp"]))

    def test_owner_is_the_customer_not_the_token(self):
        for token in ("so_one", "so_two"):
            self.gate._cache[self.gate.token_key(token)] = ("a@x.com", 9e12)
        self.assertEqual(server._owner("so_one"), server._owner("so_two"))
