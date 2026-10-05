"""Simulación del paso del tiempo para ver cómo se mueven las cotizaciones.

Un administrador adelanta el reloj del sistema un día desde la pestaña
*Simulación*. Cada día simulado, toda divisa activa distinta del guaraní cambia
de valor un porcentaje aleatorio dentro del rango ``[-5 %, +7 %]`` y queda
registrado un punto nuevo en el historial.

El mismo porcentaje se aplica al precio de compra y al de venta, de modo que el
margen relativo de la casa de cambio se mantiene y el precio de compra sigue
siendo siempre menor que el de venta.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db import transaction

from core.models import BASE_CURRENCY_CODE, Currency, CurrencyRate, SimulationState

# Variación diaria posible de una divisa, en tanto por uno.
MIN_DAILY_CHANGE = Decimal("-0.05")
MAX_DAILY_CHANGE = Decimal("0.07")

RATE_QUANTUM = Decimal("0.000001")

# Ninguna cotización puede caer por debajo de este piso, para que la simulación no
# lleve una divisa a cero tras muchos días a la baja.
MIN_RATE = Decimal("0.000100")


@dataclass
class CurrencyChange:
    """Resultado de la simulación para una divisa en un día."""

    currency: Currency
    previous_buy: Decimal
    previous_sell: Decimal
    new_buy: Decimal
    new_sell: Decimal
    change_percent: Decimal

    @property
    def went_up(self) -> bool:
        return self.change_percent > 0


def random_daily_factor(rng: random.Random | None = None) -> Decimal:
    """Devuelve el multiplicador de un día: entre 0,95 y 1,07."""
    generator = rng or random
    raw = generator.uniform(float(MIN_DAILY_CHANGE), float(MAX_DAILY_CHANGE))
    return Decimal("1") + Decimal(str(round(raw, 6)))


def _apply(value: Decimal, factor: Decimal) -> Decimal:
    return max((value * factor).quantize(RATE_QUANTUM), MIN_RATE)


@transaction.atomic
def advance_days(days: int, rng: random.Random | None = None):
    """Adelanta varios días seguidos y devuelve el cambio **neto** de cada divisa.

    Existe para que el administrador pueda avanzar una semana con un clic en lugar
    de siete. El resumen compara la cotización antes del primer día con la de
    después del último, que es lo que interesa ver; los días intermedios quedan
    igualmente registrados en el historial, uno por uno.
    """
    if days < 1:
        raise ValueError("Hay que avanzar al menos un día.")

    tracked = Currency.objects.filter(is_active=True).exclude(code=BASE_CURRENCY_CODE)
    before = {currency.id: (currency.buy_rate, currency.sell_rate) for currency in tracked}

    state = None
    for _ in range(days):
        state, _ = advance_one_day(rng=rng)

    changes: list[CurrencyChange] = []
    for currency in tracked.order_by("code"):
        previous = before.get(currency.id)
        if previous is None:
            continue  # divisa creada en el medio: no hay con qué comparar.
        previous_buy, previous_sell = previous
        percent = (
            ((currency.buy_rate - previous_buy) / previous_buy * Decimal("100")).quantize(Decimal("0.01"))
            if previous_buy
            else Decimal("0.00")
        )
        changes.append(
            CurrencyChange(
                currency=currency,
                previous_buy=previous_buy,
                previous_sell=previous_sell,
                new_buy=currency.buy_rate,
                new_sell=currency.sell_rate,
                change_percent=percent,
            )
        )
    return state, changes


@transaction.atomic
def advance_one_day(rng: random.Random | None = None) -> tuple[SimulationState, list[CurrencyChange]]:
    """Adelanta el reloj simulado un día y recotiza todas las divisas activas.

    Devuelve el estado de simulación actualizado y la lista de cambios aplicados,
    para que la vista pueda mostrarle al administrador qué pasó.
    """
    state = SimulationState.objects.select_for_update().first()
    if state is None:
        state = SimulationState.objects.create()

    state.current_date = state.current_date + timedelta(days=1)
    state.days_advanced += 1
    state.save(update_fields=["current_date", "days_advanced", "updated_at"])

    changes: list[CurrencyChange] = []
    currencies = Currency.objects.filter(is_active=True).exclude(code=BASE_CURRENCY_CODE).order_by("code")
    for currency in currencies:
        factor = random_daily_factor(rng)
        previous_buy, previous_sell = currency.buy_rate, currency.sell_rate
        currency.buy_rate = _apply(previous_buy, factor)
        currency.sell_rate = _apply(previous_sell, factor)
        currency.save(update_fields=["buy_rate", "sell_rate", "updated_at"])

        CurrencyRate.objects.update_or_create(
            currency=currency,
            date=state.current_date,
            defaults={
                "buy_rate": currency.buy_rate,
                "sell_rate": currency.sell_rate,
                "source": CurrencyRate.SIMULATION,
            },
        )

        changes.append(
            CurrencyChange(
                currency=currency,
                previous_buy=previous_buy,
                previous_sell=previous_sell,
                new_buy=currency.buy_rate,
                new_sell=currency.sell_rate,
                change_percent=((factor - Decimal("1")) * Decimal("100")).quantize(Decimal("0.01")),
            )
        )

    return state, changes
