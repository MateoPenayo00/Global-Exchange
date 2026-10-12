"""Pantalla de pago de una compra de divisa.

Comprar es un proceso de dos pasos:

1. ``create_order()`` — el usuario elige qué divisa compra y con cuál paga, y
   escribe cuánto paga o cuánto recibe (el otro monto se completa solo). Se crea
   una ``PurchaseOrder`` pendiente con las cotizaciones **congeladas** en ese
   momento y con un plazo de ``PURCHASE_TIMEOUT`` (5 minutos) para pagarla.
2. ``confirm_order()`` — el usuario elige cómo pagar y confirma. Se usan las
   cotizaciones congeladas aunque la pizarra haya cambiado mientras tanto.

Medios de pago (cada uno es una ``PaymentOption`` con una clave estable):

* ``balance``        — el saldo en guaraníes de la billetera.
* ``currency:<id>``  — otra divisa de la billetera. La casa de cambio se la
  compra al usuario al precio de compra congelado y con esos guaraníes cubre
  la compra; queda registrada como una venta más la compra.
* ``card:<id>``      — una tarjeta registrada (cobro simulado).
* ``qr``             — un código QR de pago (simulado: no se cobra nada real).

Toda operación que mueve saldo bloquea la orden y la billetera con
``select_for_update`` para que dos confirmaciones simultáneas (doble clic, dos
pestañas) no cobren dos veces.
"""

import secrets
from dataclasses import dataclass
from decimal import ROUND_UP, Decimal

from django.db import transaction
from django.utils import timezone

from ..models import (
    BASE_CURRENCY_CODE,
    CURRENCY_QUANTUM,
    PYG_QUANTUM,
    Currency,
    PaymentMethod,
    PurchaseOrder,
    Wallet,
    WalletHolding,
    WalletTransaction,
    quantize_pyg,
)
from ..templatetags.money import guarani, units
from . import pricing

BALANCE_KEY = "balance"
QR_KEY = "qr"
CURRENCY_KEY_PREFIX = "currency:"
CARD_KEY_PREFIX = "card:"


class CheckoutError(Exception):
    """La compra no pudo crearse o confirmarse; el mensaje es apto para el usuario."""


@dataclass
class PaymentOption:
    """Un medio de pago ofrecido en la pantalla de pago."""

    key: str
    kind: str
    title: str
    detail: str
    available: bool = True
    reason: str = ""
    card: PaymentMethod | None = None
    currency: Currency | None = None
    amount: Decimal | None = None  # cuánto se debita, en la divisa del medio de pago
    rate: Decimal | None = None  # cotización congelada usada para la conversión


# Mismo formato paraguayo que los filtros de plantilla (punto de miles, coma decimal).
_gs = guarani
_units = units


# --- Crear la orden -----------------------------------------------------


def create_order(
    wallet: Wallet,
    currency: Currency,
    amount: Decimal,
    *,
    pay_currency: Currency | None = None,
    anchor: str = pricing.IN,
) -> PurchaseOrder:
    """Abre una orden pendiente con las cotizaciones de este instante.

    ``amount`` es lo que paga el usuario en ``pay_currency`` (``anchor="in"``,
    guaraníes si no se indica divisa) o lo que quiere recibir de ``currency``
    (``anchor="out"``). La orden siempre se cobra en guaraníes enteros
    (``pyg_amount``); pagar con otra divisa la convierte después, en la pantalla
    de pago, al precio de compra congelado.

    Una billetera tiene a lo sumo una orden pendiente: abrir otra cancela la
    anterior, para que nunca queden dos precios congelados a la vez.
    """
    pay_currency = pay_currency or Currency.objects.get(code=BASE_CURRENCY_CODE)
    if pay_currency == currency:
        raise CheckoutError("Elegí dos divisas distintas.")
    pays_in_guaranies = pay_currency.code == BASE_CURRENCY_CODE
    if anchor == pricing.IN and pays_in_guaranies and amount != quantize_pyg(amount):
        raise CheckoutError("Los guaraníes se manejan en unidades enteras, sin decimales.")

    rate = currency.sell_rate
    conversion = pricing.convert(
        anchor=anchor,
        amount=amount,
        rate_in=pricing.rate_in(pay_currency),
        rate_out=rate,
        quantum_in=PYG_QUANTUM if pays_in_guaranies else CURRENCY_QUANTUM,
        quantum_out=CURRENCY_QUANTUM,
        value_quantum=PYG_QUANTUM,
    )
    pyg_amount, currency_amount = conversion.value, conversion.amount_out
    if currency_amount <= 0 or pyg_amount <= 0:
        raise CheckoutError(f"El monto es demasiado bajo: 1 {currency.code} cuesta Gs. {_gs(rate)}.")

    frozen_rates = {
        code: str(buy_rate)
        for code, buy_rate in Currency.objects.filter(is_active=True)
        .exclude(code=BASE_CURRENCY_CODE)
        .values_list("code", "buy_rate")
    }

    with transaction.atomic():
        now = timezone.now()
        wallet.purchase_orders.filter(status=PurchaseOrder.PENDING).update(
            status=PurchaseOrder.CANCELLED, closed_at=now
        )
        return PurchaseOrder.objects.create(
            wallet=wallet,
            currency=currency,
            pyg_amount=pyg_amount,
            currency_amount=currency_amount,
            rate=rate,
            frozen_rates=frozen_rates,
            qr_reference=f"GEX-{secrets.token_hex(5).upper()}",
        )


