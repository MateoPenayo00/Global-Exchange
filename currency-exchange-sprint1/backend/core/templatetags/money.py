"""Filtros de plantilla para mostrar montos y cotizaciones con formato paraguayo.

Se usa punto como separador de miles y coma como separador decimal, que es la
convención local, en lugar del formato por defecto de Django.
"""

from decimal import Decimal, InvalidOperation

from django import template

from core.models import BASE_CURRENCY_CODE

register = template.Library()


def _to_decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _format(value: Decimal, decimals: int) -> str:
    text = f"{value:,.{decimals}f}"
    # ',' (miles) y '.' (decimales) se intercambian pasando por un separador temporal.
    return text.replace(",", " ").replace(".", ",").replace(" ", ".")


@register.filter(name="gs")
def guarani(value):
    """Formatea un monto en guaraníes sin decimales: ``7300000`` -> ``7.300.000``."""
    number = _to_decimal(value)
    if number is None:
        return value
    return _format(number.quantize(Decimal("1")), 0)


@register.filter(name="rate")
def rate(value):
    """Formatea una cotización con la precisión justa para que se lea bien."""
    number = _to_decimal(value)
    if number is None:
        return value
    if abs(number) >= 100:
        return _format(number, 0)
    if abs(number) >= 1:
        return _format(number, 2)
    return _format(number, 4)


@register.filter(name="units")
def units(value):
    """Formatea una cantidad de divisa quitando los ceros decimales sobrantes."""
    number = _to_decimal(value)
    if number is None:
        return value
    normalized = number.normalize()
    decimals = max(-normalized.as_tuple().exponent, 2)
    return _format(number, min(decimals, 6))


@register.filter(name="countdown")
def countdown(value):
    """Formatea segundos como un reloj de minutos: ``299`` -> ``4:59``."""
    try:
        seconds = max(0, int(value))
    except (TypeError, ValueError):
        return value
    return f"{seconds // 60}:{seconds % 60:02d}"


@register.filter(name="cash")
def cash(value, currency):
    """Monto de efectivo con su divisa: ``Gs. 1.500.000`` o ``250,00 USD``."""
    number = _to_decimal(value)
    if number is None:
        return value
    if getattr(currency, "code", currency) == BASE_CURRENCY_CODE:
        return f"Gs. {guarani(number)}"
    return f"{_format(number, 2)} {getattr(currency, 'code', currency)}"


@register.filter(name="ci")
def cedula(value):
    """Cédula de identidad con puntos de miles: ``4567890`` -> ``4.567.890``."""
    text = str(value or "")
    if not text.isdigit():
        return text
    return _format(Decimal(text), 0)


@register.filter(name="sub")
def subtract(value, other):
    """Resta dos montos decimales: ``{{ actual|sub:inicial }}``."""
    a, b = _to_decimal(value), _to_decimal(other)
    if a is None or b is None:
        return ""
    return a - b
