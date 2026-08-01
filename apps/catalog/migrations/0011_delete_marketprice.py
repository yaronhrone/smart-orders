from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0010_alter_product_unit"),
    ]

    operations = [
        migrations.DeleteModel(
            name="MarketPrice",
        ),
    ]
