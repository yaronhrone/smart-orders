from datetime import date

from rest_framework import generics, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from .price_parser import update_prices_from_message
from .permissions import IsMarketAgent
from drf_spectacular.utils import extend_schema
from .models import Product, Supplier, SupplierProduct, MarketPrice
from .serializers import (
    ProductSerializer, SupplierSerializer, SupplierCreateSerializer,
    PriceMessageSerializer, PriceUpdateResultSerializer, SupplierPriceUpdateSerializer,
    SupplierWithProductsSerializer, MarketPriceSerializer,
    MarketPricesPushSerializer,
)


class ProductListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/catalog/products/        — list all products
    POST /api/catalog/products/        — create a new product
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProductSerializer
    queryset = Product.objects.all().order_by("name")


class ProductDestroyView(generics.DestroyAPIView):
    """
    DELETE /api/catalog/products/{id}/  — admin only
    """
    permission_classes = [permissions.IsAdminUser]
    queryset = Product.objects.all()


class SupplierUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    """
    PATCH  /api/catalog/suppliers/{id}/  — update supplier fields
    DELETE /api/catalog/suppliers/{id}/  — delete supplier + all prices (cascade)
    Admin can act on any supplier; owner can act on their own private supplier.
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SupplierSerializer

    def get_queryset(self):
        if self.request.user.is_staff:
            return Supplier.objects.all()
        return Supplier.objects.filter(owner=self.request.user)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)


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
        # Admin-created suppliers are global (owner=None); regular users own their own
        owner = None if self.request.user.is_staff else self.request.user
        serializer.save(owner=owner)


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
                {"error": "הספק לא נמצא"},
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
                {"error": "הספק לא נמצא"},
                status=status.HTTP_404_NOT_FOUND
            )

        # 🔥 optional security check
        if supplier.owner and supplier.owner != request.user:
            return Response(
                {"error": "אין הרשאה לפעולה זו"},
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


class ProductCatalogView(APIView):
    """
    GET /api/catalog/product-prices/?search=עגבנייה
    מוצר-מרכז: כל מוצר + מחירי כל הספקים + מחיר שוק.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        search = request.query_params.get("search", "").strip()
        products = (
            Product.objects
            .prefetch_related("supplier_prices__supplier", "market_price")
            .order_by("name")
        )
        if search:
            products = products.filter(name__icontains=search)

        result = []
        for product in products:
            prices = sorted(
                product.supplier_prices.all(),
                key=lambda sp: sp.price_per_unit,
            )
            cheapest_price = prices[0].price_per_unit if prices else None

            mp = getattr(product, "market_price", None)

            result.append({
                "product_id": product.id,
                "product_name": product.name,
                "unit": product.unit,
                "unit_display": product.get_unit_display(),
                "market_price": mp.price_per_unit if mp else None,
                "market_grade_a": mp.price_grade_a if mp else None,
                "market_premium": mp.price_premium if mp else None,
                "market_date": mp.market_date if mp else None,
                "cheapest_price": cheapest_price,
                "cheapest_supplier_name": prices[0].supplier.name if prices else None,
                "suppliers": [
                    {
                        "id": sp.supplier.id,
                        "name": sp.supplier.name,
                        "region": sp.supplier.region,
                        "price": sp.price_per_unit,
                        "updated_at": sp.updated_at,
                        "is_cheapest": sp.price_per_unit == cheapest_price,
                    }
                    for sp in prices
                ],
            })

        return Response(result)


class MarketPriceListView(generics.ListAPIView):
    """
    GET /api/catalog/market-prices/
    מחזיר את מחירי השוק העדכניים ממועצת הצמחים — שם מוצר, יחידה, סוג א', מובחר, תאריך.
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MarketPriceSerializer

    def get_queryset(self):
        return MarketPrice.objects.select_related("product").order_by("product__name")


class MarketPricesPushView(APIView):
    """
    POST /api/catalog/market-prices/push/

    Authenticated by a pre-shared API key (Authorization: Api-Key <MARKET_AGENT_SECRET>).
    Accepts a bulk payload from the local Market Agent and upserts MarketPrice records.

    Expected payload:
        {
            "prices": [
                {
                    "product_name": "עגבניה",
                    "price_grade_a": "3.50",
                    "price_premium": "4.20",
                    "market_date": "2026-05-28"
                }
            ]
        }

    Both price fields are optional; at least one must be present or the item is skipped.
    market_date defaults to today if omitted.
    product_name is matched case-insensitively and auto-created if it doesn't exist.
    """

    authentication_classes = []  # skip JWT — agent uses Api-Key header
    permission_classes = [IsMarketAgent]

    def post(self, request):
        serializer = MarketPricesPushSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        items = serializer.validated_data["prices"]
        today = date.today()
        created_count = 0
        updated_count = 0
        skipped = []

        for item in items:
            product_name = item["product_name"].strip()
            if not product_name:
                continue

            grade_a = item.get("price_grade_a")
            premium = item.get("price_premium")
            main_price = grade_a if grade_a is not None else premium

            if main_price is None:
                skipped.append({"product_name": product_name, "reason": "no price provided"})
                continue

            product, _ = Product.objects.get_or_create(name=product_name)

            _, created = MarketPrice.objects.update_or_create(
                product=product,
                defaults={
                    "price_per_unit": main_price,
                    "price_grade_a": grade_a,
                    "price_premium": premium,
                    "market_date": item.get("market_date") or today,
                    "source": "market-agent",
                },
            )

            if created:
                created_count += 1
            else:
                updated_count += 1

        return Response(
            {
                "created": created_count,
                "updated": updated_count,
                "skipped": skipped,
                "total_received": len(items),
            },
            status=status.HTTP_200_OK,
        )