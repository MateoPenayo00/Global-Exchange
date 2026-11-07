"""Pruebas de la simulación completa de transacciones y del comando ``poblar_demo``."""

import random
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from core import roles as role_lib
from core.models import (
    CashSession,
    CashTransaction,
    Invoice,
    Notification,
    PurchaseOrder,
    SimulationState,
    StripeEvent,
    Wallet,
    WalletTransaction,
)
from core.services import checkout
from core.services.demo import DEMO_USERS, ensure_demo_users, simulate_transactions
from core.tests import make_cashier, make_test_currency

User = get_user_model()


# El modo real de Factura Segura no debe usarse nunca al simular: hay un token a propósito.
@override_settings(FACTURA_SEGURA_TOKEN="token-que-no-se-debe-usar")
class SimulateTransactionsTests(TestCase):
    def setUp(self):
        self.report = simulate_transactions(rng=random.Random(1))
        self.wallet = Wallet.objects.get(user__username="demo.customer")

    def test_every_step_succeeds(self):
        failed = [vars(step) for step in self.report.steps if not step.ok]
        self.assertEqual(failed, [])

    def test_each_payment_method_completes_a_purchase(self):
        kinds = set(
            self.wallet.purchase_orders.filter(status=PurchaseOrder.COMPLETED).values_list("payment_kind", flat=True)
        )
        self.assertEqual(
            kinds,
            {PurchaseOrder.PAY_BALANCE, PurchaseOrder.PAY_CARD, PurchaseOrder.PAY_STRIPE,
             PurchaseOrder.PAY_QR, PurchaseOrder.PAY_CURRENCY},
        )

    def test_the_stripe_purchase_goes_through_the_webhook_handler(self):
        order = self.wallet.purchase_orders.get(payment_kind=PurchaseOrder.PAY_STRIPE)
        self.assertTrue(order.stripe_payment_intent.startswith("pi_demo_"))
        self.assertEqual(StripeEvent.objects.get(order=order).outcome, "completed")

    def test_a_purchase_is_cancelled_and_another_expires(self):
        statuses = list(self.wallet.purchase_orders.values_list("status", flat=True))
        self.assertIn(PurchaseOrder.CANCELLED, statuses)
        self.assertIn(PurchaseOrder.EXPIRED, statuses)
        self.assertNotIn(PurchaseOrder.PENDING, statuses)

    def test_the_customer_deposits_and_sells(self):
        kinds = set(self.wallet.transactions.values_list("kind", flat=True))
        self.assertTrue({WalletTransaction.DEPOSIT, WalletTransaction.BUY, WalletTransaction.SELL} <= kinds)

    def test_the_cash_shift_is_complete_and_balances(self):
        session = CashSession.objects.get(cashier__username="demo.cajero")
        self.assertEqual(session.status, CashSession.CLOSED)
        done = session.transactions.filter(status=CashTransaction.COMPLETED)
        self.assertEqual(set(done.values_list("payment_method", flat=True)), {"cash", "card", "qr"})
        self.assertTrue(session.transactions.filter(status=CashTransaction.CANCELLED).exists())
        for balance in session.balances.all():
            self.assertEqual(balance.closing_amount, balance.current_amount)

    def test_every_paid_purchase_and_every_sale_of_currency_at_the_desk_is_invoiced(self):
        paid = self.wallet.purchase_orders.filter(status=PurchaseOrder.COMPLETED)
        self.assertEqual(Invoice.objects.filter(purchase_order__in=paid).count(), paid.count())
        sold = CashTransaction.objects.filter(status=CashTransaction.COMPLETED).exclude(currency_out__code="PYG")
        self.assertEqual(Invoice.objects.filter(cash_transaction__in=sold).count(), sold.count())
        self.assertEqual(self.report.invoices, Invoice.objects.count())

    def test_simulated_invoices_never_use_the_real_range(self):
        self.assertFalse(Invoice.objects.filter(is_simulated=False).exists())
        self.assertFalse(Invoice.objects.exclude(status=Invoice.APPROVED).exists())

    def test_it_can_run_again(self):
        simulate_transactions(rng=random.Random(2))
        self.assertEqual(CashSession.objects.filter(cashier__username="demo.cajero").count(), 2)
        self.assertFalse(CashSession.objects.filter(status=CashSession.OPEN).exists())


