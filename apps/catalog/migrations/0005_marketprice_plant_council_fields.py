from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0004_alter_supplier_phone_alter_supplier_whatsapp_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="marketprice",
            name="price_grade_a",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name="marketprice",
            name="price_premium",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name="marketprice",
            name="market_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="marketprice",
            name="price_per_unit",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AlterField(
            model_name="marketprice",
            name="source",
            field=models.CharField(default="מועצת הצמחים", max_length=255),
        ),
    ]
