from decimal import Decimal

from django.test import TestCase

from apps.catalog.product_matcher import find_ambiguous_group, match_order_items, resolve_clarification


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
        resolved, ambiguous, leftover = match_order_items("עגבנייה 20 יחידות", self.KNOWN)
        self.assertEqual(leftover, "")
        self.assertEqual(ambiguous, [])
        self.assertEqual(resolved, [{"product_name": "עגבנייה", "quantity": "20"}])

    def test_trailing_bundle_word(self):
        resolved, ambiguous, leftover = match_order_items("פטרוזיליה 5 אגודות", self.KNOWN)
        self.assertEqual(leftover, "")
        self.assertEqual(resolved, [{"product_name": "פטרוזיליה", "quantity": "5"}])

    def test_trailing_singular_unit_word(self):
        resolved, ambiguous, leftover = match_order_items("חסה אייסברג 3 יחידה", self.KNOWN)
        self.assertEqual(leftover, "")
        self.assertEqual(resolved, [{"product_name": "חסה אייסברג", "quantity": "3"}])

    def test_leading_unit_word_still_works(self):
        """Regression guard: the already-working "number unit name" form."""
        resolved, ambiguous, leftover = match_order_items("20 יחידות עגבנייה", self.KNOWN)
        self.assertEqual(leftover, "")
        self.assertEqual(resolved, [{"product_name": "עגבנייה", "quantity": "20"}])

    def test_no_unit_word_still_works(self):
        """Regression guard: bare 'name number' with no unit at all."""
        resolved, ambiguous, leftover = match_order_items("מלפפון 20", self.KNOWN)
        self.assertEqual(leftover, "")
        self.assertEqual(resolved, [{"product_name": "מלפפון", "quantity": "20"}])

    def test_mixed_message_multiple_segments(self):
        resolved, ambiguous, leftover = match_order_items(
            "עגבנייה 20 יחידות, מלפפון 10, 5 אגודות פטרוזיליה", self.KNOWN
        )
        self.assertEqual(leftover, "")
        self.assertEqual(len(resolved), 3)
        by_name = {r["product_name"]: r["quantity"] for r in resolved}
        self.assertEqual(by_name["עגבנייה"], "20")
        self.assertEqual(by_name["מלפפון"], "10")
        self.assertEqual(by_name["פטרוזיליה"], "5")


class AmbiguousProductTests(TestCase):
    """
    "תפוח אדמה" alone isn't a catalog product — only "תפוח אדמה אדום" and
    "תפוח אדמה לבן" are. That should be surfaced as a clarification question,
    not silently guessed at or shipped off to the AI to guess for us.
    """

    KNOWN = ["תפוח אדמה אדום", "תפוח אדמה לבן", "עגבנייה", "תפוח עץ אדום", "תפוח עץ ירוק"]

    def test_find_ambiguous_group_matches_shared_prefix(self):
        group = find_ambiguous_group("תפוח אדמה", self.KNOWN)
        self.assertEqual(set(group), {"תפוח אדמה אדום", "תפוח אדמה לבן"})

    def test_find_ambiguous_group_requires_word_boundary(self):
        """"עגבני" must not match "עגבנייה" as a false "ambiguous" prefix hit."""
        self.assertIsNone(find_ambiguous_group("עגבני", ["עגבנייה"]))

    def test_find_ambiguous_group_none_for_unique_name(self):
        self.assertIsNone(find_ambiguous_group("עגבנייה", self.KNOWN))

    def test_match_order_items_surfaces_ambiguous_instead_of_leftover(self):
        resolved, ambiguous, leftover = match_order_items("5 קילו תפוח אדמה", self.KNOWN)
        self.assertEqual(resolved, [])
        self.assertEqual(leftover, "")
        self.assertEqual(len(ambiguous), 1)
        self.assertEqual(ambiguous[0]["query"], "תפוח אדמה")
        self.assertEqual(ambiguous[0]["quantity"], "5")
        self.assertEqual(set(ambiguous[0]["candidates"]), {"תפוח אדמה אדום", "תפוח אדמה לבן"})

    def test_resolve_clarification_picks_the_named_variant(self):
        ambiguous = [{
            "query": "תפוח אדמה", "quantity": "5",
            "candidates": ["תפוח אדמה אדום", "תפוח אדמה לבן"],
        }]
        resolved, still_ambiguous = resolve_clarification("אדום", ambiguous)
        self.assertEqual(still_ambiguous, [])
        self.assertEqual(resolved, [{"product_name": "תפוח אדמה אדום", "quantity": "5"}])

    def test_resolve_clarification_leaves_unmatched_still_ambiguous(self):
        ambiguous = [{
            "query": "תפוח אדמה", "quantity": "5",
            "candidates": ["תפוח אדמה אדום", "תפוח אדמה לבן"],
        }]
        resolved, still_ambiguous = resolve_clarification("לא יודע", ambiguous)
        self.assertEqual(resolved, [])
        self.assertEqual(still_ambiguous, ambiguous)
