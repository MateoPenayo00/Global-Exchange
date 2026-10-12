"""Conversión entre dos divisas con dos montos vinculados.

Las pantallas de compra (en línea y en caja) tienen dos campos: lo que entrega
el cliente y lo que recibe. El cliente escribe uno y el otro se calcula. El
campo que escribió es el **ancla** (``anchor``): su monto se respeta tal cual y
el otro se redondea siempre a favor de la casa.

La cuenta pasa siempre por guaraníes:

* lo que **entrega** el cliente vale ``amount_in × rate_in`` (precio de
  **compra** de esa divisa; 1 si son guaraníes);
* lo que **recibe** cuesta ``amount_out × rate_out`` (precio de **venta**; 1 si
  son guaraníes).

``static/js/linked-amounts.js`` repite esta misma cuenta en el navegador para
completar el segundo campo mientras se escribe; si se cambia una, hay que
cambiar la otra.
"""

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from ..models import BASE_CURRENCY_CODE, Currency

IN = "in"  # el cliente escribió lo que entrega
OUT = "out"  # el cliente escribió lo que recibe
ANCHORS = (IN, OUT)


@dataclass(frozen=True)
class Conversion:
    amount_in: Decimal
    amount_out: Decimal
    value: Decimal  # valor de la operación en guaraníes


def rate_in(currency: Currency) -> Decimal:
    """Guaraníes que la casa paga por 1 unidad que entrega el cliente."""
    return Decimal("1") if currency.code == BASE_CURRENCY_CODE else currency.buy_rate


def rate_out(currency: Currency) -> Decimal:
    """Guaraníes que la casa cobra por 1 unidad que recibe el cliente."""
    return Decimal("1") if currency.code == BASE_CURRENCY_CODE else currency.sell_rate


def convert(
    *,
    anchor: str,
    amount: Decimal,
    rate_in: Decimal,
    rate_out: Decimal,
    quantum_in: Decimal,
    quantum_out: Decimal,
    value_quantum: Decimal | None = None,
) -> Conversion:
    """Calcula el lado que falta a partir del monto ancla.

    ``value_quantum`` redondea además el valor intermedio en guaraníes (la
    compra en línea cobra guaraníes enteros); la caja no lo usa.
    """
    if anchor == IN:
        amount_in = amount
        value = amount_in * rate_in
        if value_quantum is not None:
            value = value.quantize(value_quantum, rounding=ROUND_DOWN)
        amount_out = (value / rate_out).quantize(quantum_out, rounding=ROUND_DOWN)
    elif anchor == OUT:
        amount_out = amount
        value = amount_out * rate_out
        if value_quantum is not None:
            value = value.quantize(value_quantum, rounding=ROUND_UP)
        amount_in = (value / rate_in).quantize(quantum_in, rounding=ROUND_UP)
    else:
        raise ValueError(f"anchor inválido: {anchor!r}")
    return Conversion(amount_in=amount_in, amount_out=amount_out, value=value)


def rate_table(currencies, *, foreign_decimals: int) -> dict:
    """Cotizaciones para ``linked-amounts.js``, publicadas con ``json_script``.

    Por divisa: código, precio de compra, precio de venta y cuántos decimales
    admite su monto en esa pantalla (el guaraní, ninguno). Los decimales viajan
    como texto para no perder precisión.
    """
    table = {}
    for currency in currencies:
        is_base = currency.code == BASE_CURRENCY_CODE
        table[str(currency.id)] = {
            "code": currency.code,
            "name": currency.name,
            "buy_rate": str(rate_in(currency)),
            "sell_rate": str(rate_out(currency)),
            "decimals": 0 if is_base else foreign_decimals,
        }
    return table
