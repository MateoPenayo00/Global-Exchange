"""Código QR **simulado** para la pantalla de pago.

Todavía no hay una pasarela de pagos real, así que el QR no codifica nada que
un banco pueda leer: es un dibujo con la forma de un QR (los tres cuadros de
posición y las líneas de sincronización) cuyo relleno sale de un hash de la
referencia de pago. La misma referencia produce siempre el mismo dibujo.

Cuando exista una pasarela, basta con reemplazar ``mock_qr_path()`` por un
generador real y mantener la misma salida: un ``path`` SVG de módulos 1x1.
"""

import hashlib

MODULES = 25  # tamaño de un QR versión 2
FINDER = 7


def _finder_cell(row: int, col: int) -> bool | None:
    """Valor de un módulo dentro de una zona de posición, o ``None`` si está fuera."""
    for top, left in ((0, 0), (0, MODULES - FINDER), (MODULES - FINDER, 0)):
        r, c = row - top, col - left
        if -1 <= r <= FINDER and -1 <= c <= FINDER:
            if r in (-1, FINDER) or c in (-1, FINDER):
                return False  # separador blanco alrededor del cuadro
            ring = min(r, c, FINDER - 1 - r, FINDER - 1 - c)
            return ring != 1  # borde oscuro, anillo claro, centro oscuro
    return None


def _bits(reference: str):
    """Flujo infinito de bits derivado de la referencia."""
    counter = 0
    while True:
        digest = hashlib.sha256(f"{reference}:{counter}".encode()).digest()
        for byte in digest:
            for shift in range(8):
                yield bool((byte >> shift) & 1)
        counter += 1


def mock_qr_modules(reference: str) -> list[list[bool]]:
    bits = _bits(reference)
    grid = []
    for row in range(MODULES):
        line = []
        for col in range(MODULES):
            finder = _finder_cell(row, col)
            if finder is not None:
                line.append(finder)
            elif row == 6 or col == 6:
                line.append((row + col) % 2 == 0)  # líneas de sincronización
            else:
                line.append(next(bits))
        grid.append(line)
    return grid


def mock_qr_path(reference: str) -> dict:
    """Datos para dibujar el QR: ``{"size": 25, "path": "M0 0h1v1h-1z..."}``."""
    grid = mock_qr_modules(reference)
    parts = [f"M{col} {row}h1v1h-1z" for row, line in enumerate(grid) for col, dark in enumerate(line) if dark]
    return {"size": MODULES, "path": "".join(parts)}
