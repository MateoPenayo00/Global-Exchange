from decimal import Decimal

from django.db import migrations


def create_usd(apps, schema_editor):
    Currency = apps.get_model("core", "Currency")
    Currency.objects.get_or_create(
        code="USD",
        defaults={
            "name": "Dólar estadounidense",
            "symbol": "$",
            "value_in_usd": Decimal("1.000000"),
            "is_active": True,
        },
    )


def remove_usd(apps, schema_editor):
    Currency = apps.get_model("core", "Currency")
    Currency.objects.filter(code="USD").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_usd, remove_usd),
    ]
