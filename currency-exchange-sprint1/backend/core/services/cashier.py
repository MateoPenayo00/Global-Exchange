"""Caja física: sesiones de cajero y operaciones de ventanilla.

Flujo de un turno:

1. ``open_session()`` — el cajero declara el efectivo inicial de cada divisa.
2. ``quote()`` — por cada cliente, el cajero indica qué divisa entrega el
   cliente y cuál recibe, y escribe el monto de uno de los dos lados (el otro se
   completa solo, ver ``services.pricing``). Se crea una
   ``CashTransaction`` pendiente con las cotizaciones congeladas y se abre la
   pestaña de la operación.
3. ``confirm()`` — en esa pestaña el cajero elige el medio de pago (efectivo,
   tarjeta o QR) y carga nombre y cédula del cliente. Al confirmar se mueve el
   efectivo de la sesión: entra lo que paga el cliente si paga en efectivo, y
   siempre sale lo que recibe.
4. ``close_session()`` — guarda el saldo final de cada divisa; ``session_report()``
   arma el reporte con el estado inicial, el final y todas las operaciones.

Igual que en ``services.checkout``, todo lo que mueve saldos bloquea la operación
y los saldos de la sesión con ``select_for_update``.
"""

import secrets
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import (
    BASE_CURRENCY_CODE,
    CashSession,
    CashSessionBalance,
    CashTransaction,
    Currency,
    cash_quantum,
)
from ..templatetags.money import guarani, units
from . import pricing

PYG_VALUE_QUANTUM = Decimal("0.01")


class CashierError(Exception):
    """La operación de caja no pudo realizarse; el mensaje es apto para el cajero."""


def _money(amount: Decimal, currency: Currency) -> str:
    if currency.code == BASE_CURRENCY_CODE:
        return f"Gs. {guarani(amount)}"
    return f"{units(amount)} {currency.code}"


def check_cash_amount(amount: Decimal, currency: Currency) -> Decimal:
    """Valida que ``amount`` se pueda entregar en efectivo y lo devuelve normalizado."""
    quantum = cash_quantum(currency)
    if amount != amount.quantize(quantum):
        if currency.code == BASE_CURRENCY_CODE:
            raise CashierError("Los guaraníes se manejan en unidades enteras, sin decimales.")
        raise CashierError(f"{currency.code} admite como mucho dos decimales (centavos).")
    return amount.quantize(quantum)


# --- Sesión ---------------------------------------------------------------


def current_session(user) -> CashSession | None:
    return CashSession.objects.filter(cashier=user, status=CashSession.OPEN).first()


def open_session(user, opening: dict[Currency, Decimal]) -> CashSession:
    """Abre una sesión con el efectivo inicial ``opening`` (divisa -> monto)."""
    if current_session(user) is not None:
        raise CashierError("Ya tenés una sesión de caja abierta. Cerrala antes de abrir otra.")
    if not any(amount > 0 for amount in opening.values()):
        raise CashierError("Declará el efectivo inicial de al menos una divisa.")

    name = user.get_full_name() or user.get_username()
    try:
        with transaction.atomic():
            session = CashSession.objects.create(cashier=user, cashier_name=name)
            CashSessionBalance.objects.bulk_create(
                CashSessionBalance(
                    session=session,
                    currency=currency,
                    opening_amount=check_cash_amount(amount, currency),
                    current_amount=check_cash_amount(amount, currency),
                )
                for currency, amount in opening.items()
            )
    except IntegrityError:
        # Dos aperturas simultáneas: la restricción de una sesión abierta por
        # cajero frena la segunda.
        raise CashierError("Ya tenés una sesión de caja abierta. Cerrala antes de abrir otra.")
    return session


def close_session(session: CashSession) -> CashSession:
    """Cierra la sesión: cancela lo pendiente y congela los saldos finales."""
    with transaction.atomic():
        session = CashSession.objects.select_for_update().get(pk=session.pk)
        if not session.is_open:
            raise CashierError("Esta sesión de caja ya está cerrada.")
        now = timezone.now()
        session.transactions.filter(status=CashTransaction.PENDING).update(
            status=CashTransaction.CANCELLED, closed_at=now
        )
        for balance in session.balances.select_for_update():
            balance.closing_amount = balance.current_amount
            balance.save(update_fields=["closing_amount"])
        session.status = CashSession.CLOSED
        session.closed_at = now
        session.save(update_fields=["status", "closed_at"])
    return session


# --- Operaciones ----------------------------------------------------------


def expire_due(session: CashSession) -> int:
    """Marca como vencidas las operaciones pendientes cuyo plazo pasó."""
    now = timezone.now()
    return session.transactions.filter(status=CashTransaction.PENDING, expires_at__lte=now).update(
        status=CashTransaction.EXPIRED, closed_at=now
    )


