"""Construcción del gráfico de líneas del historial de cotizaciones.

El gráfico se dibuja como un SVG en línea generado por la plantilla, así que acá
sólo se calculan las coordenadas. De esta forma el historial se ve igual sin
depender de ninguna librería de JavaScript externa.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

# Lienzo del SVG (coordenadas internas; el SVG se escala al ancho disponible).
WIDTH = 760
HEIGHT = 300
PADDING_LEFT = 78
PADDING_RIGHT = 16
PADDING_TOP = 16
PADDING_BOTTOM = 42

PLOT_WIDTH = WIDTH - PADDING_LEFT - PADDING_RIGHT
PLOT_HEIGHT = HEIGHT - PADDING_TOP - PADDING_BOTTOM

# Cuántas etiquetas como máximo se dibujan en cada eje.
MAX_X_LABELS = 7
Y_TICKS = 5


@dataclass
class AxisLabel:
    position: float
    text: str


@dataclass
class RateChart:
    """Todo lo que la plantilla necesita para dibujar el SVG."""

    width: int = WIDTH
    height: int = HEIGHT
    plot_left: int = PADDING_LEFT
    plot_top: int = PADDING_TOP
    plot_right: int = WIDTH - PADDING_RIGHT
    plot_bottom: int = HEIGHT - PADDING_BOTTOM
    buy_points: str = ""
    sell_points: str = ""
    x_labels: list = field(default_factory=list)
    y_labels: list = field(default_factory=list)
    min_value: Decimal = Decimal("0")
    max_value: Decimal = Decimal("0")
    has_data: bool = False


def _format_rate(value: Decimal) -> str:
    """Formatea una cotización de forma legible, con separador de miles en punto."""
    value = Decimal(value)
    if value >= 100:
        text = f"{value:,.0f}"
    elif value >= 1:
        text = f"{value:,.2f}"
    else:
        text = f"{value:,.4f}"
    # Se usa el formato local: punto para miles, coma para decimales.
    return text.replace(",", " ").replace(".", ",").replace(" ", ".")


def build_rate_chart(rates) -> RateChart:
    """Convierte una lista de ``CurrencyRate`` (ordenada por fecha) en un ``RateChart``."""
    chart = RateChart()
    rates = list(rates)
    if not rates:
        return chart

    buys = [Decimal(rate.buy_rate) for rate in rates]
    sells = [Decimal(rate.sell_rate) for rate in rates]
    low, high = min(buys), max(sells)
    if high == low:
        # Una serie plana necesita un margen artificial para no dividir por cero.
        margin = high * Decimal("0.05") if high else Decimal("1")
        low, high = low - margin, high + margin
    else:
        margin = (high - low) * Decimal("0.08")
        low, high = low - margin, high + margin

    span = high - low
    last_index = max(len(rates) - 1, 1)

    def x_for(index: int) -> float:
        return round(PADDING_LEFT + (PLOT_WIDTH * index / last_index), 2)

    def y_for(value: Decimal) -> float:
        ratio = (Decimal(value) - low) / span
        return round(PADDING_TOP + PLOT_HEIGHT - (Decimal(PLOT_HEIGHT) * ratio), 2)

    chart.buy_points = " ".join(f"{x_for(i)},{y_for(value)}" for i, value in enumerate(buys))
    chart.sell_points = " ".join(f"{x_for(i)},{y_for(value)}" for i, value in enumerate(sells))

    # Etiquetas del eje horizontal: fechas espaciadas de forma regular.
    step = max(1, (len(rates) + MAX_X_LABELS - 1) // MAX_X_LABELS)
    indexes = list(range(0, len(rates), step))
    if indexes[-1] != len(rates) - 1:
        indexes.append(len(rates) - 1)
    chart.x_labels = [
        AxisLabel(position=x_for(i), text=rates[i].date.strftime("%d/%m")) for i in indexes
    ]

    # Etiquetas del eje vertical: valores repartidos entre el mínimo y el máximo.
    chart.y_labels = [
        AxisLabel(
            position=y_for(low + span * Decimal(tick) / Decimal(Y_TICKS)),
            text=_format_rate(low + span * Decimal(tick) / Decimal(Y_TICKS)),
        )
        for tick in range(Y_TICKS + 1)
    ]

    chart.min_value = min(buys)
    chart.max_value = max(sells)
    chart.has_data = True
    return chart
