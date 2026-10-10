import json
import logging
import os
from decimal import Decimal, InvalidOperation

from openai import OpenAI

from apps.catalog.product_matcher import list_ambiguous_families, match_order_items

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


class AmbiguousProductError(Exception):
    """
    Raised instead of guessing (or asking the AI to guess) when a segment
    names a product family rather than a specific catalog item — "תפוח אדמה"
    when only "תפוח אדמה אדום"/"תפוח אדمה לבן" exist. Carries both what
    couldn't be resolved and what already was, so the caller can ask about
    just the ambiguous part without losing the rest of the order.
    """
    def __init__(self, ambiguous: list, resolved: list):
        self.ambiguous = ambiguous
        self.resolved = resolved
        super().__init__(f"{len(ambiguous)} ambiguous product(s)")


def _to_decimal_items(raw_items: list) -> list:
    """{'product_name', 'quantity': str} -> same, quantity parsed to Decimal. Drops unparsable/non-positive entries."""
    result = []
    for entry in raw_items:
        name = str(entry.get("product_name", "")).strip()
        qty_raw = str(entry.get("quantity", "")).strip()
        if not name or not qty_raw:
            continue
        try:
            qty = Decimal(qty_raw)
            if qty <= 0:
                continue
        except InvalidOperation:
            continue
        result.append({"product_name": name, "quantity": qty})
    return result


def parse_modification_intent(message: str, product_names: list) -> dict:
    """
    Detects if message is a modification to an existing order.
    Returns {"intent": "add"|"update"|"none", "items": [{"product_name": str, "quantity": Decimal}]}.
    """
    known = ", ".join(product_names) if product_names else "—"

    # "פלפל" isn't itself a catalog product — only "פלפל אדום"/"פלפל ירוק"/...
    # are (same family-root ambiguity parse_customer_order asks about for a
    # fresh order, via find_ambiguous_group). Told only to match against known
    # names, the model used to just pick a variant on its own — spell the
    # families out and require it to hand back the bare root instead, so the
    # caller's own find_ambiguous_group check (which only fires when the
    # returned name ISN'T an existing product) actually gets a chance to ask.
    families = list_ambiguous_families(product_names)
    families_note = ""
    if families:
        family_lines = "\n".join(
            f"  - \"{root}\" could mean: {', '.join(variants)}" for root, variants in families.items()
        )
        families_note = (
            "\nSome names above are ambiguous family roots covering several specific variants:\n"
            f"{family_lines}\n"
            "If the customer used one of these root terms WITHOUT naming a specific variant, "
            "return the root text itself as product_name — do NOT guess or default to one of "
            "the variants.\n"
        )

    prompt = (
        "You are an order-modification parser for a Hebrew vegetable ordering system.\n"
        "The customer already has an open order. For EACH product they mention, decide the action "
        "and what the quantity means:\n"
        "1. 'add' — MORE of it on top of what they already have. quantity = the amount to ADD. "
        "Cues: תוסיף, הוסף, עוד, גם, תביא עוד, תגדיל ב-X. A product with a quantity and NO other cue "
        "(for example 'עגבניות 20' or '50 עגבניות') is ALWAYS 'add', whatever the number: when unsure, "
        "choose 'add', which never removes anything the customer already has.\n"
        "2. 'update' — change the TOTAL. quantity = the NEW TOTAL. "
        "Cues: תעדכן ל-X, שנה ל-X, תהפוך ל-X, תקטין ל-X, במקום 30 תביא 50 (total 50).\n"
        "3. 'reduce' — LESS of it. quantity = the amount to REMOVE. "
        "Cues: תוריד X, תפחית X, תקטין ב-X, X פחות.\n"
        "4. 'none' (top-level only) — the message is not a request to change the order "
        "(a question, thanks, a greeting).\n"
        "Examples (product known as עגבניה):\n"
        '  "תוסיף 20 קילו עגבניות" -> add 20\n'
        '  "גם 20 עגבניות" -> add 20\n'
        '  "עגבניות 20" -> add 20\n'
        '  "50 עגבניות" -> add 50\n'
        '  "תגדיל את העגבניות ב-10" -> add 10\n'
        '  "תעדכן עגבניות ל-50" -> update 50\n'
        '  "במקום 30 עגבניות תביא 50" -> update 50\n'
        '  "תקטין את העגבניות ל-20" -> update 20\n'
        '  "תוריד 10 קילו עגבניות" -> reduce 10\n'
        '  "תוסיף 20 עגבניות ותעדכן מלפפונים ל-15" -> add עגבניה 20 AND update מלפפון 15\n'
        "Match product names to known products using fuzzy Hebrew matching, but always return "
        "the exact known product name from the list above, never a paraphrase or synonym — "
        "the caller looks it up by exact string match against the known list.\n"
        f"{families_note}"
        "Return ONLY JSON: "
        '{"intent": "add"|"update"|"reduce"|"none", "items": '
        '[{"product_name": "...", "quantity": "5.0", "action": "add"|"update"|"reduce"}]}\n'
        f"Message: {message}"
    )
    try:
        response = _get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        data = json.loads(response.choices[0].message.content)
    except Exception as exc:
        logger.error("OpenAI modification parsing failed: %s", exc)
        return {"intent": "none", "items": []}

    actions = ("add", "update", "reduce")
    intent = data.get("intent", "none")
    raw_items = data.get("items", [])
    items = []
    for entry in raw_items:
        name = entry.get("product_name", "").strip()
        qty_raw = str(entry.get("quantity", "")).strip()
        if not name or not qty_raw:
            continue
        try:
            qty = Decimal(qty_raw)
        except InvalidOperation:
            continue
        if qty > 0:
            # Each product carries its own action (a message can mix "add X" and
            # "update Y to N"); fall back to the message-level intent, then "add".
            action = entry.get("action") if entry.get("action") in actions else (intent if intent in actions else "add")
            items.append({"product_name": name, "quantity": qty, "intent": action})

    if intent not in actions:
        intent = "none"
    elif items:
        intent = items[0]["intent"]
    return {"intent": intent, "items": items}


