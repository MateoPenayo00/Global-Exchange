"""Integración con Stripe Checkout para pagar una compra con tarjeta.

Flujo:

1. ``create_checkout_session()`` — el usuario elige "Tarjeta (Stripe)" y se le
   crea una sesión de Checkout por el monto en guaraníes de la orden. Se lo
   redirige a la página de pago de Stripe.
2. El usuario paga en Stripe. El navegador vuelve a la pantalla de pago, pero
   **eso no confirma nada**: el cobro lo confirma Stripe llamando al webhook.
3. ``handle_event()`` — procesa ``checkout.session.completed``: acredita la
   divisa con ``checkout.settle_stripe_payment()``. Es idempotente: cada
   ``event.id`` se guarda en ``StripeEvent`` y un reenvío se ignora.

Si el pago llega cuando la orden ya no se puede cobrar (vencida, cancelada o ya
pagada por otro medio), se reembolsa automáticamente para no quedarse con plata
sin entregar la divisa.
"""

import logging
from datetime import timedelta

import stripe
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import PurchaseOrder, StripeEvent, Wallet
from . import checkout

logger = logging.getLogger(__name__)

# El usuario puede tardar unos segundos en la página de Stripe y el webhook en
# llegar: se tolera un pequeño margen pasado el plazo de la orden.
PAYMENT_GRACE = timedelta(minutes=2)

HANDLED_EVENT = "checkout.session.completed"


class StripeError(Exception):
    """Falló la comunicación con Stripe; el mensaje es apto para el usuario."""


class InvalidWebhook(Exception):
    """El webhook no trae una firma válida de Stripe."""


def is_enabled() -> bool:
    return bool(settings.STRIPE_SECRET_KEY)


# --- Crear la sesión de pago --------------------------------------------


def create_checkout_session(order: PurchaseOrder, *, success_url: str, cancel_url: str) -> str:
    """Crea la sesión de Stripe para ``order`` y devuelve la URL donde se paga."""
    if not is_enabled():
        raise StripeError("El pago con Stripe no está configurado.")
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            client_reference_id=str(order.pk),
            metadata={"order_id": str(order.pk)},
            line_items=[
                {
                    "quantity": 1,
                    "price_data": {
                        "currency": settings.STRIPE_CURRENCY,
                        # El guaraní no tiene decimales: el monto va en unidades enteras.
                        "unit_amount": int(order.pyg_amount),
                        "product_data": {
                            "name": f"Compra de {order.currency.code} (orden #{order.pk})",
                        },
                    },
                }
            ],
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except stripe.StripeError as exc:
        logger.warning("Stripe no pudo crear la sesión de la orden %s: %s", order.pk, exc)
        raise StripeError("No se pudo iniciar el pago con Stripe. Probá de nuevo en un momento.") from exc

    PurchaseOrder.objects.filter(pk=order.pk).update(stripe_session_id=session.id)
    return session.url


# --- Webhook ------------------------------------------------------------


def parse_event(payload: bytes, signature: str):
    """Verifica la firma del webhook y devuelve el evento. Lanza ``InvalidWebhook`` si no es de Stripe."""
    try:
        return stripe.Webhook.construct_event(payload, signature, settings.STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise InvalidWebhook(str(exc)) from exc


def handle_event(event) -> str:
    """Procesa un evento ya verificado. Devuelve cómo terminó (para registro y pruebas).

    Resultados: ``ignored`` (tipo que no nos interesa), ``duplicate`` (ya
    procesado), ``unpaid``, ``unknown_order``, ``completed`` o ``refunded``.
    """
    if event["type"] != HANDLED_EVENT:
        return "ignored"

    session = event["data"]["object"]
    with transaction.atomic():
        record, created = StripeEvent.objects.get_or_create(
            event_id=event["id"], defaults={"event_type": event["type"]}
        )
        if not created:
            return "duplicate"

        outcome = _process_session(session, record)
        record.outcome = outcome
        record.save(update_fields=["outcome", "order"])
    return outcome


def _process_session(session, record: StripeEvent) -> str:
    if session.get("payment_status") != "paid":
        return "unpaid"

    order_id = (session.get("metadata") or {}).get("order_id")
    order = (
        PurchaseOrder.objects.select_for_update().select_related("currency").filter(pk=order_id).first()
        if order_id
        else None
    )
    if order is None:
        logger.error("Pago de Stripe %s sin orden asociada (order_id=%s)", session.get("id"), order_id)
        return "unknown_order"

    record.order = order
    payment_intent = session.get("payment_intent") or ""
    can_settle = order.status == PurchaseOrder.PENDING and timezone.now() < order.expires_at + PAYMENT_GRACE
    if can_settle:
        wallet = Wallet.objects.select_for_update().get(pk=order.wallet_id)
        checkout.settle_stripe_payment(order, wallet, payment_intent)
        return "completed"

    _refund(payment_intent, order)
    return "refunded"


def _refund(payment_intent: str, order: PurchaseOrder) -> None:
    """Devuelve el cobro de una orden que ya no se puede entregar."""
    if not payment_intent:
        logger.error("No hay PaymentIntent para reembolsar la orden %s", order.pk)
        return
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        stripe.Refund.create(payment_intent=payment_intent)
    except stripe.StripeError:
        # Si el reembolso falla se deja el error en el registro: el evento queda
        # marcado como procesado y hay que devolver la plata a mano desde Stripe.
        logger.exception("No se pudo reembolsar el pago %s de la orden %s", payment_intent, order.pk)
