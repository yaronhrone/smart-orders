from django.test import TestCase
from django.contrib.auth import get_user_model
from apps.catalog.models import Product, Supplier, Unit, Region
from apps.orders.models import ShoppingList, ShoppingListProduct, OrderRequest, OrderRequestProduct

User = get_user_model()


def make_user(email="user@test.com"):
    return User.objects.create_user(email=email, password="pass1234")


def make_product(name="tomato"):
    return Product.objects.create(name=name, unit=Unit.KG)


def make_supplier(name="supplier A", minimum_order=100):
    return Supplier.objects.create(
        name=name,
        phone="0500000000",
        whatsapp_number="0500000000",
        region=Region.CENTER,
        minimum_order=minimum_order,
    )


class ShoppingListModelTests(TestCase):

    def setUp(self):
        self.user = make_user()

    def test_create_shopping_list(self):
        """ShoppingList is created with correct user and name"""
        sl = ShoppingList.objects.create(user=self.user, name="pattren weekly")
        self.assertEqual(sl.user, self.user)
        self.assertEqual(sl.name, "pattren weekly")

    def test_shopping_list_str(self):
        """__str__ contains user email and list name"""
        sl = ShoppingList.objects.create(user=self.user, name="pattren weekly")
        self.assertIn(self.user.email, str(sl))
        self.assertIn("pattren weekly", str(sl))

    def test_shopping_list_deleted_with_user(self):
        """ShoppingList deleted when user is deleted"""
        sl = ShoppingList.objects.create(user=self.user, name="pattren weekly")
        self.user.delete()
        self.assertFalse(ShoppingList.objects.filter(id=sl.id).exists())


class ShoppingListItemModelTests(TestCase):

    def setUp(self):
        self.user = make_user()
        self.product = make_product()
        self.sl = ShoppingList.objects.create(user=self.user, name="pattren weekly")

    def test_create_item(self):
        """ShoppingListProduct stores product and quantity"""
        item = ShoppingListProduct.objects.create(shopping_list=self.sl, product=self.product, default_quantity=5)
        self.assertEqual(item.product, self.product)
        self.assertEqual(float(item.default_quantity), 5)

    def test_item_unique_per_list(self):
        """Cannot add same product twice to same list"""
        ShoppingListProduct.objects.create(shopping_list=self.sl, product=self.product, default_quantity=5)
        with self.assertRaises(Exception):
            ShoppingListProduct.objects.create(shopping_list=self.sl, product=self.product, default_quantity=3)

    def test_item_str(self):
        """__str__ includes product name and quantity"""
        item = ShoppingListProduct.objects.create(shopping_list=self.sl, product=self.product, default_quantity=5)
        self.assertIn("tomato", str(item))


class OrderRequestModelTests(TestCase):

    def setUp(self):
        self.user = make_user()

    def test_create_order_default_status(self):
        """New OrderRequest starts as pending"""
        order = OrderRequest.objects.create(user=self.user)
        self.assertEqual(order.status, OrderRequest.Status.PENDING)

    def test_order_status_transitions(self):
        """Status can be changed to approved and sent"""
        order = OrderRequest.objects.create(user=self.user)

        order.status = OrderRequest.Status.APPROVED
        order.save()
        self.assertEqual(OrderRequest.objects.get(id=order.id).status, OrderRequest.Status.APPROVED)

        order.status = OrderRequest.Status.SENT
        order.save()
        self.assertEqual(OrderRequest.objects.get(id=order.id).status, OrderRequest.Status.SENT)

    def test_order_str(self):
        """__str__ includes order id and user email"""
        order = OrderRequest.objects.create(user=self.user)
        self.assertIn(self.user.email, str(order))
        self.assertIn(str(order.id), str(order))

    def test_order_deleted_with_user(self):
        """OrderRequest deleted when user is deleted"""
        order = OrderRequest.objects.create(user=self.user)
        self.user.delete()
        self.assertFalse(OrderRequest.objects.filter(id=order.id).exists())


class OrderRequestItemModelTests(TestCase):

    def setUp(self):
        self.user = make_user()
        self.product = make_product()
        self.supplier = make_supplier()
        self.order = OrderRequest.objects.create(user=self.user)

    def test_create_order_item(self):
        """OrderRequestProduct stores product, supplier, quantity and price"""
        product = OrderRequestProduct.objects.create(
            order_request=self.order,
            product=self.product,
            supplier=self.supplier,
            quantity=10,
            unit_price=3.50,
        )
        self.assertEqual(product.product, self.product)
        self.assertEqual(product.supplier, self.supplier)
        self.assertEqual(float(product.quantity), 10)
        self.assertEqual(float(product.unit_price), 3.50)

    def test_order_item_subtotal(self):
        """subtotal = quantity * unit_price"""
        product = OrderRequestProduct.objects.create(
            order_request=self.order,
            product=self.product,
            supplier=self.supplier,
            quantity=10,
            unit_price=3.50,
        )
        self.assertEqual(float(product.subtotal), 35.0)

    def test_order_item_str(self):
        """__str__ includes product name and supplier name"""
        product = OrderRequestProduct.objects.create(
            order_request=self.order,
            product=self.product,
            supplier=self.supplier,
            quantity=10,
            unit_price=3.50,
        )
        self.assertIn("tomato", str(product))
        self.assertIn("supplier A", str(product))
