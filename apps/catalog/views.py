from rest_framework import generics, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from .models import Product, Supplier
from .serializers import (
    ProductSerializer, SupplierSerializer, SupplierCreateSerializer,
    PriceMessageSerializer, PriceUpdateResultSerializer,
)


class ProductListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/catalog/products/        — list all products
    POST /api/catalog/products/        — create a new product
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProductSerializer
    queryset = Product.objects.all().order_by("name")


class SupplierListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/catalog/suppliers/?region=center  — list available suppliers
    POST /api/catalog/suppliers/                — create a private supplier (+ optional prices)
    """
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return SupplierCreateSerializer
        return SupplierSerializer

    def get_queryset(self):
        region = self.request.query_params.get("region")
        qs = Supplier.objects.prefetch_related("products__product")
        if region:
            return qs.filter(region=region, owner__isnull=True) | qs.filter(owner=self.request.user)
        return qs.filter(owner__isnull=True) | qs.filter(owner=self.request.user)

    def perform_create(self, serializer):
        # Supplier created via API is always private (owned by the requesting user)
        serializer.save(owner=self.request.user)


class SupplierUpdatePricesView(APIView):
    """
    POST /api/catalog/suppliers/{id}/update-prices/

    Accepts a free-text price message, uses AI to parse it,
    and updates the supplier's product prices in the DB.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=PriceMessageSerializer, responses={200: PriceUpdateResultSerializer})
    def post(self, request, pk):
        supplier = get_object_or_404(Supplier, pk=pk)

        serializer = PriceMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from .tasks import update_supplier_prices_task
        task = update_supplier_prices_task.delay(supplier.id, serializer.validated_data["message"])

        return Response(
            {"detail": "Price update queued.", "task_id": task.id},
            status=status.HTTP_202_ACCEPTED,
        )
