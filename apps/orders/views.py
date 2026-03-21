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
)
from .services import suggest_order, build_order


class SuggestOrderView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=SuggestOrderInputSerializer, responses=SuggestOrderResponseSerializer)
    def post(self, request):
        serializer = SuggestOrderInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user

        if not hasattr(user, "profile"):
            return Response({"detail": "User has no profile"}, status=status.HTTP_400_BAD_REQUEST)

        region = user.profile.region
        try:
            result = suggest_order(user=request.user, region=region, items=items)
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
            return Response({"detail": "User has no profile"}, status=status.HTTP_400_BAD_REQUEST)

        region = user.profile.region
        scenario = serializer.validated_data["scenario"]
        items = serializer.validated_data["items"]

        try:
            order, whatsapp_links = build_order(
                user=request.user,
                region=region,
                items=items,
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
            .prefetch_related("items")
            .order_by("-created_at")
        )
        data = [
            {
                "id": o.id,
                "status": o.status,
                "total_price": o.total_price,
                "created_at": o.created_at,
                "item_count": o.items.count(),
            }
            for o in orders
        ]
        return Response(OrderListSerializer(data, many=True).data)


class OrderDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=OrderDetailSerializer)
    def get(self, request, pk):
        order = get_object_or_404(
            OrderRequest.objects.prefetch_related("items__product", "items__supplier"),
            pk=pk,
            user=request.user,
        )
        data = {
            "id": order.id,
            "status": order.status,
            "total_price": order.total_price,
            "created_at": order.created_at,
            "items": list(order.items.all()),
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
        lists = ShoppingList.objects.filter(user=request.user).prefetch_related("items__product").order_by("-id")
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
            ShoppingList.objects.prefetch_related("items__product"),
            pk=pk, user=user,
        )

    @extend_schema(responses=ShoppingListSerializer)
    def get(self, request, pk):
        """GET /api/orders/shopping-lists/{id}/"""
        return Response(ShoppingListSerializer(self.get_object(pk, request.user)).data)

    @extend_schema(request=ShoppingListSerializer, responses=ShoppingListSerializer)
    def put(self, request, pk):
        """PUT /api/orders/shopping-lists/{id}/ — replace name and items."""
        instance = self.get_object(pk, request.user)
        serializer = ShoppingListSerializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        """DELETE /api/orders/shopping-lists/{id}/"""
        self.get_object(pk, request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ShoppingListSuggestView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=ShoppingListSuggestSerializer, responses=SuggestOrderResponseSerializer)
    def post(self, request, pk):
        """POST /api/orders/shopping-lists/{id}/suggest/ — suggest order from saved list."""
        shopping_list = get_object_or_404(
            ShoppingList.objects.prefetch_related("items__product"),
            pk=pk, user=request.user,
        )
        serializer = ShoppingListSuggestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        items = [
            {"product": item.product, "quantity": item.default_quantity}
            for item in shopping_list.items.all()
        ]

        try:
            if not hasattr(request.user, "profile"):
                return Response({"detail": "User has no profile"}, status=status.HTTP_400_BAD_REQUEST)

            region = request.user.profile.region
            result = suggest_order(user=request.user, region=region , items=items)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(SuggestOrderResponseSerializer(result).data)
