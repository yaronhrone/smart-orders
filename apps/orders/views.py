from rest_framework import generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from django.core.cache import cache
from django.db.models import Count, Prefetch
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema

from core.cache_utils import get_cache_version
from core.pagination import LoadMorePagination, paginate, paginated_response_serializer
from .models import OrderBatch, OrderRequest

ORDERS_CACHE_TTL = 300  # safety-net TTL; real invalidation happens via signals.py on any write
from .serializers import (
    SuggestOrderInputSerializer,
    SuggestOrderResponseSerializer,
    PlaceOrderInputSerializer,
    PlaceOrderResponseSerializer,
    OrderBatchSerializer,
    AdminOrderBatchSerializer,
    OrderDetailSerializer,
    OrderStatusUpdateSerializer,
    OrderStatsSerializer,
)
from decimal import Decimal
from collections import defaultdict
from .services import suggest_order, build_order
class SuggestOrderView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    @extend_schema(request=SuggestOrderInputSerializer, responses=SuggestOrderResponseSerializer)
    def post(self, request):
        serializer = SuggestOrderInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        if not hasattr(user, "profile"):
            return Response({"detail": "המשתמש אינו מקושר לפרופיל חברה"}, status=status.HTTP_400_BAD_REQUEST)
        products = serializer.validated_data["products"]
        region = user.profile.region
        try:
            result = suggest_order(user=request.user, region=region, products=products)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(SuggestOrderResponseSerializer(result).data)
class PlaceOrderView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    @extend_schema(request=PlaceOrderInputSerializer, responses={201: PlaceOrderResponseSerializer})
    def post(self, request):
        serializer = PlaceOrderInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user

        if not hasattr(user, "profile"):
            return Response({"detail": "המשתמש אינו מקושר לפרופיל חברה"}, status=status.HTTP_400_BAD_REQUEST)

        region = user.profile.region
        scenario = serializer.validated_data["scenario"]
        products = serializer.validated_data["products"]

        try:
            batch, orders, whatsapp_links = build_order(
                user=request.user,
                region=region,
                products=products,
                scenario=scenario,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            from apps.orders.whatsapp import notify_suppliers_for_batch
            notify_suppliers_for_batch(orders)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("Failed to notify suppliers for batch %s: %s", batch.id, exc)

        response_data = {
            "batch_id": batch.id,
            "total_price": sum((o.total_price for o in orders), Decimal(0)),
            "scenario": scenario,
            "orders": orders,
            "whatsapp_links": list(whatsapp_links.values()),
        }

        return Response(
            PlaceOrderResponseSerializer(response_data).data,
            status=status.HTTP_201_CREATED,
        )
def _batches_queryset():
    """Batches with their orders (supplier + product_count) prefetched — the
    shape OrderBatchSerializer expects."""
    orders_qs = (
        OrderRequest.objects
        .select_related("supplier")
        .annotate(product_count=Count("products"))
        .order_by("id")
    )
    return (
        OrderBatch.objects
        .prefetch_related(Prefetch("orders", queryset=orders_qs))
        .order_by("-created_at")
    )


class OrderBatchListView(APIView):
    """GET /api/orders/batches/ — the customer's checkouts, newest first,
    each with its per-supplier orders (dashboard: one row per checkout that
    expands into them)."""
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=paginated_response_serializer(OrderBatchSerializer))
    def get(self, request):
        limit = request.query_params.get("limit", "")
        offset = request.query_params.get("offset", "")
        version = get_cache_version("orders", request.user.id)
        cache_key = f"orders:batches:{request.user.id}:v{version}:{limit}:{offset}"

        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        batches = (
            _batches_queryset()
            .filter(user=request.user)
            # A batch whose every order is still PENDING never reached a
            # supplier — same reason the old flat list hid PENDING orders.
            .filter(orders__status__in=[
                s for s in OrderRequest.Status.values if s != OrderRequest.Status.PENDING
            ])
            .distinct()
        )
        page, has_more = paginate(request, batches, default_limit=10)
        payload = {
            "results": OrderBatchSerializer(page, many=True).data,
            "has_more": has_more,
        }
        cache.set(cache_key, payload, timeout=ORDERS_CACHE_TTL)
        return Response(payload)


class OrderDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=OrderDetailSerializer)
    def get(self, request, pk):
        version = get_cache_version("orders", request.user.id)
        cache_key = f"orders:detail:{request.user.id}:v{version}:{pk}"

        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        order = get_object_or_404(
            OrderRequest.objects
            .select_related("supplier", "batch")
            .prefetch_related("products__product", "products__supplier"),
            pk=pk,
            user=request.user,
        )
        data = {
            "id": order.id,
            "status": order.status,
            "total_price": order.total_price,
            "created_at": order.created_at,
            "supplier_id": order.supplier_id,
            "supplier_name": order.supplier.name,
            "batch_id": order.batch_id,
            "batch_created_at": order.batch.created_at,
            "products": list(order.products.all()),
        }
        payload = OrderDetailSerializer(data).data
        cache.set(cache_key, payload, timeout=ORDERS_CACHE_TTL)
        return Response(payload)
class OrderStatusUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        request=OrderStatusUpdateSerializer,
        responses={200: OrderStatusUpdateSerializer},
    )
    def patch(self, request, pk):
        # Staff can unstick any customer's order (e.g. a test order with no
        # real supplier to confirm it) — everyone else only their own.
        if request.user.is_staff:
            order = get_object_or_404(OrderRequest, pk=pk)
        else:
            order = get_object_or_404(OrderRequest, pk=pk, user=request.user)

        serializer = OrderStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_status = serializer.validated_data["status"]
        try:
            order.transition_to(new_status)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"id": order.id, "status": order.status})


class AdminOrderBatchListView(generics.ListAPIView):
    """
    GET /api/orders/admin/batches/ — admin-only, cross-customer checkouts,
    each with its per-supplier orders. Defaults to checkouts with at least
    one order still open (not DELIVERED/CANCELLED) so a stuck test order —
    one whose supplier has no real WhatsApp number to confirm from — is easy
    to find and close via OrderStatusUpdateView above. ?status=<value> shows
    checkouts with at least one order in that status instead.
    """
    permission_classes = [permissions.IsAdminUser]
    serializer_class = AdminOrderBatchSerializer
    pagination_class = LoadMorePagination

    def get_queryset(self):
        qs = _batches_queryset().select_related("user", "user__profile")
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(orders__status=status_param)
        else:
            qs = qs.filter(
                orders__status__in=[
                    s for s in OrderRequest.Status.values
                    if s not in (OrderRequest.Status.DELIVERED, OrderRequest.Status.CANCELLED)
                ]
            )
        return qs.distinct()


class OrderStatsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=OrderStatsSerializer)
    def get(self, request):
        """GET /api/orders/stats/ — spending totals per supplier for the current user."""
        version = get_cache_version("orders", request.user.id)
        cache_key = f"orders:stats:{request.user.id}:v{version}"

        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        orders = (
            OrderRequest.objects
            .filter(user=request.user)
            .prefetch_related("products__supplier")
        )

        total_spent = Decimal("0")
        order_count = orders.count()
        supplier_totals = defaultdict(lambda: {"total": Decimal("0"), "count": 0, "name": ""})

        for order in orders:
            for item in order.products.all():
                line = item.quantity * item.unit_price
                total_spent += line
                sid = item.supplier.id
                supplier_totals[sid]["total"] += line
                supplier_totals[sid]["count"] += 1
                supplier_totals[sid]["name"] = item.supplier.name

        by_supplier = sorted(
            [
                {
                    "supplier_id": sid,
                    "supplier_name": v["name"],
                    "total_spent": v["total"],
                    "order_count": v["count"],
                }
                for sid, v in supplier_totals.items()
            ],
            key=lambda x: x["total_spent"],
            reverse=True,
        )

        result = {
            "total_spent": total_spent,
            "order_count": order_count,
            "by_supplier": by_supplier,
        }
        payload = OrderStatsSerializer(result).data
        cache.set(cache_key, payload, timeout=ORDERS_CACHE_TTL)
        return Response(payload)


