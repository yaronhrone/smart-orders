import copy
import datetime as dt
import difflib
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Max, Min, Q, Sum
from django.db.models.functions import TruncDay, TruncMonth, TruncWeek
from django.utils import timezone

from apps.catalog.models import Product, ProductAlias, Region, Supplier, SupplierProduct, Unit
from apps.catalog.product_matcher import find_ambiguous_group, resolve_alias
from apps.orders.models import OrderRequest
from apps.orders.spend import LINE_TOTAL, spend_lines

logger = logging.getLogger(__name__)

IL_TZ = ZoneInfo("Asia/Jerusalem")
MAX_ROWS = 50
MAX_RANGE_DAYS = 3 * 366
UNIT_LABELS = dict(Unit.choices)
REGION_LABELS = dict(Region.choices)
TRUNCATORS = {"day": TruncDay, "week": TruncWeek, "month": TruncMonth}


class ToolError(Exception):
    """A problem the model can fix by calling again with different arguments."""


@dataclass
class ToolContext:
    user: object
    is_admin: bool
    region: str | None
    today: dt.date


def build_context(user, now=None) -> ToolContext:
    profile = getattr(user, "profile", None)
    now = now or timezone.now()
    return ToolContext(
        user=user,
        is_admin=bool(user.is_staff),
        region=profile.region if profile else None,
        today=now.astimezone(IL_TZ).date(),
    )


def _money(value) -> str:
    return f"{(value or Decimal('0')).quantize(Decimal('0.01')):.2f}"


def _local(value) -> str:
    return timezone.localtime(value, IL_TZ).strftime("%Y-%m-%d %H:%M")


def _parse_date(value, field):
    if value in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ToolError(f"{field} must be a date in YYYY-MM-DD format")


def _parse_range(date_from, date_to):
    """Inclusive date range in Israel time -> (start, end_exclusive) aware datetimes."""
    d_from = _parse_date(date_from, "date_from")
    d_to = _parse_date(date_to, "date_to")
    if d_from and d_to:
        if d_from > d_to:
            raise ToolError("date_from is after date_to")
        if (d_to - d_from).days > MAX_RANGE_DAYS:
            raise ToolError("date range is too long (maximum 3 years)")
    start = dt.datetime.combine(d_from, dt.time.min, tzinfo=IL_TZ) if d_from else None
    end = dt.datetime.combine(d_to + dt.timedelta(days=1), dt.time.min, tzinfo=IL_TZ) if d_to else None
    return start, end, d_from, d_to


def resolve_products(names):
    """
    Map free-text product names to catalog products.
    Returns (products, ambiguous, not_found): ambiguous is {name: [variants]},
    not_found is {name: [close catalog names]}.
    """
    known = list(Product.objects.values_list("name", flat=True))
    by_name = {p.name: p for p in Product.objects.all()}
    folded = {name.casefold(): name for name in known}
    products, ambiguous, not_found = [], {}, {}

    for raw in names:
        name = (raw or "").strip()
        if not name:
            continue
        canonical = resolve_alias(name, known) or folded.get(name.casefold())
        if canonical:
            products.append(by_name[canonical])
            continue
        family = find_ambiguous_group(name, known)
        if family:
            ambiguous[name] = family
            continue
        contains = [n for n in known if name.casefold() in n.casefold()]
        if len(contains) == 1:
            products.append(by_name[contains[0]])
        elif contains:
            ambiguous[name] = contains
        else:
            not_found[name] = difflib.get_close_matches(name, known, n=3, cutoff=0.5)

    unique = list({p.id: p for p in products}.values())
    return unique, ambiguous, not_found


def _resolution_problem(ambiguous, not_found):
    if ambiguous:
        return {
            "ambiguous_products": ambiguous,
            "note": "Ask the user which one they mean, or call again with all the variants.",
        }
    if not_found:
        return {
            "products_not_found": not_found,
            "note": "No such product in the catalog. Use list_products to search, or ask the user.",
        }
    return None


def _order_span(qs, field):
    """When the account's first and last order happened, so an empty answer can tell a wrong period from no data."""
    bounds = qs.aggregate(first=Min(field), last=Max(field))
    if not bounds["first"]:
        return None
    return {
        "first_order": bounds["first"].astimezone(IL_TZ).date().isoformat(),
        "last_order": bounds["last"].astimezone(IL_TZ).date().isoformat(),
    }