def pending_transaction(session: CashSession) -> CashTransaction | None:
    expire_due(session)
    return session.transactions.filter(status=CashTransaction.PENDING).first()


def available_cash(session: CashSession, currency: Currency) -> Decimal:
    balance = session.balances.filter(currency=currency).first()
    return balance.current_amount if balance else Decimal("0")


def quote(
    session: CashSession,
    currency_in: Currency,
    currency_out: Currency,
    amount: Decimal,
    anchor: str = pricing.OUT,
) -> CashTransaction:
    """Cotiza una operación con las cotizaciones de este instante y la deja pendiente.

    ``amount`` es lo que entrega el cliente (``anchor="in"``) o lo que recibe
    (``anchor="out"``). El otro lado se calcula a favor de la casa: lo que recibe
    el cliente se redondea hacia abajo y lo que entrega, hacia arriba, cada uno a
    la unidad de efectivo de su divisa.
    """
    if not session.is_open:
        raise CashierError("La sesión de caja está cerrada.")
    if currency_in == currency_out:
        raise CashierError("Elegí dos divisas distintas.")
    if amount <= 0:
        raise CashierError("El monto debe ser mayor que cero.")

    rate_in, rate_out = pricing.rate_in(currency_in), pricing.rate_out(currency_out)
    amount = check_cash_amount(amount, currency_in if anchor == pricing.IN else currency_out)
    conversion = pricing.convert(
        anchor=anchor,
        amount=amount,
        rate_in=rate_in,
        rate_out=rate_out,
        quantum_in=cash_quantum(currency_in),
        quantum_out=cash_quantum(currency_out),
    )
    amount_in, amount_out = conversion.amount_in, conversion.amount_out
    if amount_out <= 0:
        raise CashierError(
            f"El monto es demasiado bajo: no alcanza para entregar ni {_money(cash_quantum(currency_out), currency_out)}."
        )

    cash = available_cash(session, currency_out)
    if cash < amount_out:
        raise CashierError(
            f"No hay suficiente efectivo en caja: hay {_money(cash, currency_out)} "
            f"y la operación entrega {_money(amount_out, currency_out)}."
        )

    with transaction.atomic():
        now = timezone.now()
        # Una operación por vez en ventanilla: cotizar otra descarta la anterior.
        session.transactions.filter(status=CashTransaction.PENDING).update(
            status=CashTransaction.CANCELLED, closed_at=now
        )
        return CashTransaction.objects.create(
            session=session,
            currency_in=currency_in,
            amount_in=amount_in,
            rate_in=rate_in,
            currency_out=currency_out,
            amount_out=amount_out,
            rate_out=rate_out,
            pyg_value=(amount_in * rate_in).quantize(PYG_VALUE_QUANTUM),
            qr_reference=f"GEX-CAJA-{secrets.token_hex(4).upper()}",
        )


def confirm(
    tx_id: int,
    session: CashSession,
    *,
    payment_method: str,
    customer_name: str,
    customer_document: str,
    card_brand: str = "",
    card_holder: str = "",
    card_last4: str = "",
) -> CashTransaction:
    """Ejecuta la operación con las cotizaciones congeladas y mueve el efectivo.

    De una tarjeta sólo llegan acá marca, titular y últimos cuatro dígitos: el
    número completo y el código de seguridad se validan en el formulario y se
    descartan, nunca se guardan.
    """
    expired = False
    with transaction.atomic():
        tx = (
            CashTransaction.objects.select_for_update()
            .select_related("currency_in", "currency_out")
            .get(pk=tx_id, session=session)
        )
        if not tx.is_pending:
            raise CashierError(f"Esta operación ya está {tx.get_status_display().lower()}.")
        if tx.is_past_deadline():
            tx.status = CashTransaction.EXPIRED
            tx.closed_at = timezone.now()
            tx.save(update_fields=["status", "closed_at"])
            expired = True
        else:
            locked_session = CashSession.objects.select_for_update().get(pk=session.pk)
            if not locked_session.is_open:
                raise CashierError("La sesión de caja está cerrada.")
            if payment_method not in dict(CashTransaction.PAYMENT_METHOD_CHOICES):
                raise CashierError("Elegí un medio de pago válido.")

            out_balance = locked_session.balances.select_for_update().filter(currency=tx.currency_out).first()
            cash = out_balance.current_amount if out_balance else Decimal("0")
            if cash < tx.amount_out:
                raise CashierError(
                    f"No hay suficiente efectivo en caja: hay {_money(cash, tx.currency_out)} "
                    f"y la operación entrega {_money(tx.amount_out, tx.currency_out)}."
                )

            out_balance.current_amount -= tx.amount_out
            out_balance.save(update_fields=["current_amount"])
            if payment_method == CashTransaction.PAY_CASH:
                in_balance, _ = CashSessionBalance.objects.select_for_update().get_or_create(
                    session=locked_session, currency=tx.currency_in
                )
                in_balance.current_amount += tx.amount_in
                in_balance.save(update_fields=["current_amount"])

            tx.status = CashTransaction.COMPLETED
            tx.payment_method = payment_method
            is_card = payment_method == CashTransaction.PAY_CARD
            tx.card_brand = card_brand if is_card else ""
            tx.card_holder = card_holder if is_card else ""
            tx.card_last4 = card_last4 if is_card else ""
            tx.customer_name = customer_name
            tx.customer_document = customer_document
            tx.closed_at = timezone.now()
            tx.save(
                update_fields=[
                    "status",
                    "payment_method",
                    "card_brand",
                    "card_holder",
                    "card_last4",
                    "customer_name",
                    "customer_document",
                    "closed_at",
                ]
            )

    if expired:
        raise CashierError("Se venció el plazo de la cotización. Cotizá de nuevo con el precio actual.")
    return tx