def parse_customer_order(message: str, product_names: list) -> list:
    """
    Extracts products and quantities from a free-text customer order message.
    Returns [{"product_name": str, "quantity": Decimal}].
    Raises ValueError("no_items") if nothing extracted.
    Raises ValueError("AI parsing failed: ...") on OpenAI error.
    Raises AmbiguousProductError if a segment names a product family rather
    than a specific catalog item ("תפוח אדמה" when only the אדום/לבן variants
    exist) — asking which one is cheap and instant; guessing isn't.

    Segments that resolve via the exact/alias dictionary (data/product_aliases.json)
    skip the AI entirely — only what's left over goes to OpenAI. If everything
    resolves via the dictionary, no AI call is made at all.
    """
    dict_resolved, ambiguous, remaining_message = match_order_items(message, product_names)

    if ambiguous:
        # Don't also hand remaining_message to the AI here: this message needs
        # a clarifying answer before it means anything, and mixing an AI guess
        # for an unrelated leftover segment into that reply would be more
        # confusing than just asking about the ambiguous part first.
        raise AmbiguousProductError(
            ambiguous=[
                {**item, "quantity": Decimal(item["quantity"])} for item in ambiguous
            ],
            resolved=_to_decimal_items(dict_resolved),
        )

    items = list(dict_resolved)
    if remaining_message:
        known = ", ".join(product_names) if product_names else "—"
        prompt = (
            "You are an order parser for a vegetable/fruit ordering system in Israel.\n"
            "Extract ALL products and quantities from the customer's order message.\n"
            f"Known products in the system: {known}\n"
            "Rules:\n"
            "1. Match product names to known products using fuzzy/phonetic Hebrew matching. "
            "Use the exact known name when there is a match.\n"
            "2. Default quantity is 1 if not specified.\n"
            "Return ONLY a JSON object with key 'items'. "
            'Each element: {"product_name": "...", "quantity": "5.0"}\n'
            f"Message: {remaining_message}"
        )

        try:
            response = _get_client().chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0,
            )
            raw = response.choices[0].message.content
            data = json.loads(raw)
            if isinstance(data, dict):
                data = data.get("items", next(iter(data.values()), []))
            items += data if isinstance(data, list) else []
        except Exception as exc:
            logger.error("OpenAI order parsing failed: %s", exc)
            raise ValueError(f"AI parsing failed: {exc}")

    result = _to_decimal_items(items)

    if not result:
        raise ValueError("no_items")

    return result