_NO_MATCH_NOTE = (
    "Nothing matched. If the dates may be wrong (for example the wrong year), retry; "
    "data_range shows when this account has orders."
)


def _num(value) -> str:
    return str((value or Decimal("0")).quantize(Decimal("0.01")))


def _period_label(group_by, value):
    local = value.astimezone(IL_TZ) if timezone.is_aware(value) else value
    if group_by == "month":
        return local.strftime("%Y-%m")
    return local.date().isoformat()


def query_spending(
    ctx, date_from=None, date_to=None, products=None, suppliers=None, group_by="none", customer=None,
):
    """Spend (sent/approved/shipped/delivered orders only), optionally filtered and grouped."""
    if group_by == "customer" and not ctx.is_admin:
        raise ToolError("group_by=customer is not available")
    if group_by not in ("none", "product", "supplier", "order", "customer", *TRUNCATORS):
        raise ToolError("unknown group_by")

    start, end, d_from, d_to = _parse_range(date_from, date_to)
    lines = spend_lines(None if ctx.is_admin else ctx.user)
    if start:
        lines = lines.filter(order_request__created_at__gte=start)
    if end:
        lines = lines.filter(order_request__created_at__lt=end)

    filters = {"date_from": d_from.isoformat() if d_from else None, "date_to": d_to.isoformat() if d_to else None}
    single_product = None
    if products:
        found, ambiguous, not_found = resolve_products(products)
        problem = _resolution_problem(ambiguous, not_found)
        if problem:
            return problem
        lines = lines.filter(product_id__in=[p.id for p in found])
        filters["products"] = [p.name for p in found]
        if len(found) == 1:
            single_product = found[0]

    if suppliers:
        match = Q()
        for name in suppliers:
            match |= Q(supplier__name__icontains=name.strip())
        lines = lines.filter(match)
        matched = sorted(set(lines.values_list("supplier__name", flat=True)))
        if not matched:
            return {"total_spend": "0.00", "order_count": 0, "filters": filters,
                    "note": "No spend found for these suppliers in this period."}
        filters["suppliers"] = matched

    if customer and ctx.is_admin:
        lines = lines.filter(
            Q(order_request__user__email__icontains=customer)
            | Q(order_request__user__profile__company_name__icontains=customer)
        )
        filters["customer"] = customer

    overall = lines.aggregate(
        total=Sum(LINE_TOTAL), orders=Count("order_request_id", distinct=True), qty=Sum("quantity"),
    )
    result = {
        "total_spend": _money(overall["total"]),
        "order_count": overall["orders"],
        "filters": filters,
    }
    if not overall["orders"]:
        span = _order_span(spend_lines(None if ctx.is_admin else ctx.user), "order_request__created_at")
        if span:
            result["data_range"] = span
            result["note"] = _NO_MATCH_NOTE
    if single_product:
        result["unit"] = UNIT_LABELS.get(single_product.unit, single_product.unit)
        result["total_quantity"] = _num(overall["qty"])
    if group_by == "none":
        return result

    if group_by in TRUNCATORS:
        lines = lines.annotate(period=TRUNCATORS[group_by]("order_request__created_at", tzinfo=IL_TZ))
        keys, order = ["period"], ["period"]
    elif group_by == "product":
        keys, order = ["product__name", "product__unit"], ["-total"]
    elif group_by == "supplier":
        keys, order = ["supplier__name"], ["-total"]
    elif group_by == "order":
        keys, order = ["order_request_id"], ["-total"]
    else:
        keys, order = ["order_request__user__email", "order_request__user__profile__company_name"], ["-total"]

    rows = (
        lines.order_by().values(*keys)
        .annotate(
            total=Sum(LINE_TOTAL), orders=Count("order_request_id", distinct=True),
            line_count=Count("id"), qty=Sum("quantity"), min_price=Min("unit_price"), max_price=Max("unit_price"),
        )
        .order_by(*order)
    )
    rows = list(rows[: MAX_ROWS + 1])
    result["truncated"] = len(rows) > MAX_ROWS
    groups = []
    for row in rows[:MAX_ROWS]:
        entry = {"total_spend": _money(row["total"]), "order_count": row["orders"]}
        if group_by in TRUNCATORS:
            entry["period"] = _period_label(group_by, row["period"])
        elif group_by == "product":
            entry["product"] = row["product__name"]
            entry["unit"] = UNIT_LABELS.get(row["product__unit"], row["product__unit"])
        elif group_by == "supplier":
            entry["supplier"] = row["supplier__name"]
        elif group_by == "order":
            entry["order_id"] = row["order_request_id"]
        else:
            entry["customer"] = row["order_request__user__profile__company_name"] or row["order_request__user__email"]
        if group_by == "product" or single_product:
            entry["total_quantity"] = _num(row["qty"])
            entry["min_unit_price"] = _money(row["min_price"])
            entry["max_unit_price"] = _money(row["max_price"])
            if row["qty"]:
                entry["avg_unit_price"] = _money(row["total"] / row["qty"])
        groups.append(entry)
    result["groups"] = groups
    return result