# --- Vencimiento --------------------------------------------------------


def expire_due_orders(wallet: Wallet) -> int:
    """Marca como vencidas las órdenes pendientes de ``wallet`` cuyo plazo pasó.

    No hace falta un proceso en segundo plano: el plazo se comprueba cada vez
    que se mira o se intenta pagar una orden, así que una orden vencida nunca
    llega a cobrarse aunque nadie la haya marcado todavía.
    """
    now = timezone.now()
    return wallet.purchase_orders.filter(status=PurchaseOrder.PENDING, expires_at__lte=now).update(
        status=PurchaseOrder.EXPIRED, closed_at=now
    )


def pending_order(wallet: Wallet) -> PurchaseOrder | None:
    """La orden que el usuario todavía puede pagar, si tiene una."""
    expire_due_orders(wallet)
    return wallet.purchase_orders.filter(status=PurchaseOrder.PENDING).select_related("currency").first()


# --- Medios de pago -----------------------------------------------------


def required_amount(order: PurchaseOrder, rate: Decimal) -> Decimal:
    """Cuánta divisa hay que entregar, a ``rate`` guaraníes, para cubrir la orden.

    Se redondea **hacia arriba** a 6 decimales: si se redondeara hacia abajo la
    casa de cambio recibiría menos guaraníes de los que entrega.
    """
    return (order.pyg_amount / rate).quantize(CURRENCY_QUANTUM, rounding=ROUND_UP)


def payment_options(order: PurchaseOrder, wallet: Wallet) -> list[PaymentOption]:
    """Todos los medios de pago para ``order``, marcando cuáles alcanzan."""
    options = []

    enough = wallet.pyg_balance >= order.pyg_amount
    options.append(
        PaymentOption(
            key=BALANCE_KEY,
            kind=PurchaseOrder.PAY_BALANCE,
            title="Saldo en guaraníes",
            detail=f"Disponible: Gs. {_gs(wallet.pyg_balance)}",
            available=enough,
            reason="" if enough else "Tu saldo en guaraníes no alcanza.",
            amount=order.pyg_amount,
        )
    )

    holdings = (
        wallet.holdings.select_related("currency")
        .filter(amount__gt=0)
        .exclude(currency=order.currency)
        .exclude(currency__code=BASE_CURRENCY_CODE)
    )
    for holding in holdings:
        rate = order.frozen_buy_rate(holding.currency)
        if rate is None or rate <= 0:
            continue  # la divisa no estaba activa al iniciar la compra
        needed = required_amount(order, rate)
        enough = holding.amount >= needed
        options.append(
            PaymentOption(
                key=f"{CURRENCY_KEY_PREFIX}{holding.currency.id}",
                kind=PurchaseOrder.PAY_CURRENCY,
                title=f"{holding.currency.name} de tu billetera",
                detail=(
                    f"Se debitan {_units(needed)} {holding.currency.code} "
                    f"(a Gs. {_gs(rate)} cada uno). Tenés {_units(holding.amount)}."
                ),
                available=enough,
                reason="" if enough else f"No tenés suficiente {holding.currency.code}.",
                currency=holding.currency,
                amount=needed,
                rate=rate,
            )
        )

    for card in wallet.payment_methods.filter(is_active=True):
        expired = card.is_expired()
        options.append(
            PaymentOption(
                key=f"{CARD_KEY_PREFIX}{card.id}",
                kind=PurchaseOrder.PAY_CARD,
                title=f"{card.label} · {card.get_brand_display()}",
                detail=f"{card.masked_number} · vence {card.expiry_display}",
                available=not expired,
                reason="La tarjeta está vencida." if expired else "",
                card=card,
                amount=order.pyg_amount,
            )
        )

    options.append(
        PaymentOption(
            key=QR_KEY,
            kind=PurchaseOrder.PAY_QR,
            title="Código QR",
            detail="Escaneás el código con la app de tu banco y confirmás acá.",
            amount=order.pyg_amount,
        )
    )
    return options


