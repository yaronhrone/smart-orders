from rest_framework import generics, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from .price_parser import update_prices_from_message
from drf_spectacular.utils import extend_schema
from .models import Product, Supplier, SupplierProduct, MarketPrice
from .serializers import (
    ProductSerializer, SupplierSerializer, SupplierCreateSerializer,
    PriceMessageSerializer, PriceUpdateResultSerializer, SupplierPriceUpdateSerializer,
    SupplierWithProductsSerializer, MarketPriceSerializer,
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


# class SupplierUpdatePricesView(APIView):
#     """
#     POST /api/catalog/suppliers/{id}/update-prices/

#     Accepts a free-text price message, uses AI to parse it,
#     and updates the supplier's product prices in the DB.
#     """
#     permission_classes = [permissions.IsAuthenticated]

#     @extend_schema(request=PriceMessageSerializer, responses={200: PriceUpdateResultSerializer})
#     def post(self, request, pk):
#         supplier = get_object_or_404(Supplier, pk=pk)

#         serializer = PriceMessageSerializer(data=request.data)
#         serializer.is_valid(raise_exception=True)

#         from .tasks import update_supplier_prices_task
#         task = update_supplier_prices_task.delay(supplier.id, serializer.validated_data["message"])

#         return Response(
#             {"detail": "Price update queued.", "task_id": task.id},
#             status=status.HTTP_202_ACCEPTED,
#         )
class SupplierPriceMessageView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = PriceMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone = request.data.get("phone")
        message = serializer.validated_data["message"]

        # normalize phone
        clean_phone = "".join(filter(str.isdigit, phone))

        try:
            supplier = Supplier.objects.get(phone=clean_phone)
        except Supplier.DoesNotExist:
            return Response(
                {"error": "Supplier not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        try:
            result = update_prices_from_message(supplier, message)
        except ValueError as e:
            return Response({"error": str(e)}, status=400)

        return Response(PriceUpdateResultSerializer(result).data)


    #  update supplier prices for POC
class SupplierPriceUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    @extend_schema(
        request=SupplierPriceUpdateSerializer,
        responses={200: dict},
    )
    def post(self, request):
        serializer = SupplierPriceUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not serializer.is_valid():
            print("ERRORS:", serializer.errors)
            return Response(serializer.errors, status=400)

        # 🔥 זה הדיבאג השני
        print("VALIDATED DATA:", serializer.validated_data)

        prices = serializer.validated_data.get("prices", [])

        # 🔥 זה הכי חשוב
        print("PRICES:", prices)

        prices = serializer.validated_data["prices"]
        phone = request.data.get("phone")
        clean_phone = "".join(filter(str.isdigit, phone))

        try:
            supplier = Supplier.objects.get(phone=clean_phone)
        except Supplier.DoesNotExist:
            return Response(
                {"error": "Supplier not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        # 🔥 optional security check
        if supplier.owner and supplier.owner != request.user:
            return Response(
                {"error": "Not allowed"},
                status=status.HTTP_403_FORBIDDEN
            )

        updated_items = []

        for item in prices:
            product_name = item["product_name"].lower().strip()
            unit = item["unit"]

            product, _ = Product.objects.get_or_create(
                name=product_name,
                defaults={"unit": unit}
            )

            if product.unit != unit:
                product.unit = unit
                product.save()

            sp, created = SupplierProduct.objects.update_or_create(
                supplier=supplier,
                product=product,
                defaults={"price_per_unit": item["price_per_unit"]}
            )

            updated_items.append({
                "product_name": product.name,
                "price_per_unit": str(sp.price_per_unit),
                "created": created
            })

        return Response({
            "message": "Prices updated successfully",
            "items": updated_items
        })

class SupplierPricesListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SupplierWithProductsSerializer

    def get_queryset(self):
        return Supplier.objects.prefetch_related("products__product").order_by("name")


class MarketPriceListView(generics.ListAPIView):
    """
    GET /api/catalog/market-prices/
    מחזיר את מחירי השוק העדכניים ממועצת הצמחים — שם מוצר, יחידה, סוג א', מובחר, תאריך.
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MarketPriceSerializer

    def get_queryset(self):
        return MarketPrice.objects.select_related("product").order_by("product__name")