class DemoUsersTests(TestCase):
    def test_the_demo_users_get_their_role(self):
        users = ensure_demo_users()
        for username, (role, _, _) in DEMO_USERS.items():
            self.assertIn(role, role_lib.user_roles(users[username]))
        self.assertTrue(users["demo.admin"].is_staff)

    def test_it_is_idempotent(self):
        ensure_demo_users()
        ensure_demo_users()
        self.assertEqual(User.objects.filter(username__startswith="demo.").count(), len(DEMO_USERS))


class PoblarDemoCommandTests(TestCase):
    def _run(self, *args):
        out = StringIO()
        call_command("poblar_demo", *args, stdout=out)
        return out.getvalue()

    def test_it_populates_an_empty_database(self):
        output = self._run("--dias", "3")
        self.assertIn("0 con error", output)
        self.assertEqual(SimulationState.load().days_advanced, 3)
        self.assertTrue(Notification.objects.filter(user__username="demo.customer").exists())
        self.assertTrue(Invoice.objects.exists())

    def test_a_second_run_adds_nothing(self):
        self._run()
        invoices = Invoice.objects.count()
        self.assertIn("Ya hay datos", self._run())
        self.assertEqual(Invoice.objects.count(), invoices)
        self.assertEqual(SimulationState.load().days_advanced, 7)

    def test_force_adds_another_round(self):
        self._run("--dias", "0")
        invoices = Invoice.objects.count()
        self._run("--force", "--dias", "0")
        self.assertGreater(Invoice.objects.count(), invoices)


class SimulationPanelTests(TestCase):
    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")
        self.client.force_login(make_cashier("jefa", role_lib.ADMIN))

    def test_the_button_runs_the_simulation_and_shows_the_steps(self):
        response = self.client.post("/simulation/transactions/", follow=True)
        self.assertContains(response, "Simulación completa:")
        self.assertContains(response, "con Stripe")
        self.assertContains(response, "Cierre de caja")
        self.assertTrue(Invoice.objects.exists())

    def test_a_get_does_not_simulate(self):
        self.assertRedirects(self.client.get("/simulation/transactions/"), "/simulation/")
        self.assertFalse(Invoice.objects.exists())

    def test_only_an_admin_can_simulate(self):
        other = Client(SERVER_NAME="localhost")
        other.force_login(make_cashier("gestor", role_lib.MANAGER))
        self.assertEqual(other.post("/simulation/transactions/").status_code, 403)


class SellServiceTests(TestCase):
    def setUp(self):
        self.user = make_cashier("vendedor", role_lib.USER)
        self.wallet = Wallet.objects.create(user=self.user)
        self.currency = make_test_currency()  # compra 7000
        self.wallet.holdings.create(currency=self.currency, amount=Decimal("10"))

    def test_selling_credits_guaranies_at_the_buy_price(self):
        sale = checkout.sell_currency(self.wallet, self.currency, Decimal("2"))
        self.assertEqual((sale.pyg_amount, sale.rate_used), (Decimal("14000"), Decimal("7000")))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.pyg_balance, Decimal("14000"))
        self.assertEqual(self.wallet.holding_amount(self.currency), Decimal("8"))

    def test_selling_more_than_held_is_rejected(self):
        with self.assertRaises(checkout.CheckoutError):
            checkout.sell_currency(self.wallet, self.currency, Decimal("11"))
        self.assertEqual(self.wallet.holding_amount(self.currency), Decimal("10"))
