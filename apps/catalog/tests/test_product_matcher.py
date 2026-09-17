from decimal import Decimal

from django.test import TestCase

from apps.catalog.models import Product, ProductAlias, Unit
from apps.catalog.product_matcher import (
    find_ambiguous_group,
    list_ambiguous_families,
    match_order_items,
    resolve_alias,
    resolve_clarification,
)


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

    def test_resolve_clarification_positional_pairing_avoids_cross_match(self):
        """
        Regression, found live: "ירוק" is a valid variant for BOTH onion and
        pepper. Answering two questions in one reply ("ירוק, כתום" - green
        onion, orange pepper) used to search the WHOLE reply for each item's
        candidates independently, so the pepper's own search also found
        "ירוק" (meant for the onion) and matched "פלפל ירוק" instead of the
        intended "פלפל כתום". Positional pairing scopes each answer to its
        own question.
        """
        ambiguous = [
            {
                "query": "בצל", "quantity": "30",
                "candidates": ["בצל סגול קלוף", "בצל שאלוט", "בצל סגול", "בצל לבן קלוף", "בצל ירוק", "בצל לבן"],
            },
            {
                "query": "פלפל", "quantity": "20",
                "candidates": ["פלפל צ'ילי", "פלפל חריף", "פלפל צהוב", "פלפל ירוק", "פלפל אדום", "פלפל כתום"],
            },
        ]
        resolved, still_ambiguous = resolve_clarification("ירוק, כתום", ambiguous)
        self.assertEqual(still_ambiguous, [])
        self.assertEqual(
            {r["product_name"] for r in resolved}, {"בצל ירוק", "פלפל כתום"}
        )

    def test_resolve_clarification_positional_pairing_three_items_in_order(self):
        ambiguous = [
            {"query": "פלפל", "quantity": "20", "candidates": ["פלפל אדום", "פלפל כתום"]},
            {"query": "בצל", "quantity": "30", "candidates": ["בצל לבן", "בצל ירוק"]},
            {"query": "חסה", "quantity": "40", "candidates": ["חסה קיסר", "חסה רומית"]},
        ]
        resolved, still_ambiguous = resolve_clarification("כתום, לבן, קיסר", ambiguous)
        self.assertEqual(still_ambiguous, [])
        names_by_quantity = {r["quantity"]: r["product_name"] for r in resolved}
        self.assertEqual(names_by_quantity["20"], "פלפל כתום")
        self.assertEqual(names_by_quantity["30"], "בצל לבן")
        self.assertEqual(names_by_quantity["40"], "חסה קיסר")

    def test_resolve_clarification_falls_back_to_whole_reply_when_part_count_differs(self):
        """A reply that doesn't segment into exactly one part per question still resolves via the old best-effort search."""
        ambiguous = [
            {"query": "בצל", "quantity": "30", "candidates": ["בצל לבן", "בצל ירוק"]},
            {"query": "פלפל", "quantity": "20", "candidates": ["פלפל אדום", "פלפל כתום"]},
        ]
        # One combined phrase, no comma/newline separator -> single part, count mismatch.
        resolved, still_ambiguous = resolve_clarification("לבן ואדום", ambiguous)
        self.assertEqual(still_ambiguous, [])
        self.assertEqual(
            {r["product_name"] for r in resolved}, {"בצל לבן", "פלפל אדום"}
        )

    def test_list_ambiguous_families_groups_by_shared_root(self):
        families = list_ambiguous_families(self.KNOWN)
        self.assertEqual(
            set(families["תפוח אדמה"]), {"תפוח אדמה אדום", "תפוח אדמה לבן"}
        )
        self.assertEqual(
            set(families["תפוח עץ"]), {"תפוח עץ אדום", "תפוח עץ ירוק"}
        )
        self.assertNotIn("עגבנייה", families)

    def test_list_ambiguous_families_empty_when_no_shared_roots(self):
        self.assertEqual(list_ambiguous_families(["עגבנייה", "מלפפון"]), {})


class DbAliasResolutionTests(TestCase):
    """
    resolve_alias() must also check the admin-managed ProductAlias table
    (not just the static data/product_aliases.json file), and let a DB
    alias take precedence when the same text is defined in both places.
    """

    def test_resolves_alias_from_db(self):
        onion = Product.objects.create(name="בצל יבש", unit=Unit.KG)
        ProductAlias.objects.create(product=onion, alias="בצל לבן")

        self.assertEqual(resolve_alias("בצל לבן", ["בצל יבש"]), "בצל יבש")

    def test_db_alias_for_unknown_product_is_ignored(self):
        """A DB alias pointing at a product not in known_product_names must not resurrect it."""
        onion = Product.objects.create(name="בצל יבש", unit=Unit.KG)
        ProductAlias.objects.create(product=onion, alias="בצל לבן")

        self.assertIsNone(resolve_alias("בצל לבן", ["מלפפון"]))

    def test_db_alias_overrides_json_file_for_same_text(self):
        """"בצל לבן" is already a JSON alias for "בצל יבש" - a DB alias should be able to redirect it."""
        red_onion = Product.objects.create(name="בצל סגול", unit=Unit.KG)
        ProductAlias.objects.create(product=red_onion, alias="בצל לבן")

        self.assertEqual(resolve_alias("בצל לבן", ["בצל יבש", "בצל סגול"]), "בצל סגול")

    def test_falls_back_to_json_file_when_no_db_alias(self):
        """No DB row for this text at all - the static file still resolves it as before."""
        self.assertEqual(resolve_alias("בצל לבן", ["בצל יבש"]), "בצל יבש")