# --- Confirmar y cancelar -----------------------------------------------


def _close(order: PurchaseOrder, status: str) -> None:
    order.status = status
    order.closed_at = timezone.now()
    order.save(update_fields=["status", "closed_at"])


def confirm_order(order_id: int, wallet: Wallet, source_key: str) -> PurchaseOrder:
    """Cobra la orden con el medio ``source_key`` y acredita la divisa comprada."""
    expired = False
    with transaction.atomic():
        order = PurchaseOrder.objects.select_for_update().select_related("currency").get(pk=order_id, wallet=wallet)
        if order.status != PurchaseOrder.PENDING:
            raise CheckoutError(f"Esta compra ya está {order.get_status_display().lower()}.")
        if order.is_past_deadline():
            # Se guarda el vencimiento y se sale del bloque antes de avisar, para
            # que la excepción no deshaga el cambio de estado.
            _close(order, PurchaseOrder.EXPIRED)
            expired = True
        else:
            wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
            option = next((opt for opt in payment_options(order, wallet) if opt.key == source_key), None)
            if option is None:
                raise CheckoutError("Elegí un medio de pago válido.")
            if not option.available:
                raise CheckoutError(option.reason)
            _settle(order, wallet, option)

    if expired:
        raise CheckoutError("Se venció el plazo para pagar esta compra. Iniciá una nueva para ver el precio actual.")
    return order


def _settle(order: PurchaseOrder, wallet: Wallet, option: PaymentOption) -> None:
    """Mueve los saldos de una orden ya validada. Corre dentro de una transacción."""
    tag = f"Compra #{order.pk}"

    if option.kind == PurchaseOrder.PAY_BALANCE:
        wallet.pyg_balance -= order.pyg_amount
        wallet.save(update_fields=["pyg_balance", "updated_at"])
        note = f"{tag} con saldo en guaraníes"
    elif option.kind == PurchaseOrder.PAY_CURRENCY:
        source = wallet.holdings.select_for_update().get(currency=option.currency)
        source.amount -= option.amount
        source.save(update_fields=["amount"])
        # La conversión a guaraníes que ocurre por detrás queda como una venta,
        # para que el historial de la billetera explique por qué bajó esa divisa.
        WalletTransaction.objects.create(
            wallet=wallet,
            kind=WalletTransaction.SELL,
            currency=option.currency,
            amount=option.amount,
            pyg_amount=order.pyg_amount,
            rate_used=option.rate,
            note=f"Conversión para pagar la compra #{order.pk}",
        )
        note = f"{tag} pagada con {_units(option.amount)} {option.currency.code}"
    elif option.kind == PurchaseOrder.PAY_CARD:
        note = f"{tag} con {option.card.masked_number}"
    else:
        note = f"{tag} con QR {order.qr_reference}"

    holding, _ = WalletHolding.objects.select_for_update().get_or_create(wallet=wallet, currency=order.currency)
    holding.amount += order.currency_amount
    holding.save(update_fields=["amount"])
    WalletTransaction.objects.create(
        wallet=wallet,
        kind=WalletTransaction.BUY,
        currency=order.currency,
        payment_method=option.card,
        amount=order.currency_amount,
        pyg_amount=order.pyg_amount,
        rate_used=order.rate,
        note=note,
    )

    order.status = PurchaseOrder.COMPLETED
    order.payment_kind = option.kind
    order.payment_method = option.card
    order.paid_currency = option.currency
    order.paid_amount = option.amount
    order.closed_at = timezone.now()
    order.save(
        update_fields=["status", "payment_kind", "payment_method", "paid_currency", "paid_amount", "closed_at"]
    )


def cancel_order(order_id: int, wallet: Wallet) -> PurchaseOrder:
    """Cancela una orden pendiente. Si ya había vencido, queda como vencida."""
    with transaction.atomic():
        order = PurchaseOrder.objects.select_for_update().get(pk=order_id, wallet=wallet)
        if order.status != PurchaseOrder.PENDING:
            raise CheckoutError(f"Esta compra ya está {order.get_status_display().lower()}.")
        _close(order, PurchaseOrder.EXPIRED if order.is_past_deadline() else PurchaseOrder.CANCELLED)
    return order
