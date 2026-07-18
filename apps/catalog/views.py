from django.conf import settings
from rest_framework import generics, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from .price_parser import update_prices_from_message
from .market_scraper import fetch_vegetable_prices
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
    POST /api/catalog/products/        — create a new product only by admin
    """
    def get_permissions(self):
        if self.request.method == "POST":
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProductSerializer
    queryset = Product.objects.all().order_by("name")


class ProductDestroyView(generics.DestroyAPIView):
    """
    DELETE /api/catalog/products/{id}/  — admin only
    """
    permission_classes = [permissions.IsAdminUser]
    queryset = Product.objects.all()


class ProductBulkCreateView(APIView):
    """
    POST /api/catalog/products/bulk/  — admin only
    Body: {"products": [{"name": "עגבנייה", "unit": "kg"}, ...]}
    Skips duplicates silently and reports results.
    """
    permission_classes = [permissions.IsAdminUser]

    def post(self, request):
        items = request.data.get("products", [])
        if not isinstance(items, list):
            return Response({"error": "שדה 'products' חייב להיות רשימה"}, status=status.HTTP_400_BAD_REQUEST)

        created, skipped = [], []
        for item in items:
            name = str(item.get("name", "")).strip()
            unit = str(item.get("unit", "kg")).strip()
            if not name:
                continue
            _, was_created = Product.objects.get_or_create(name=name, defaults={"unit": unit})
            (created if was_created else skipped).append(name)

        return Response({
            "created": len(created),
            "skipped_duplicates": len(skipped),
            "created_names": created,
            "skipped_names": skipped,
        }, status=status.HTTP_201_CREATED)


class SupplierUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/catalog/suppliers/{id}/  — retrieve supplier (authenticated)
    PATCH  /api/catalog/suppliers/{id}/  — update supplier fields (admin only)
    DELETE /api/catalog/suppliers/{id}/  — delete supplier + all prices (admin only)
    """
    serializer_class = SupplierSerializer
    queryset = Supplier.objects.all()

    def get_permissions(self):
        if self.request.method in ("PATCH", "PUT", "DELETE"):
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)


class SupplierListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/catalog/suppliers/?region=center  — list all suppliers (authenticated)
    POST /api/catalog/suppliers/                — create a supplier (admin only)
    """
    def get_permissions(self):
        if self.request.method == "POST":
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return SupplierCreateSerializer
        return SupplierSerializer

    def get_queryset(self):
        region = self.request.query_params.get("region")
        qs = Supplier.objects.prefetch_related("products__product")
        if region:
            return qs.filter(region=region)
        return qs.all()


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


class SupplierPriceUpdateView(APIView):
    """
    POST /api/catalog/suppliers/prices/
    Admin-only: manually update a supplier's prices for existing catalog products.
    """
    permission_classes = [permissions.IsAdminUser]

    @extend_schema(
        request=SupplierPriceUpdateSerializer,
        responses={200: dict},
    )
    def post(self, request):
        serializer = SupplierPriceUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        prices = serializer.validated_data["prices"]
        phone = request.data.get("phone")
        clean_phone = "".join(filter(str.isdigit, phone))

        try:
            supplier = Supplier.objects.get(phone=clean_phone)
        except Supplier.DoesNotExist:
            return Response({"error": "הספק לא נמצא"}, status=status.HTTP_404_NOT_FOUND)

        updated_items = []
        not_found = []

        for item in prices:
            product_name = item["product_name"].strip()

            try:
                product = Product.objects.get(name__iexact=product_name)
            except Product.DoesNotExist:
                not_found.append(product_name)
                continue

            sp, created = SupplierProduct.objects.update_or_create(
                supplier=supplier,
                product=product,
                defaults={"price_per_unit": item["price_per_unit"]},
            )
            updated_items.append({
                "product_name": product.name,
                "price_per_unit": str(sp.price_per_unit),
                "created": created,
            })

        response = {"message": "Prices updated", "items": updated_items}
        if not_found:
            response["not_found"] = not_found
        return Response(response)

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


class MarketPriceRawScrapeView(APIView):
    """
    GET /api/catalog/market-prices/raw/
    Admin-only: live scrape of the plant council site, unfiltered by catalog matching.
    Lets an admin see everything currently listed on the site (including items not
    yet in the catalog) before entering prices for suppliers manually.
    """
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        url = getattr(settings, "PLANT_COUNCIL_PRICES_URL", "")
        if not url:
            return Response({"error": "PLANT_COUNCIL_PRICES_URL not configured"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            rows = fetch_vegetable_prices(url)
        except Exception as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        catalog_names = set(Product.objects.values_list("name", flat=True))
        return Response([
            {
                "name": row["name"],
                "in_catalog": row["name"] in catalog_names,
                "market_date": row["market_date"],
                "price_grade_a": row["price_grade_a"],
                "price_premium": row["price_premium"],
            }
            for row in rows
        ])