def _orders_for(ctx):
    orders = OrderRequest.objects.select_related("supplier", "user__profile")
    return orders if ctx.is_admin else orders.filter(user=ctx.user)




ORDER_LINES_TOTAL = ExpressionWrapper(
    F("products__quantity") * F("products__unit_price"),
    output_field=DecimalField(max_digits=14, decimal_places=2),
)


def list_orders(ctx, date_from=None, date_to=None, status=None, supplier=None, limit=10):
    """Orders (any status), newest first. Totals are summed from the order lines."""
    start, end, _, _ = _parse_range(date_from, date_to)
    orders = _orders_for(ctx)
    if start:
        orders = orders.filter(created_at__gte=start)
    if end:
        orders = orders.filter(created_at__lt=end)
    if status:
        if status not in OrderRequest.Status.values:
            raise ToolError(f"status must be one of {list(OrderRequest.Status.values)}")
        orders = orders.filter(status=status)
    if supplier:
        orders = orders.filter(supplier__name__icontains=supplier.strip())
    try:
        limit = max(1, min(int(limit or 10), MAX_ROWS))
    except (TypeError, ValueError):
        raise ToolError("limit must be a number")

    rows = (
        orders.annotate(order_total=Sum(ORDER_LINES_TOTAL), line_count=Count("products"))
        .order_by("-created_at")[:limit]
    )
    result = []
    for order in rows:
        entry = {
            "order_id": order.id,
            "created_at": _local(order.created_at),
            "supplier": order.supplier.name,
            "status": order.status,
            "status_label": order.get_status_display(),
            "total": _money(order.order_total),
            "line_count": order.line_count,
        }
        if ctx.is_admin:
            profile = getattr(order.user, "profile", None)
            entry["customer"] = (profile.company_name if profile else None) or order.user.email
        result.append(entry)
    response = {"orders": result, "returned": len(result)}
    if not result:
        span = _order_span(_orders_for(ctx), "created_at")
        if span:
            response["data_range"] = span
            response["note"] = _NO_MATCH_NOTE
    return response


def get_order(ctx, order_id):
    """One order with its lines. A customer can only open their own."""
    try:
        order_id = int(order_id)
    except (TypeError, ValueError):
        raise ToolError("order_id must be a number")
    order = _orders_for(ctx).filter(id=order_id).first()
    if order is None:
        return {"error": "order not found"}

    lines, total = [], Decimal("0")
    for item in order.products.select_related("product", "supplier").prefetch_related("confirmations"):
        subtotal = item.quantity * item.unit_price
        total += subtotal
        confirmations = list(item.confirmations.all())
        lines.append({
            "product": item.product.name,
            "quantity": _num(item.quantity),
            "unit": UNIT_LABELS.get(item.product.unit, item.product.unit),
            "unit_price": _money(item.unit_price),
            "subtotal": _money(subtotal),
            "supplier": item.supplier.name,
            "confirmed_quantity": _num(sum((c.confirmed_quantity for c in confirmations), Decimal("0")))
            if confirmations else None,
        })
    result = {
        "order_id": order.id,
        "created_at": _local(order.created_at),
        "supplier": order.supplier.name,
        "status": order.status,
        "status_label": order.get_status_display(),
        "total": _money(total),
        "lines": lines,
    }
    if ctx.is_admin:
        profile = getattr(order.user, "profile", None)
        result["customer"] = (profile.company_name if profile else None) or order.user.email
    return result


