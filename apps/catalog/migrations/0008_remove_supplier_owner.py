from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0007_alter_product_unit"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="supplier",
            name="owner",
        ),
    ]
