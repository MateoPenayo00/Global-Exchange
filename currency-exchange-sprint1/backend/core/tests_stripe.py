"""Pruebas del pago con Stripe. Nunca llaman a Stripe: la librería se reemplaza por mocks."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

import stripe
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from core import roles as role_lib
from core.models import PurchaseOrder, StripeEvent, Wallet
from core.services import checkout, stripe_payments
from core.tests import buy_post, make_test_currency

User = get_user_model()

STRIPE_SETTINGS = dict(
    STRIPE_SECRET_KEY="sk_test_dummy",
    STRIPE_WEBHOOK_SECRET="whsec_dummy",
    STRIPE_CURRENCY="pyg",
)


def completed_event(order, event_id="evt_1", payment_status="paid", payment_intent="pi_1"):
    """Evento ``checkout.session.completed`` como lo entrega Stripe, ya verificado."""
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_1",
                "payment_status": payment_status,
                "payment_intent": payment_intent,
                "metadata": {"order_id": str(order.pk)},
            }
        },
    }


@override_settings(**STRIPE_SETTINGS)
class StripePaymentTests(TestCase):
    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")
        self.user = User.objects.create_user(username="stripe-buyer")
        self.user.groups.add(Group.objects.get_or_create(name=role_lib.USER)[0])
        self.client.force_login(self.user)
        self.currency = make_test_currency()  # venta 8000
        self.wallet = Wallet.objects.create(user=self.user)
        self.client.post("/trade/", buy_post(self.currency, "8000000"))
        self.order = self.wallet.purchase_orders.get()

    def _holding(self):
        holding = self.wallet.holdings.filter(currency=self.currency).first()
        return holding.amount if holding else Decimal("0")

    # --- Medio de pago ------------------------------------------------------

    def test_stripe_is_offered_when_the_key_is_configured(self):
        keys = [option.key for option in checkout.payment_options(self.order, self.wallet)]
        self.assertIn(checkout.STRIPE_KEY, keys)

    @override_settings(STRIPE_SECRET_KEY="")
    def test_stripe_is_hidden_without_a_key(self):
        keys = [option.key for option in checkout.payment_options(self.order, self.wallet)]
        self.assertNotIn(checkout.STRIPE_KEY, keys)

    def test_confirming_with_stripe_never_settles_the_order_directly(self):
        with self.assertRaises(checkout.CheckoutError):
            checkout.confirm_order(self.order.id, self.wallet, checkout.STRIPE_KEY)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, PurchaseOrder.PENDING)

    # --- Crear la sesión ----------------------------------------------------

    def test_choosing_stripe_redirects_to_the_stripe_page_and_keeps_the_session(self):
        session = mock.Mock(id="cs_test_1", url="https://checkout.stripe.com/pay/cs_test_1")
        with mock.patch("stripe.checkout.Session.create", return_value=session) as create:
            response = self.client.post(
                f"/trade/checkout/{self.order.id}/confirm/", {"payment_source": checkout.STRIPE_KEY}
            )
        self.assertRedirects(response, session.url, fetch_redirect_response=False)
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["metadata"], {"order_id": str(self.order.id)})
        self.assertEqual(kwargs["line_items"][0]["price_data"]["unit_amount"], 8000000)
        self.order.refresh_from_db()
        self.assertEqual(self.order.stripe_session_id, "cs_test_1")
        self.assertEqual(self.order.status, PurchaseOrder.PENDING, "volver del pago no confirma nada")

    def test_a_stripe_failure_returns_to_the_checkout_with_an_error(self):
        with mock.patch("stripe.checkout.Session.create", side_effect=stripe.APIConnectionError("sin red")):
            response = self.client.post(
                f"/trade/checkout/{self.order.id}/confirm/", {"payment_source": checkout.STRIPE_KEY}
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/trade/checkout/{self.order.id}/", response.url)

    # --- Webhook ------------------------------------------------------------

    def test_the_webhook_rejects_an_invalid_signature(self):
        response = self.client.post(
            "/stripe/webhook/", data="{}", content_type="application/json", HTTP_STRIPE_SIGNATURE="t=1,v1=falsa"
        )
        self.assertEqual(response.status_code, 400)

    @override_settings(STRIPE_WEBHOOK_SECRET="")
    def test_the_webhook_is_unavailable_without_a_secret(self):
        response = self.client.post("/stripe/webhook/", data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 503)

    def test_the_webhook_only_accepts_post(self):
        self.assertEqual(self.client.get("/stripe/webhook/").status_code, 405)

    def test_a_valid_webhook_event_completes_the_order(self):
        event = completed_event(self.order)
        with mock.patch.object(stripe_payments, "parse_event", return_value=event):
            response = self.client.post("/stripe/webhook/", data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, PurchaseOrder.COMPLETED)
        self.assertEqual(self.order.payment_kind, PurchaseOrder.PAY_STRIPE)
        self.assertEqual(self.order.stripe_payment_intent, "pi_1")
        self.assertEqual(self._holding(), Decimal("1000.000000"))

    def test_the_same_event_delivered_twice_credits_only_once(self):
        event = completed_event(self.order)
        self.assertEqual(stripe_payments.handle_event(event), "completed")
        self.assertEqual(stripe_payments.handle_event(event), "duplicate")
        self.assertEqual(self._holding(), Decimal("1000.000000"))
        self.assertEqual(StripeEvent.objects.filter(event_id="evt_1").count(), 1)

    def test_an_unpaid_session_does_not_complete_the_order(self):
        outcome = stripe_payments.handle_event(completed_event(self.order, payment_status="unpaid"))
        self.assertEqual(outcome, "unpaid")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, PurchaseOrder.PENDING)

    def test_other_event_types_are_ignored(self):
        self.assertEqual(stripe_payments.handle_event({"id": "evt_x", "type": "charge.updated"}), "ignored")

    def test_an_event_for_an_unknown_order_is_recorded_without_crashing(self):
        event = completed_event(self.order)
        event["data"]["object"]["metadata"] = {"order_id": "999999"}
        self.assertEqual(stripe_payments.handle_event(event), "unknown_order")

    def test_a_payment_for_an_expired_order_is_refunded_and_not_delivered(self):
        PurchaseOrder.objects.filter(pk=self.order.pk).update(
            status=PurchaseOrder.EXPIRED, expires_at=timezone.now() - timedelta(minutes=10)
        )
        with mock.patch("stripe.Refund.create") as refund:
            outcome = stripe_payments.handle_event(completed_event(self.order))
        self.assertEqual(outcome, "refunded")
        refund.assert_called_once_with(payment_intent="pi_1")
        self.assertEqual(self._holding(), Decimal("0"))
