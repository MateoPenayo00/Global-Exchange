"""Sprint 3 — datos: convierte lo que existía a guaraníes y carga datos de demo.

Qué hace, en orden:

1. Convierte las divisas que ya existían: el valor antiguo estaba expresado en
   dólares por unidad, así que se multiplica por una cotización de referencia
   (``USD_PYG_REFERENCE``) para pasarlo a guaraníes y se le aplica un *spread*
   para obtener precio de compra y de venta.
2. Convierte los saldos y movimientos de las billeteras existentes a guaraníes
   con la misma referencia, para que los datos viejos sigan siendo coherentes.
3. Crea el guaraní (PYG) como divisa base, con valor fijo 1.
4. Siembra un conjunto de divisas de demostración si todavía no existen.
5. Genera 30 días de historial de cotizaciones hacia atrás para cada divisa no
   base, de forma determinista, para que la pestaña *Cotizaciones* tenga datos
   y el gráfico se vea apenas se levanta el proyecto.
6. Crea la fila única del reloj de simulación.
"""

import random
from datetime import date, timedelta
from decimal import Decimal

from django.db import migrations

# Cotización de referencia usada únicamente para convertir los datos del Sprint 2
# (que estaban en dólares) a guaraníes.
USD_PYG_REFERENCE = Decimal("7300")

# Margen de la casa de cambio aplicado a cada lado de la cotización media.
HALF_SPREAD = Decimal("0.015")

# Divisas de demostración: código, nombre, símbolo y cotización media en guaraníes.
DEMO_CURRENCIES = [
    ("USD", "Dólar estadounidense", "US$", Decimal("7300")),
    ("EUR", "Euro", "€", Decimal("8050")),
    ("BRL", "Real brasileño", "R$", Decimal("1340")),
    ("ARS", "Peso argentino", "AR$", Decimal("6.50")),
]

HISTORY_DAYS = 30
RANDOM_SEED = 20260911


def _rates_from_mid(mid: Decimal):
    """Reparte una cotización media en precio de compra (menor) y de venta (mayor)."""
    buy = (mid * (Decimal("1") - HALF_SPREAD)).quantize(Decimal("0.000001"))
    sell = (mid * (Decimal("1") + HALF_SPREAD)).quantize(Decimal("0.000001"))
    return buy, sell


def forwards(apps, schema_editor):
    Currency = apps.get_model("core", "Currency")
    CurrencyRate = apps.get_model("core", "CurrencyRate")
    SimulationState = apps.get_model("core", "SimulationState")
    Wallet = apps.get_model("core", "Wallet")
    WalletTransaction = apps.get_model("core", "WalletTransaction")

    # 1) Las divisas que ya existían tenían su valor en dólares -> pasarlo a guaraníes.
    for currency in Currency.objects.exclude(code="PYG"):
        mid = (currency.buy_rate * USD_PYG_REFERENCE).quantize(Decimal("0.000001"))
        currency.buy_rate, currency.sell_rate = _rates_from_mid(mid)
        currency.save(update_fields=["buy_rate", "sell_rate"])

    # 2) Saldos y movimientos previos: estaban en dólares, se reexpresan en guaraníes.
    for wallet in Wallet.objects.all():
        if wallet.pyg_balance:
            wallet.pyg_balance = (wallet.pyg_balance * USD_PYG_REFERENCE).quantize(Decimal("0.01"))
            wallet.save(update_fields=["pyg_balance"])
    for tx in WalletTransaction.objects.all():
        new_amount = (tx.pyg_amount * USD_PYG_REFERENCE).quantize(Decimal("0.01"))
        new_rate = (tx.rate_used * USD_PYG_REFERENCE).quantize(Decimal("0.000001")) if tx.rate_used else None
        if tx.kind == "deposit":
            # Una carga de saldo mueve guaraníes: su "amount" también se reexpresa.
            tx.amount = new_amount
        tx.pyg_amount = new_amount
        tx.rate_used = new_rate
        tx.save(update_fields=["amount", "pyg_amount", "rate_used"])

    # 3) El guaraní pasa a ser la divisa base.
    Currency.objects.update_or_create(
        code="PYG",
        defaults={
            "name": "Guaraní paraguayo",
            "symbol": "Gs.",
            "buy_rate": Decimal("1.000000"),
            "sell_rate": Decimal("1.000000"),
            "is_active": True,
        },
    )

    # 4) Divisas de demostración que falten.
    for code, name, symbol, mid in DEMO_CURRENCIES:
        buy, sell = _rates_from_mid(mid)
        Currency.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "symbol": symbol,
                "buy_rate": buy,
                "sell_rate": sell,
                "is_active": True,
            },
        )

    # 5) Historial sintético hacia atrás: el último punto coincide con la cotización actual.
    today = date.today()
    rng = random.Random(RANDOM_SEED)
    for currency in Currency.objects.exclude(code="PYG").order_by("code"):
        buy = currency.buy_rate
        sell = currency.sell_rate
        points = [(today, buy, sell, "inicial")]
        for offset in range(1, HISTORY_DAYS):
            # Se camina hacia atrás deshaciendo una variación diaria aleatoria.
            factor = Decimal("1") + Decimal(str(round(rng.uniform(-0.05, 0.07), 6)))
            buy = (buy / factor).quantize(Decimal("0.000001"))
            sell = (sell / factor).quantize(Decimal("0.000001"))
            points.append((today - timedelta(days=offset), buy, sell, "inicial"))

        CurrencyRate.objects.bulk_create(
            [
                CurrencyRate(currency=currency, date=day, buy_rate=b, sell_rate=s, source=source)
                for day, b, s, source in reversed(points)
            ],
            ignore_conflicts=True,
        )

    # 6) El reloj simulado arranca hoy.
    if not SimulationState.objects.exists():
        SimulationState.objects.create(current_date=today, days_advanced=0)


def backwards(apps, schema_editor):
    """Deshace los datos de demostración (el esquema lo revierte 0003)."""
    apps.get_model("core", "CurrencyRate").objects.all().delete()
    apps.get_model("core", "SimulationState").objects.all().delete()
    apps.get_model("core", "Currency").objects.filter(code="PYG").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_guarani_base_rates_payments_history"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