def cancel(tx_id: int, session: CashSession) -> CashTransaction:
    with transaction.atomic():
        tx = CashTransaction.objects.select_for_update().get(pk=tx_id, session=session)
        if not tx.is_pending:
            raise CashierError(f"Esta operación ya está {tx.get_status_display().lower()}.")
        tx.status = CashTransaction.EXPIRED if tx.is_past_deadline() else CashTransaction.CANCELLED
        tx.closed_at = timezone.now()
        tx.save(update_fields=["status", "closed_at"])
    return tx


# --- Reporte --------------------------------------------------------------


@dataclass
class CurrencySummary:
    """Movimiento de una divisa durante la sesión."""

    currency: Currency
    opening: Decimal
    cash_in: Decimal = Decimal("0")
    cash_out: Decimal = Decimal("0")
    card_in: Decimal = Decimal("0")
    qr_in: Decimal = Decimal("0")
    closing: Decimal = Decimal("0")

    @property
    def expected(self) -> Decimal:
        """Lo que debería haber según apertura y movimientos (control de coherencia)."""
        return self.opening + self.cash_in - self.cash_out

    @property
    def net(self) -> Decimal:
        return self.closing - self.opening


@dataclass
class SessionReport:
    session: CashSession
    currencies: list[CurrencySummary] = field(default_factory=list)
    transactions: list[CashTransaction] = field(default_factory=list)
    discarded: int = 0  # cotizaciones canceladas o vencidas

    @property
    def has_non_cash(self) -> bool:
        return any(row.card_in or row.qr_in for row in self.currencies)

    @property
    def total_pyg_value(self) -> Decimal:
        return sum((tx.pyg_value for tx in self.transactions), Decimal("0"))


def session_report(session: CashSession) -> SessionReport:
    """Estado inicial, estado final y operaciones de la sesión.

    Con la sesión abierta, el "final" es el saldo actual: así el cajero puede
    revisar el reporte antes de cerrar.
    """
    summaries = {
        balance.currency_id: CurrencySummary(
            currency=balance.currency,
            opening=balance.opening_amount,
            closing=balance.closing_amount if balance.closing_amount is not None else balance.current_amount,
        )
        for balance in session.balances.select_related("currency").order_by("currency__code")
    }
    done = list(
        session.transactions.filter(status=CashTransaction.COMPLETED)
        .select_related("currency_in", "currency_out")
        .order_by("closed_at")
    )

    def summary_for(currency):
        # Una divisa sin saldo en la sesión (p. ej. pagada con tarjeta y activada
        # después de abrir la caja) igual figura en el reporte, desde cero.
        if currency.id not in summaries:
            summaries[currency.id] = CurrencySummary(currency=currency, opening=Decimal("0"))
        return summaries[currency.id]

    for tx in done:
        incoming = summary_for(tx.currency_in)
        if tx.payment_method == CashTransaction.PAY_CASH:
            incoming.cash_in += tx.amount_in
        elif tx.payment_method == CashTransaction.PAY_CARD:
            incoming.card_in += tx.amount_in
        else:
            incoming.qr_in += tx.amount_in
        summary_for(tx.currency_out).cash_out += tx.amount_out

    # El guaraní primero: es la divisa base y la que más se mueve.
    ordered = sorted(summaries.values(), key=lambda s: (s.currency.code != BASE_CURRENCY_CODE, s.currency.code))
    discarded = session.transactions.filter(
        status__in=[CashTransaction.CANCELLED, CashTransaction.EXPIRED]
    ).count()
    return SessionReport(session=session, currencies=ordered, transactions=done, discarded=discarded)
