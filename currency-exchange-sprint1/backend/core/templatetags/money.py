"""Filtros de plantilla para mostrar montos y cotizaciones con formato paraguayo.

Se usa punto como separador de miles y coma como separador decimal, que es la
convención local, en lugar del formato por defecto de Django.
"""

from decimal import Decimal, InvalidOperation

from django import template

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
