from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0014_productalias"),
    ]

    operations = [
        migrations.AddField(
            model_name="supplier",
            name="blocked_until",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="Excluded from new order assignment/reroute until this time "
                           "(e.g. after cancelling an order) — set automatically, not by an admin form.",
            ),
        ),
    ]
