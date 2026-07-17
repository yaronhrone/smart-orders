from django.test import TestCase
from django.contrib.auth import get_user_model
from apps.catalog.models import Product, Supplier, SupplierProduct, Unit, Region

User = get_user_model()


class ProductModelTests(TestCase):

    def test_create_product(self):
        """Product is created with correct name and unit"""
        product = Product.objects.create(name="עגבנייה", unit=Unit.KG)
        self.assertEqual(product.name, "עגבנייה")
        self.assertEqual(product.unit, Unit.KG)

    def test_product_name_unique(self):
        """Cannot create two products with same name"""
        Product.objects.create(name="מלפפון", unit=Unit.KG)
        with self.assertRaises(Exception):
            Product.objects.create(name="מלפפון", unit=Unit.KG)

    def test_product_default_unit_is_kg(self):
        """Default unit is KG"""
        product = Product.objects.create(name="תפוח")
        self.assertEqual(product.unit, Unit.KG)

    def test_product_str(self):
        """__str__ returns name with unit"""
        product = Product.objects.create(name="בצל", unit=Unit.KG)
        self.assertIn("בצל", str(product))


class SupplierModelTests(TestCase):

    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="admin123")
        self.user = User.objects.create_user(email="user@test.com", password="pass123")

    def test_create_supplier(self):
        """Supplier is created with correct fields"""
        supplier = Supplier.objects.create(
            name="ירקות הצפון",
            phone="0501234567",
            whatsapp_number="0501234567",
            region=Region.NORTH,
            minimum_order=200,
        )
        self.assertEqual(supplier.name, "ירקות הצפון")
        self.assertEqual(supplier.region, Region.NORTH)

    def test_supplier_str(self):
        """__str__ returns supplier name"""
        supplier = Supplier.objects.create(
            name="ירקות השוק",
            phone="050000",
            whatsapp_number="050000",
            region=Region.TEL_AVIV,
        )
        self.assertEqual(str(supplier), "ירקות השוק")


class SupplierProductModelTests(TestCase):

    def setUp(self):
        self.product = Product.objects.create(name="גזר", unit=Unit.KG)
        self.supplier = Supplier.objects.create(
            name="ירקות מרכז",
            phone="0501111111",
            whatsapp_number="0501111111",
            region=Region.CENTER,
            minimum_order=150,
        )

    def test_create_supplier_product(self):
        """SupplierProduct links supplier, product and price"""
        sp = SupplierProduct.objects.create(
            supplier=self.supplier,
            product=self.product,
            price_per_unit=3.50,
        )
        self.assertEqual(sp.supplier, self.supplier)
        self.assertEqual(sp.product, self.product)
        self.assertEqual(float(sp.price_per_unit), 3.50)

    def test_supplier_product_unique_together(self):
        """Cannot add same product twice for the same supplier"""
        SupplierProduct.objects.create(supplier=self.supplier, product=self.product, price_per_unit=3.50)
        with self.assertRaises(Exception):
            SupplierProduct.objects.create(supplier=self.supplier, product=self.product, price_per_unit=4.00)

    def test_supplier_product_str(self):
        """__str__ includes supplier name, product name, and price"""
        sp = SupplierProduct.objects.create(supplier=self.supplier, product=self.product, price_per_unit=3.50)
        self.assertIn("ירקות מרכז", str(sp))
        self.assertIn("גזר", str(sp))
