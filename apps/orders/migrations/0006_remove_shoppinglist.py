from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0005_orderrequest_delivered_status"),
    ]

    operations = [
        migrations.DeleteModel(name="ShoppingListProduct"),
        migrations.DeleteModel(name="ShoppingList"),
    ]
