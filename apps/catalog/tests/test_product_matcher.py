from decimal import Decimal

from django.test import TestCase

from apps.catalog.product_matcher import match_order_items


class OrderSegmentUnitWordTests(TestCase):
    """
    _PRICE_SEGMENT_RE ("name number") only matched when the number was the
    very last thing in the segment — any unit word trailing it ("עגבניה 20
    יחידות", not just kilo-ish phrasing) fell through to the AI parser
    entirely instead of just being ignored like the leading-unit-word form
    ("20 יחידות עגבניה") already was.
    """

    KNOWN = ["עגבנייה", "פטרוזיליה", "חסה אייסברג", "מלפפון"]

    def test_trailing_unit_word_after_name_number(self):
        resolved, leftover = match_order_items("עגבנייה 20 יחידות", self.KNOWN)
        self.assertEqual(leftover, "")
        self.assertEqual(resolved, [{"product_name": "עגבנייה", "quantity": "20"}])

    def test_trailing_bundle_word(self):
        resolved, leftover = match_order_items("פטרוזיליה 5 אגודות", self.KNOWN)
        self.assertEqual(leftover, "")
        self.assertEqual(resolved, [{"product_name": "פטרוזיליה", "quantity": "5"}])

    def test_trailing_singular_unit_word(self):
        resolved, leftover = match_order_items("חסה אייסברג 3 יחידה", self.KNOWN)
        self.assertEqual(leftover, "")
        self.assertEqual(resolved, [{"product_name": "חסה אייסברג", "quantity": "3"}])

    def test_leading_unit_word_still_works(self):
        """Regression guard: the already-working "number unit name" form."""
        resolved, leftover = match_order_items("20 יחידות עגבנייה", self.KNOWN)
        self.assertEqual(leftover, "")
        self.assertEqual(resolved, [{"product_name": "עגבנייה", "quantity": "20"}])

    def test_no_unit_word_still_works(self):
        """Regression guard: bare 'name number' with no unit at all."""
        resolved, leftover = match_order_items("מלפפון 20", self.KNOWN)
        self.assertEqual(leftover, "")
        self.assertEqual(resolved, [{"product_name": "מלפפון", "quantity": "20"}])

    def test_mixed_message_multiple_segments(self):
        resolved, leftover = match_order_items(
            "עגבנייה 20 יחידות, מלפפון 10, 5 אגודות פטרוזיליה", self.KNOWN
        )
        self.assertEqual(leftover, "")
        self.assertEqual(len(resolved), 3)
        by_name = {r["product_name"]: r["quantity"] for r in resolved}
        self.assertEqual(by_name["עגבנייה"], "20")
        self.assertEqual(by_name["מלפפון"], "10")
        self.assertEqual(by_name["פטרוזיליה"], "5")
