"""Sprint 3: guaraní como divisa base, precios de compra/venta, medios de pago,
historial de cotizaciones y reloj de simulación.

Esta migración sólo cambia el *esquema*. La conversión de los datos existentes y
la carga de datos de demostración se hacen en ``0004_seed_guarani_and_history``.
"""

import datetime
from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_seed_usd"),
    ]

    operations = [
        # --- Currency: un precio de compra y uno de venta, en guaraníes -------
        migrations.RenameField(
            model_name="currency",
            old_name="value_in_usd",
            new_name="buy_rate",
        ),
        migrations.AlterField(
            model_name="currency",
            name="buy_rate",
            field=models.DecimalField(
                decimal_places=6,
                help_text="Precio de compra: guaraníes que la casa de cambio paga por 1 unidad de esta divisa.",
                max_digits=18,
            ),
        ),
        migrations.AddField(
            model_name="currency",
            name="sell_rate",
            field=models.DecimalField(
                decimal_places=6,
                default=Decimal("1.000000"),
                help_text="Precio de venta: guaraníes que la casa de cambio cobra por 1 unidad de esta divisa.",
                max_digits=18,
            ),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="currency",
            name="code",
            field=models.CharField(
                help_text="Código ISO-like, p. ej. USD, EUR, PYG.", max_length=8, unique=True
            ),
        ),
        migrations.AlterModelOptions(
            name="currency",
            options={"ordering": ["code"], "verbose_name": "divisa", "verbose_name_plural": "divisas"},
        ),
        # --- Wallet / WalletTransaction: los montos pasan a guaraníes ---------
        migrations.RenameField(
            model_name="wallet",
            old_name="usd_balance",
            new_name="pyg_balance",
        ),
        migrations.RenameField(
            model_name="wallettransaction",
            old_name="usd_amount",
            new_name="pyg_amount",
        ),
        migrations.AlterField(
            model_name="wallettransaction",
            name="kind",
            field=models.CharField(
                choices=[
                    ("deposit", "Carga de saldo"),
                    ("buy", "Compra de divisa"),
                    ("sell", "Venta de divisa"),
                    ("withdraw", "Retiro de divisa"),
                ],
                max_length=10,
            ),
        ),
        migrations.AlterModelOptions(
            name="wallet",
            options={"verbose_name": "billetera", "verbose_name_plural": "billeteras"},
        ),
        migrations.AlterModelOptions(
            name="walletholding",
            options={
                "ordering": ["currency__code"],
                "verbose_name": "tenencia",
                "verbose_name_plural": "tenencias",
            },
        ),
        migrations.AlterModelOptions(
            name="wallettransaction",
            options={
                "ordering": ["-created_at"],
                "verbose_name": "movimiento",
                "verbose_name_plural": "movimientos",
            },
        ),
        # --- Historial de cotizaciones ---------------------------------------
        migrations.CreateModel(
            name="CurrencyRate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField()),
                ("buy_rate", models.DecimalField(decimal_places=6, max_digits=18)),
                ("sell_rate", models.DecimalField(decimal_places=6, max_digits=18)),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("manual", "Carga manual"),
                            ("simulacion", "Simulación de día"),
                            ("inicial", "Valor inicial"),
                        ],
                        default="manual",
                        max_length=12,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "currency",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="rates", to="core.currency"
                    ),
                ),
            ],
            options={
                "verbose_name": "cotización histórica",
                "verbose_name_plural": "cotizaciones históricas",
                "ordering": ["currency__code", "date"],
            },
        ),
        migrations.AddConstraint(
            model_name="currencyrate",
            constraint=models.UniqueConstraint(fields=("currency", "date"), name="unique_currency_rate_per_day"),
        ),
        # --- Reloj simulado ---------------------------------------------------
        migrations.CreateModel(
            name="SimulationState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("current_date", models.DateField(default=datetime.date.today)),
                ("days_advanced", models.PositiveIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "estado de simulación",
                "verbose_name_plural": "estado de simulación",
            },
        ),
        # --- Medios de pago (tarjetas de crédito) -----------------------------
        migrations.CreateModel(
            name="PaymentMethod",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "label",
                    models.CharField(
                        help_text="Un alias para reconocer la tarjeta, p. ej. 'Visa personal'.", max_length=40
                    ),
                ),
                ("holder_name", models.CharField(max_length=80, verbose_name="Titular")),
                (
                    "brand",
                    models.CharField(
                        choices=[
                            ("visa", "Visa"),
                            ("mastercard", "Mastercard"),
                            ("amex", "American Express"),
                            ("otra", "Otra"),
                        ],
                        default="otra",
                        max_length=12,
                    ),
                ),
                ("last4", models.CharField(max_length=4)),
                ("expiry_month", models.PositiveSmallIntegerField()),
                ("expiry_year", models.PositiveSmallIntegerField()),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "wallet",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="payment_methods",
                        to="core.wallet",
                    ),
                ),
            ],
            options={
                "verbose_name": "medio de pago",
                "verbose_name_plural": "medios de pago",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddField(
            model_name="wallettransaction",
            name="payment_method",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="transactions",
                to="core.paymentmethod",
            ),
        ),
    ]