def get_current_prices(ctx, products=None, region=None):
    """Current supplier prices per product, cheapest first. Customers see only their region."""
    if not products:
        raise ToolError("products is required")
    found, ambiguous, not_found = resolve_products(products)
    problem = _resolution_problem(ambiguous, not_found)
    if problem:
        return problem
    if not found:
        raise ToolError("products is required")

    prices = SupplierProduct.objects.filter(product__in=found).select_related("supplier", "product")
    now = timezone.now()
    if ctx.is_admin:
        if region:
            prices = prices.filter(supplier__region=region)
    else:
        if not ctx.region:
            raise ToolError("no region is set for this account")
        prices = prices.filter(supplier__region=ctx.region).filter(
            Q(supplier__blocked_until__isnull=True) | Q(supplier__blocked_until__lt=now)
        )

    result = {}
    for sp in prices.order_by("product__name", "price_per_unit"):
        group = result.setdefault(sp.product.name, {
            "product": sp.product.name,
            "unit": UNIT_LABELS.get(sp.product.unit, sp.product.unit),
            "offers": [],
        })
        if len(group["offers"]) >= 10:
            continue
        offer = {
            "supplier": sp.supplier.name,
            "price_per_unit": _money(sp.price_per_unit),
            "minimum_order": _money(sp.supplier.minimum_order),
            "price_updated": _local(sp.updated_at)[:10],
        }
        if ctx.is_admin:
            offer["region"] = REGION_LABELS.get(sp.supplier.region, sp.supplier.region)
            offer["blocked"] = bool(sp.supplier.blocked_until and sp.supplier.blocked_until > now)
        group["offers"].append(offer)
    return {
        "prices": list(result.values()),
        "products_without_offers": [p.name for p in found if p.name not in result],
    }


def get_suppliers(ctx, search=None, region=None):
    """Suppliers with region and minimum order. Customers see only available suppliers in their region."""
    suppliers = Supplier.objects.all()
    now = timezone.now()
    if ctx.is_admin:
        if region:
            suppliers = suppliers.filter(region=region)
    else:
        if not ctx.region:
            raise ToolError("no region is set for this account")
        suppliers = suppliers.filter(region=ctx.region).filter(
            Q(blocked_until__isnull=True) | Q(blocked_until__lt=now)
        )
    if search:
        suppliers = suppliers.filter(name__icontains=search.strip())

    result = []
    for s in suppliers.order_by("name")[:MAX_ROWS]:
        entry = {
            "name": s.name,
            "region": REGION_LABELS.get(s.region, s.region),
            "minimum_order": _money(s.minimum_order),
        }
        if ctx.is_admin:
            entry["phone"] = s.phone
            entry["blocked_until"] = _local(s.blocked_until) if s.blocked_until and s.blocked_until > now else None
        result.append(entry)
    return {"suppliers": result}


def list_products(ctx, search=None, limit=30):
    """Catalog search by name or alias."""
    products = Product.objects.all()
    if search:
        alias_ids = ProductAlias.objects.filter(alias__icontains=search.strip()).values("product_id")
        products = products.filter(Q(name__icontains=search.strip()) | Q(id__in=alias_ids))
    try:
        limit = max(1, min(int(limit or 30), MAX_ROWS))
    except (TypeError, ValueError):
        raise ToolError("limit must be a number")
    return {"products": [
        {"name": p.name, "unit": UNIT_LABELS.get(p.unit, p.unit)} for p in products.order_by("name")[:limit]
    ]}


def list_customers(ctx, search=None):
    """Admin only: customers that have a company profile."""
    User = get_user_model()
    users = User.objects.filter(profile__isnull=False).select_related("profile")
    if search:
        users = users.filter(
            Q(email__icontains=search.strip()) | Q(profile__company_name__icontains=search.strip())
        )
    return {"customers": [
        {
            "company": u.profile.company_name,
            "email": u.email,
            "region": REGION_LABELS.get(u.profile.region, u.profile.region),
        }
        for u in users.order_by("profile__company_name")[:MAX_ROWS]
    ]}


_DATE_FROM = {"type": "string", "description": "Start date, inclusive, YYYY-MM-DD (Israel time)."}
_DATE_TO = {"type": "string", "description": "End date, inclusive, YYYY-MM-DD (Israel time)."}
_NAMES = {"type": "array", "items": {"type": "string"}}


@dataclass
class Tool:
    name: str
    fn: object
    description: str
    parameters: dict
    admin_only: bool = False


