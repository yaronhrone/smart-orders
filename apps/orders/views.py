from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema

from .models import OrderRequest, ShoppingList
from .serializers import (
    SuggestOrderInputSerializer,
    SuggestOrderResponseSerializer,
    PlaceOrderInputSerializer,
    PlaceOrderResponseSerializer,
    OrderListSerializer,
    OrderDetailSerializer,
    OrderStatusUpdateSerializer,
    ShoppingListSerializer,
    ShoppingListSuggestSerializer,
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
            order, whatsapp_links = build_order(
                user=request.user,
                region=region,
                products=products,
                scenario=scenario,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        response_data = {
            "order_id": order.id,
            "status": order.status,
            "total_price": order.total_price,
            "scenario": scenario,
            "whatsapp_links": list(whatsapp_links.values()),
        }

        return Response(
            PlaceOrderResponseSerializer(response_data).data,
            status=status.HTTP_201_CREATED,
        )
class OrderListView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    @extend_schema(responses=OrderListSerializer(many=True))
    def get(self, request):
        orders = (
            OrderRequest.objects
            .filter(user=request.user)
            .exclude(status=OrderRequest.Status.PENDING)
            .prefetch_related("products")
            .order_by("-created_at")
        )
        data = [
            {
                "id": o.id,
                "status": o.status,
                "total_price": o.total_price,
                "created_at": o.created_at,
                "product_count": o.products.count(),
            }
            for o in orders
        ]
        return Response(OrderListSerializer(data, many=True).data)
class OrderDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=OrderDetailSerializer)
    def get(self, request, pk):
        order = get_object_or_404(
            OrderRequest.objects.prefetch_related("products__product", "products__supplier"),
            pk=pk,
            user=request.user,
        )
        data = {
            "id": order.id,
            "status": order.status,
            "total_price": order.total_price,
            "created_at": order.created_at,
            "products": list(order.products.all()),
        }
        return Response(OrderDetailSerializer(data).data)
class OrderStatusUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        request=OrderStatusUpdateSerializer,
        responses={200: OrderStatusUpdateSerializer},
    )
    def patch(self, request, pk):
        order = get_object_or_404(OrderRequest, pk=pk, user=request.user)

        serializer = OrderStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order.status = serializer.validated_data["status"]
        order.save(update_fields=["status"])

        return Response({"id": order.id, "status": order.status})
class ShoppingListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=ShoppingListSerializer(many=True))
    def get(self, request):
        """GET /api/orders/shopping-lists/ — list the user's shopping lists."""
        lists = ShoppingList.objects.filter(user=request.user).prefetch_related("products__product").order_by("-id")
        return Response(ShoppingListSerializer(lists, many=True).data)

    @extend_schema(request=ShoppingListSerializer, responses={201: ShoppingListSerializer})
    def post(self, request):
        """POST /api/orders/shopping-lists/ — create a new shopping list."""
        serializer = ShoppingListSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        shopping_list = serializer.save(user=request.user)
        return Response(ShoppingListSerializer(shopping_list).data, status=status.HTTP_201_CREATED)


class ShoppingListDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self, pk, user):
        return get_object_or_404(
            ShoppingList.objects.prefetch_related("products__product"),
            pk=pk, user=user,
        )

    @extend_schema(responses=ShoppingListSerializer)
    def get(self, request, pk):
        """GET /api/orders/shopping-lists/{id}/"""
        return Response(ShoppingListSerializer(self.get_object(pk, request.user)).data)

    @extend_schema(request=ShoppingListSerializer, responses=ShoppingListSerializer)
    def put(self, request, pk):
        """PUT /api/orders/shopping-lists/{id}/ — replace name and products."""
        instance = self.get_object(pk, request.user)
        serializer = ShoppingListSerializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        """DELETE /api/orders/shopping-lists/{id}/"""
        self.get_object(pk, request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class OrderStatsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=OrderStatsSerializer)
    def get(self, request):
        """GET /api/orders/stats/ — spending totals per supplier for the current user."""
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
        return Response(OrderStatsSerializer(result).data)


class ShoppingListSuggestView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=ShoppingListSuggestSerializer, responses=SuggestOrderResponseSerializer)
    def post(self, request, pk):
        """POST /api/orders/shopping-lists/{id}/suggest/ — suggest order from saved list."""
        shopping_list = get_object_or_404(
            ShoppingList.objects.prefetch_related("products__product"),
            pk=pk, user=request.user,
        )
        serializer = ShoppingListSuggestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        products = [
            {"product": product.product, "quantity": product.default_quantity * 2}
            for product in shopping_list.products.all()
        ]

        try:
            region = serializer.validated_data["region"]
            result = suggest_order(user=request.user, region=region, products=products)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(SuggestOrderResponseSerializer(result).data)