TOOLS = [
    Tool(
        "query_spending", query_spending,
        "Total spend and counts over a date range, optionally filtered by products or suppliers and "
        "grouped. Only orders that were sent to suppliers count (cancelled and pending are excluded). "
        "Use this for any 'how much did we spend' question. Never add up or estimate numbers yourself.",
        {"type": "object", "properties": {
            "date_from": _DATE_FROM, "date_to": _DATE_TO,
            "products": {**_NAMES, "description": "Product names as the user said them."},
            "suppliers": {**_NAMES, "description": "Supplier names (partial match)."},
            "group_by": {
                "type": "string",
                "enum": ["none", "product", "supplier", "day", "week", "month", "order", "customer"],
                "description": "Break the total down by this dimension. 'customer' is admin only.",
            },
            "customer": {"type": "string", "description": "Admin only: customer email or company name."},
        }},
    ),
    Tool(
        "list_orders", list_orders,
        "List orders of any status, newest first. Use for 'what was my last order', status questions, "
        "or listing cancelled orders.",
        {"type": "object", "properties": {
            "date_from": _DATE_FROM, "date_to": _DATE_TO,
            "status": {
                "type": "string", "enum": list(OrderRequest.Status.values),
                "description": "pending=waiting, sent=sent to supplier, approved, shipped, delivered, cancelled.",
            },
            "supplier": {"type": "string", "description": "Supplier name (partial match)."},
            "limit": {"type": "integer", "description": "Max orders to return (default 10, max 50)."},
        }},
    ),
    Tool(
        "get_order", get_order,
        "Full details of one order by its number, including every line.",
        {"type": "object", "properties": {"order_id": {"type": "integer"}}, "required": ["order_id"]},
    ),
    Tool(
        "get_current_prices", get_current_prices,
        "Current supplier prices for products, cheapest first. Use for 'who is cheapest' or 'what is the price now'.",
        {"type": "object", "properties": {
            "products": {**_NAMES, "description": "Product names as the user said them."},
            "region": {"type": "string", "enum": list(Region.values), "description": "Admin only."},
        }, "required": ["products"]},
    ),
    Tool(
        "get_suppliers", get_suppliers,
        "Suppliers with their region and minimum order amount.",
        {"type": "object", "properties": {
            "search": {"type": "string", "description": "Supplier name (partial match)."},
            "region": {"type": "string", "enum": list(Region.values), "description": "Admin only."},
        }},
    ),
    Tool(
        "list_products", list_products,
        "Search the product catalog by name or alias. Use it to find the exact product name when unsure.",
        {"type": "object", "properties": {"search": {"type": "string"}, "limit": {"type": "integer"}}},
    ),
    Tool(
        "list_customers", list_customers,
        "Admin only: list customers.",
        {"type": "object", "properties": {"search": {"type": "string"}}},
        admin_only=True,
    ),
]
_REGISTRY = {tool.name: tool for tool in TOOLS}
_ADMIN_ONLY_PARAMS = ("customer", "region")


def build_tool_schemas(is_admin: bool) -> list:
    schemas = []
    for tool in TOOLS:
        if tool.admin_only and not is_admin:
            continue
        params = copy.deepcopy(tool.parameters)
        if not is_admin:
            for name in _ADMIN_ONLY_PARAMS:
                params["properties"].pop(name, None)
            group_by = params["properties"].get("group_by")
            if group_by:
                group_by["enum"].remove("customer")
                group_by["description"] = "Break the total down by this dimension."
        schemas.append({"type": "function", "function": {
            "name": tool.name, "description": tool.description, "parameters": params,
        }})
    return schemas


def execute_tool(ctx: ToolContext, name: str, raw_args) -> dict:
    """Run one tool call. Never raises: problems come back as {"error": ...} for the model to read."""
    tool = _REGISTRY.get(name)
    if tool is None or (tool.admin_only and not ctx.is_admin):
        return {"error": f"unknown tool {name}"}
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else (raw_args or {})
        if not isinstance(args, dict):
            raise ValueError
    except ValueError:
        return {"error": "arguments must be a JSON object"}
    if not ctx.is_admin:
        for param in _ADMIN_ONLY_PARAMS:
            args.pop(param, None)
    try:
        return tool.fn(ctx, **args)
    except ToolError as exc:
        return {"error": str(exc)}
    except TypeError:
        return {"error": "invalid arguments"}
    except Exception:
        logger.exception("assistant tool %s failed", name)
        return {"error": "internal error"}
