"""Pruebas unitarias de la app core de Exchange Pro.

Se ejecutan con:
    python manage.py test core
o, con el stack levantado:
    docker compose exec web python manage.py test core -v 2

Las migraciones siembran el guaraní (PYG) como divisa base y un conjunto de
divisas de demostración (USD, EUR, BRL, ARS) con 30 días de historial, así que
varias pruebas se apoyan en esos datos. Cuando una prueba necesita cotizaciones
de números redondos usa el código XTS, reservado por la norma ISO 4217
justamente para pruebas.
"""

import random
from datetime import timedelta
from unittest import mock
from decimal import ROUND_DOWN, Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.utils import timezone

from core import roles as role_lib
from core.forms import (
    CurrencyForm,
    DeleteAccountForm,
    PaymentMethodForm,
    WalletDepositForm,
)
from core.models import (
    BASE_CURRENCY_CODE,
    PURCHASE_TIMEOUT,
    CashSession,
    CashSessionBalance,
    CashTransaction,
    Currency,
    CurrencyRate,
    PaymentMethod,
    PurchaseOrder,
    SimulationState,
    Wallet,
    WalletTransaction,
)
from core.services import cashier, checkout, pricing
from core.services.charts import build_rate_chart
from core.services.qr import MODULES, mock_qr_modules, mock_qr_path
from core.services.simulation import (
    MAX_DAILY_CHANGE,
    MIN_DAILY_CHANGE,
    advance_days,
    advance_one_day,
)
from core.services.keycloak_admin import KeycloakAdminClient
from core.templatetags.money import cash, cedula, countdown, subtract
from core.views import DEPOSIT_PRESETS, TEST_DEPOSIT_AMOUNT

User = get_user_model()

# Número de tarjeta de prueba válido según el algoritmo de Luhn.
VALID_CARD = "4111111111111111"


def make_card(wallet, label="Visa de prueba"):
    """Crea un medio de pago de prueba directamente, sin pasar por el formulario."""
    return PaymentMethod.objects.create(
        wallet=wallet,
        label=label,
        holder_name="Ana Prueba",
        brand=PaymentMethod.VISA,
        last4="1111",
        expiry_month=12,
        expiry_year=2099,
    )


def buy_post(currency, amount, *, pay_currency=None, anchor="in"):
    """Datos del formulario de compra: ``amount`` es lo que se paga (``in``) o lo que se recibe (``out``)."""
    pay_currency = pay_currency or Currency.objects.get(code=BASE_CURRENCY_CODE)
    data = {"action": "buy", "currency": currency.id, "pay_currency": pay_currency.id, "anchor": anchor}
    data["pay_amount" if anchor == "in" else "receive_amount"] = str(amount)
    return data


def order_id_from(response):
    """Id de la orden a partir del redirect a ``/trade/checkout/<id>/?pay=...``, o ``None``."""
    if response.status_code != 302 or "/checkout/" not in response.url:
        return None
    return int(response.url.split("?")[0].rstrip("/").rsplit("/", 1)[-1])


def buy(client, currency, pyg_amount, source="balance"):
    """Compra completa en dos pasos: abrir la orden en la pizarra y confirmar el pago.

    Paga ``pyg_amount`` guaraníes. ``source`` es la clave del medio de pago:
    ``balance``, ``card:<id>``, ``currency:<id>`` o ``qr``. Devuelve la orden, o
    ``None`` si no se abrió.
    """
    order_id = order_id_from(client.post("/trade/", buy_post(currency, pyg_amount)))
    if order_id is None:
        return None
    client.post(f"/trade/checkout/{order_id}/confirm/", {"payment_source": source})
    return PurchaseOrder.objects.get(pk=order_id)


def make_test_currency(buy="7000", sell="8000"):
    """Divisa de prueba con cotizaciones redondas, para que las cuentas sean exactas."""
    return Currency.objects.create(
        code="XTS",
        name="Divisa de prueba",
        symbol="X$",
        buy_rate=Decimal(buy),
        sell_rate=Decimal(sell),
    )


class RolesTests(TestCase):
    """core/roles.py — ayudantes de roles."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice")

    def test_user_with_no_group_has_no_roles(self):
        self.assertEqual(role_lib.user_roles(self.user), set())
        self.assertFalse(role_lib.is_admin(self.user))

    def test_admin_group_grants_admin_role(self):
        self.user.groups.add(Group.objects.get_or_create(name=role_lib.ADMIN)[0])
        self.assertTrue(role_lib.is_admin(self.user))
        self.assertTrue(role_lib.is_manager(self.user), "un admin hereda las capacidades de gestor")

    def test_manager_group_does_not_grant_admin(self):
        self.user.groups.add(Group.objects.get_or_create(name=role_lib.MANAGER)[0])
        self.assertTrue(role_lib.is_manager(self.user))
        self.assertFalse(role_lib.is_admin(self.user))

    def test_user_group_grants_trader_role_only(self):
        self.user.groups.add(Group.objects.get_or_create(name=role_lib.USER)[0])
        self.assertTrue(role_lib.is_trader(self.user))
        self.assertFalse(role_lib.is_admin(self.user))
        self.assertFalse(role_lib.is_manager(self.user))

    def test_roles_from_claims_ignores_unknown_roles(self):
        claims = {"realm_access": {"roles": ["admin", "offline_access", "made_up"]}}
        self.assertEqual(role_lib.roles_from_claims(claims), {"admin"})


class BaseCurrencyTests(TestCase):
    """core/models.py — el guaraní como divisa base."""

    def test_guarani_is_seeded_as_the_base_currency(self):
        base = Currency.objects.get(code=BASE_CURRENCY_CODE)
        self.assertEqual(BASE_CURRENCY_CODE, "PYG")
        self.assertTrue(base.is_base)
        self.assertEqual(base.buy_rate, Decimal("1.000000"))
        self.assertEqual(base.sell_rate, Decimal("1.000000"))

    def test_guarani_rates_are_locked_to_one(self):
        base = Currency.objects.get(code=BASE_CURRENCY_CODE)
        base.buy_rate = Decimal("5.00")
        base.sell_rate = Decimal("9.00")
        base.save()
        base.refresh_from_db()
        self.assertEqual(base.buy_rate, Decimal("1.000000"))
        self.assertEqual(base.sell_rate, Decimal("1.000000"))

    def test_demo_currencies_are_quoted_in_guaranies(self):
        usd = Currency.objects.get(code="USD")
        self.assertFalse(usd.is_base)
        self.assertGreater(usd.buy_rate, Decimal("1000"), "el dólar vale miles de guaraníes")
        self.assertLess(usd.buy_rate, usd.sell_rate, "la casa de cambio compra más barato de lo que vende")


class CurrencyRatesTests(TestCase):
    """core/models.py — precio de compra, precio de venta y margen."""

    def test_code_is_upper_cased_and_rates_are_kept(self):
        currency = Currency.objects.create(
            code="xts", name="Divisa de prueba", buy_rate=Decimal("7000"), sell_rate=Decimal("8000")
        )
        self.assertEqual(currency.code, "XTS")
        self.assertEqual(currency.buy_rate, Decimal("7000"))
        self.assertEqual(currency.sell_rate, Decimal("8000"))

    def test_mid_rate_is_the_average_of_both_prices(self):
        currency = make_test_currency()
        self.assertEqual(currency.mid_rate, Decimal("7500.000000"))

    def test_spread_is_the_difference_between_sell_and_buy(self):
        currency = make_test_currency()
        self.assertEqual(currency.spread, Decimal("1000"))
        # 1000 / 7500 = 13,33 %
        self.assertEqual(currency.spread_percent, Decimal("13.33"))

    def test_record_rate_stores_one_history_point_per_day(self):
        currency = make_test_currency()
        day = SimulationState.load().current_date

        currency.record_rate(on_date=day)
        currency.buy_rate = Decimal("7100")
        currency.save()
        currency.record_rate(on_date=day)

        points = CurrencyRate.objects.filter(currency=currency, date=day)
        self.assertEqual(points.count(), 1, "dos cambios el mismo día actualizan el mismo punto")
        self.assertEqual(points.first().buy_rate, Decimal("7100.000000"))

    def test_base_currency_has_no_history(self):
        base = Currency.objects.get(code=BASE_CURRENCY_CODE)
        self.assertIsNone(base.record_rate())
        self.assertEqual(base.rates.count(), 0)

    def test_demo_currencies_come_with_seeded_history(self):
        self.assertGreaterEqual(Currency.objects.get(code="USD").rates.count(), 30)


class CurrencyFormTests(TestCase):
    """core/forms.py — CurrencyForm."""

    def _data(self, **overrides):
        data = {
            "code": "xts",
            "name": "Divisa de prueba",
            "symbol": "X$",
            "buy_rate": "7000",
            "sell_rate": "8000",
            "is_active": "on",
        }
        data.update(overrides)
        return data

    def test_accepts_a_buy_price_below_the_sell_price(self):
        form = CurrencyForm(data=self._data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["code"], "XTS")

    def test_rejects_a_buy_price_above_the_sell_price(self):
        form = CurrencyForm(data=self._data(buy_rate="9000", sell_rate="8000"))
        self.assertFalse(form.is_valid())
        self.assertIn("compra debe ser menor", str(form.errors))

    def test_rejects_equal_buy_and_sell_prices(self):
        form = CurrencyForm(data=self._data(buy_rate="8000", sell_rate="8000"))
        self.assertFalse(form.is_valid())

    def test_rejects_zero_or_negative_prices(self):
        form = CurrencyForm(data=self._data(buy_rate="0"))
        self.assertFalse(form.is_valid())
        self.assertIn("buy_rate", form.errors)


class PaymentMethodFormTests(TestCase):
    """core/forms.py — PaymentMethodForm (tarjetas de crédito)."""

    def setUp(self):
        self.user = User.objects.create_user(username="cardholder")
        self.wallet = Wallet.objects.create(user=self.user)

    def _data(self, **overrides):
        data = {
            "label": "Visa personal",
            "holder_name": "Ana Prueba",
            "card_number": VALID_CARD,
            "expiry_month": "12",
            "expiry_year": "2099",
        }
        data.update(overrides)
        return data

    def test_rejects_a_number_that_fails_the_luhn_check(self):
        form = PaymentMethodForm(data=self._data(card_number="4111111111111112"))
        self.assertFalse(form.is_valid())
        self.assertIn("card_number", form.errors)

    def test_rejects_a_number_that_is_too_short(self):
        form = PaymentMethodForm(data=self._data(card_number="4111"))
        self.assertFalse(form.is_valid())

    def test_rejects_an_expired_card(self):
        form = PaymentMethodForm(data=self._data(expiry_month="1", expiry_year="2001"))
        self.assertFalse(form.is_valid())
        self.assertIn("vencida", str(form.errors))

    def test_accepts_spaces_in_the_card_number(self):
        form = PaymentMethodForm(data=self._data(card_number="4111 1111 1111 1111"))
        self.assertTrue(form.is_valid(), form.errors)

    def test_only_the_last_four_digits_are_stored(self):
        form = PaymentMethodForm(data=self._data())
        self.assertTrue(form.is_valid(), form.errors)
        card = form.build_payment_method(self.wallet)
        self.assertEqual(card.last4, "1111")
        self.assertEqual(card.brand, PaymentMethod.VISA, "un número que empieza con 4 es Visa")
        self.assertEqual(card.masked_number, "•••• •••• •••• 1111")
        stored = [str(value) for value in PaymentMethod.objects.filter(pk=card.pk).values()[0].values()]
        self.assertFalse(
            any(VALID_CARD in value for value in stored),
            "el número completo de la tarjeta nunca debe quedar guardado",
        )

    def test_mastercard_and_amex_are_detected(self):
        mastercard = PaymentMethodForm(data=self._data(card_number="5500000000000004"))
        self.assertTrue(mastercard.is_valid(), mastercard.errors)
        self.assertEqual(mastercard.build_payment_method(self.wallet).brand, PaymentMethod.MASTERCARD)

        amex = PaymentMethodForm(data=self._data(card_number="340000000000009"))
        self.assertTrue(amex.is_valid(), amex.errors)
        self.assertEqual(amex.build_payment_method(self.wallet).brand, PaymentMethod.AMEX)


class WalletDepositFormTests(TestCase):
    """core/forms.py — el formulario de carga sólo acepta tarjetas propias."""

    def setUp(self):
        self.owner = Wallet.objects.create(user=User.objects.create_user(username="owner"))
        self.other = Wallet.objects.create(user=User.objects.create_user(username="other"))

    def test_rejects_a_card_belonging_to_another_wallet(self):
        foreign_card = make_card(self.other)
        form = WalletDepositForm(
            data={"amount": "100000", "payment_method": foreign_card.id}, wallet=self.owner
        )
        self.assertFalse(form.is_valid())
        self.assertIn("payment_method", form.errors)

    def test_accepts_an_own_card(self):
        card = make_card(self.owner)
        form = WalletDepositForm(data={"amount": "100000", "payment_method": card.id}, wallet=self.owner)
        self.assertTrue(form.is_valid(), form.errors)


class DeleteAccountFormTests(TestCase):
    """core/forms.py — DeleteAccountForm."""

    def test_requires_the_word_delete(self):
        form = DeleteAccountForm(data={"confirmation": "please"})
        self.assertFalse(form.is_valid())

    def test_accepts_delete_case_insensitively(self):
        form = DeleteAccountForm(data={"confirmation": "delete"})
        self.assertTrue(form.is_valid())


class RoleRequiredViewTests(TestCase):
    """Control de acceso por rol, ejercitado a través de las vistas reales."""

    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")
        self.admin = User.objects.create_user(username="test_admin")
        self.admin.groups.add(Group.objects.get_or_create(name=role_lib.ADMIN)[0])
        self.manager = User.objects.create_user(username="test_manager")
        self.manager.groups.add(Group.objects.get_or_create(name=role_lib.MANAGER)[0])
        self.trader = User.objects.create_user(username="test_user")
        self.trader.groups.add(Group.objects.get_or_create(name=role_lib.USER)[0])

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get("/manage/users/")
        self.assertEqual(response.status_code, 302)

    def test_trader_cannot_reach_user_management(self):
        self.client.force_login(self.trader)
        self.assertEqual(self.client.get("/manage/users/").status_code, 403)

    def test_manager_cannot_reach_user_management(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get("/manage/users/").status_code, 403)

    def test_manager_can_reach_currency_management(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get("/currencies/").status_code, 200)

    def test_trader_cannot_reach_currency_management(self):
        self.client.force_login(self.trader)
        self.assertEqual(self.client.get("/currencies/").status_code, 403)

    def test_trader_can_reach_wallet(self):
        self.client.force_login(self.trader)
        self.assertEqual(self.client.get("/wallet/").status_code, 200)

    def test_only_an_admin_can_reach_the_simulation_panel(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/simulation/").status_code, 200)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get("/simulation/").status_code, 403)
        self.client.force_login(self.trader)
        self.assertEqual(self.client.get("/simulation/").status_code, 403)

    def test_rate_history_is_public(self):
        self.assertEqual(self.client.get("/history/").status_code, 200)


class WalletTests(TestCase):
    """Flujos de billetera: tarjetas, carga de saldo, compra, venta y retiro."""

    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")
        self.user = User.objects.create_user(username="trader1")
        self.user.groups.add(Group.objects.get_or_create(name=role_lib.USER)[0])
        self.client.force_login(self.user)
        self.currency = make_test_currency()  # compra 7000 / venta 8000

    def _wallet(self):
        # Las vistas crean la billetera la primera vez que se la necesita.
        wallet, _ = Wallet.objects.get_or_create(user=self.user)
        return wallet

    def _card(self):
        return make_card(self._wallet())

    def test_new_user_starts_with_an_empty_wallet(self):
        response = self.client.get("/wallet/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._wallet().pyg_balance, Decimal("0.00"))

    def test_a_card_can_be_registered_from_the_wallet(self):
        response = self.client.post(
            "/wallet/payment-methods/new/",
            {
                "label": "Visa personal",
                "holder_name": "Ana Prueba",
                "card_number": VALID_CARD,
                "expiry_month": "12",
                "expiry_year": "2099",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._wallet().payment_methods.count(), 1)

    def test_the_card_form_page_renders(self):
        response = self.client.get("/wallet/payment-methods/new/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Agregar tarjeta de crédito")

    def test_the_card_deletion_page_asks_for_confirmation(self):
        card = self._card()
        response = self.client.get(f"/wallet/payment-methods/{card.id}/delete/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, card.last4)

    def test_a_card_from_another_user_cannot_be_deleted(self):
        other = Wallet.objects.create(user=User.objects.create_user(username="someone_else"))
        foreign_card = make_card(other)
        response = self.client.post(f"/wallet/payment-methods/{foreign_card.id}/delete/")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(PaymentMethod.objects.filter(pk=foreign_card.pk).exists())

    def test_deposit_is_refused_without_a_payment_method(self):
        self.client.post("/wallet/deposit/", {"amount": "100000"})
        self.assertEqual(self._wallet().pyg_balance, Decimal("0.00"))

    def test_deposit_credits_guaranies_and_records_the_card(self):
        card = self._card()
        self.client.post("/wallet/deposit/", {"amount": "250000", "payment_method": card.id})
        wallet = self._wallet()
        self.assertEqual(wallet.pyg_balance, Decimal("250000.00"))
        transaction = wallet.transactions.get(kind=WalletTransaction.DEPOSIT)
        self.assertEqual(transaction.payment_method_id, card.id)

    def test_test_deposit_button_uses_the_registered_card(self):
        self._card()
        self.client.post("/wallet/deposit/test/")
        self.assertEqual(self._wallet().pyg_balance, TEST_DEPOSIT_AMOUNT)

    def test_test_deposit_button_does_nothing_without_a_card(self):
        self.client.post("/wallet/deposit/test/")
        self.assertEqual(self._wallet().pyg_balance, Decimal("0.00"))

    def test_buying_with_the_balance_uses_the_sell_price(self):
        card = self._card()
        self.client.post("/wallet/deposit/", {"amount": "8000000", "payment_method": card.id})
        buy(self.client, self.currency, "8000000")
        wallet = self._wallet()
        holding = wallet.holdings.get(currency=self.currency)
        self.assertEqual(wallet.pyg_balance, Decimal("0.00"))
        # 8.000.000 Gs. / 8000 (precio de venta) = 1000 unidades
        self.assertEqual(holding.amount, Decimal("1000.000000"))
        transaction = wallet.transactions.get(kind=WalletTransaction.BUY)
        self.assertEqual(transaction.rate_used, Decimal("8000.000000"))

    def test_buying_with_a_card_does_not_touch_the_balance(self):
        card = self._card()
        buy(self.client, self.currency, "8000000", f"card:{card.id}")
        wallet = self._wallet()
        self.assertEqual(wallet.pyg_balance, Decimal("0.00"), "pagar con tarjeta no usa el saldo")
        self.assertEqual(wallet.holdings.get(currency=self.currency).amount, Decimal("1000.000000"))
        self.assertEqual(wallet.transactions.get(kind=WalletTransaction.BUY).payment_method_id, card.id)

    def test_cannot_buy_with_a_balance_that_is_too_low(self):
        buy(self.client, self.currency, "8000000")
        wallet = self._wallet()
        self.assertEqual(wallet.pyg_balance, Decimal("0.00"))
        self.assertFalse(wallet.holdings.filter(currency=self.currency).exists())

    def test_cannot_buy_with_a_card_of_another_user(self):
        other = Wallet.objects.create(user=User.objects.create_user(username="intruder_target"))
        foreign_card = make_card(other)
        buy(self.client, self.currency, "8000000", f"card:{foreign_card.id}")
        self.assertFalse(self._wallet().holdings.filter(currency=self.currency).exists())

    def test_selling_uses_the_buy_price_and_credits_the_balance(self):
        card = self._card()
        self.client.post("/wallet/deposit/", {"amount": "8000000", "payment_method": card.id})
        buy(self.client, self.currency, "8000000")
        self.client.post("/trade/", {"action": "sell", "currency": self.currency.id, "amount": "1000"})

        wallet = self._wallet()
        # 1000 unidades × 7000 (precio de compra) = 7.000.000 Gs.
        self.assertEqual(wallet.pyg_balance, Decimal("7000000.00"))
        self.assertEqual(wallet.holdings.get(currency=self.currency).amount, Decimal("0.000000"))
        transaction = wallet.transactions.get(kind=WalletTransaction.SELL)
        self.assertEqual(transaction.rate_used, Decimal("7000.000000"))

    def test_the_spread_is_the_cost_of_a_round_trip(self):
        """Comprar y vender de inmediato deja al usuario con menos guaraníes: ese es el margen."""
        card = self._card()
        self.client.post("/wallet/deposit/", {"amount": "8000000", "payment_method": card.id})
        buy(self.client, self.currency, "8000000")
        self.client.post("/trade/", {"action": "sell", "currency": self.currency.id, "amount": "1000"})
        self.assertLess(self._wallet().pyg_balance, Decimal("8000000.00"))

    def test_cannot_sell_more_than_the_holding(self):
        card = self._card()
        self.client.post("/wallet/deposit/", {"amount": "8000000", "payment_method": card.id})
        buy(self.client, self.currency, "8000000")
        self.client.post("/trade/", {"action": "sell", "currency": self.currency.id, "amount": "9999"})
        wallet = self._wallet()
        self.assertEqual(wallet.holdings.get(currency=self.currency).amount, Decimal("1000.000000"))
        self.assertEqual(wallet.pyg_balance, Decimal("0.00"))

    def test_withdraw_test_button_removes_a_small_fixed_amount(self):
        card = self._card()
        self.client.post("/wallet/deposit/", {"amount": "8000000", "payment_method": card.id})
        buy(self.client, self.currency, "8000000")
        self.client.post(f"/wallet/withdraw/{self.currency.id}/test/")
        self.assertEqual(
            self._wallet().holdings.get(currency=self.currency).amount, Decimal("999.000000")
        )

    def test_cannot_withdraw_more_than_the_holding(self):
        card = self._card()
        self.client.post("/wallet/deposit/", {"amount": "8000000", "payment_method": card.id})
        buy(self.client, self.currency, "8000000")
        self.client.post("/wallet/withdraw/", {"currency": self.currency.id, "amount": "9999"})
        self.assertEqual(
            self._wallet().holdings.get(currency=self.currency).amount,
            Decimal("1000.000000"),
            "un retiro mayor a la tenencia debe rechazarse",
        )

    def test_holdings_are_valued_at_the_buy_price(self):
        card = self._card()
        self.client.post("/wallet/deposit/", {"amount": "8000000", "payment_method": card.id})
        buy(self.client, self.currency, "8000000")
        self.assertEqual(self._wallet().holdings_value_pyg, Decimal("7000000"))


class BuyPreviewTests(TestCase):
    """Vista previa de la conversión en la pantalla de compra."""

    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")
        self.user = User.objects.create_user(username="previewer")
        self.user.groups.add(Group.objects.get_or_create(name=role_lib.USER)[0])
        self.client.force_login(self.user)
        self.currency = make_test_currency()  # compra 7000 / venta 8000

    def test_the_preview_panel_is_rendered(self):
        response = self.client.get("/trade/")
        self.assertContains(response, 'id="buy-preview"')
        self.assertContains(response, "Vista previa de la operación")
        self.assertContains(response, 'id="preview-bar-resale"')

    def test_the_rate_table_carries_both_prices_of_every_active_currency(self):
        response = self.client.get("/trade/")
        table = response.context["rate_table"]
        entry = table[str(self.currency.id)]
        self.assertEqual(entry["code"], "XTS")
        # Las cotizaciones viajan como texto, así que se comparan como Decimal.
        self.assertEqual(Decimal(entry["sell_rate"]), Decimal("8000"))
        self.assertEqual(Decimal(entry["buy_rate"]), Decimal("7000"))
        self.assertIn(str(Currency.objects.get(code="USD").id), table)

    def test_the_base_currency_can_pay_but_cannot_be_bought(self):
        response = self.client.get("/trade/")
        base = Currency.objects.get(code=BASE_CURRENCY_CODE)
        form = response.context["buy_form"]
        self.assertNotIn(base, form.fields["currency"].queryset)
        self.assertIn(base, form.fields["pay_currency"].queryset)
        entry = response.context["rate_table"][str(base.id)]
        self.assertEqual((entry["buy_rate"], entry["sell_rate"], entry["decimals"]), ("1", "1", 0))

    def test_an_inactive_currency_is_not_offered_in_the_preview(self):
        self.currency.is_active = False
        self.currency.save()
        response = self.client.get("/trade/")
        self.assertNotIn(str(self.currency.id), response.context["rate_table"])

    def test_the_buy_form_leads_to_the_payment_step(self):
        """El medio de pago ya no se elige en la pizarra sino en la pantalla de pago."""
        response = self.client.get("/trade/")
        self.assertContains(response, "Continuar al pago")
        self.assertNotIn("payment_source", response.context["buy_form"].fields)

    def test_the_two_forms_do_not_share_field_ids(self):
        """Los dos formularios tienen un campo "currency": sus ids deben diferir."""
        response = self.client.get("/trade/")
        html = response.content.decode()
        self.assertIn('id="id_buy_currency"', html)
        self.assertIn('id="id_buy_pay_amount"', html)
        self.assertIn('id="id_buy_receive_amount"', html)
        self.assertNotIn('id="id_currency"', html)

    def test_the_preview_matches_what_the_purchase_actually_does(self):
        """La cuenta que hace el navegador y la que hace el servidor deben coincidir."""
        entry = self.client.get("/trade/").context["rate_table"][str(self.currency.id)]
        sell_rate = Decimal(entry["sell_rate"])
        amount = Decimal("8000000")
        expected = (amount / sell_rate).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)

        card = make_card(Wallet.objects.get_or_create(user=self.user)[0])
        buy(self.client, self.currency, str(amount), f"card:{card.id}")
        holding = Wallet.objects.get(user=self.user).holdings.get(currency=self.currency)
        self.assertEqual(holding.amount, expected)


class NavigationTests(TestCase):
    """La navegación de la barra lateral y el estado activo."""

    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")
        self.user = User.objects.create_user(username="navigator")
        self.user.groups.add(Group.objects.get_or_create(name=role_lib.USER)[0])

    def test_the_public_pages_show_the_sidebar_with_login_actions(self):
        html = self.client.get("/").content.decode()
        self.assertIn('class="sidebar"', html)
        self.assertIn("Iniciar sesión", html)
        self.assertNotIn("Mi billetera", html, "un visitante anónimo no debería ver la billetera")

    def test_a_trader_sees_the_operating_section(self):
        self.client.force_login(self.user)
        html = self.client.get("/dashboard/").content.decode()
        self.assertIn("Mi billetera", html)
        self.assertIn("Comprar y vender", html)
        self.assertNotIn("Simulación", html, "un usuario no debería ver la sección de administración")

    def test_the_current_page_is_marked_active_in_the_sidebar(self):
        self.client.force_login(self.user)
        html = self.client.get("/wallet/").content.decode()
        self.assertIn('class="nav-item is-active" href="/wallet/"', html)

    def test_the_stylesheet_and_logo_are_linked(self):
        html = self.client.get("/").content.decode()
        self.assertIn("/static/css/app.css", html)
        self.assertIn("/static/img/favicon.svg", html)
        self.assertIn("rocket-body", html, "el logotipo del cohete se incluye en línea")


class FewerClicksTests(TestCase):
    """Atajos que recortan clics: preselección de divisa, alta de tarjeta en la
    misma página, montos predefinidos y avance de una semana completa."""

    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")
        self.user = User.objects.create_user(username="hurried")
        self.user.groups.add(Group.objects.get_or_create(name=role_lib.USER)[0])
        self.client.force_login(self.user)
        self.currency = make_test_currency()

    def _wallet(self):
        wallet, _ = Wallet.objects.get_or_create(user=self.user)
        return wallet

    def test_the_buy_form_arrives_with_the_currency_already_chosen(self):
        response = self.client.get(f"/trade/?currency={self.currency.id}")
        self.assertEqual(response.context["buy_form"].initial["currency"], str(self.currency.id))
        self.assertIn(
            f'value="{self.currency.id}" selected', response.content.decode(),
            "la divisa indicada en la URL debe quedar seleccionada en el formulario",
        )

    def test_the_sell_form_arrives_with_the_currency_already_chosen(self):
        card = make_card(self._wallet())
        buy(self.client, self.currency, "8000000", f"card:{card.id}")
        response = self.client.get(f"/trade/?sell={self.currency.id}")
        self.assertEqual(response.context["sell_form"].initial["currency"], str(self.currency.id))

    def test_an_unknown_currency_in_the_url_is_harmless(self):
        response = self.client.get("/trade/?currency=999999")
        self.assertEqual(response.status_code, 200)

    def test_the_card_form_is_available_on_the_wallet_page_itself(self):
        """Sin esto hay que cargar otra página sólo para agregar una tarjeta."""
        html = self.client.get("/wallet/").content.decode()
        self.assertIn("Agregar una tarjeta", html)
        self.assertIn('action="/wallet/payment-methods/new/"', html)
        self.assertIn("id_card_number", html)

    def test_the_wallet_offers_preset_deposit_amounts(self):
        response = self.client.get("/wallet/")
        self.assertEqual(response.context["deposit_presets"], DEPOSIT_PRESETS)
        make_card(self._wallet())
        html = self.client.get("/wallet/").content.decode()
        for preset in DEPOSIT_PRESETS:
            self.assertIn(f'data-amount="{preset}"', html)

    def test_a_card_can_be_deleted_straight_from_the_wallet_row(self):
        card = make_card(self._wallet())
        html = self.client.get("/wallet/").content.decode()
        self.assertIn(f'action="/wallet/payment-methods/{card.id}/delete/"', html)
        self.client.post(f"/wallet/payment-methods/{card.id}/delete/")
        self.assertFalse(PaymentMethod.objects.filter(pk=card.pk).exists())


class AdvanceWeekTests(TestCase):
    """Avanzar siete días con un clic en lugar de siete."""

    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")
        self.admin = User.objects.create_user(username="week_admin")
        self.admin.groups.add(Group.objects.get_or_create(name=role_lib.ADMIN)[0])
        self.client.force_login(self.admin)
        self.currency = make_test_currency()

    def test_advance_days_moves_the_clock_by_that_many_days(self):
        before = SimulationState.load().current_date
        state, _ = advance_days(7, rng=random.Random(11))
        self.assertEqual(state.current_date, before + timedelta(days=7))
        self.assertEqual(state.days_advanced, 7)

    def test_advance_days_leaves_one_history_point_per_day(self):
        advance_days(7, rng=random.Random(12))
        self.assertEqual(
            CurrencyRate.objects.filter(currency=self.currency, source=CurrencyRate.SIMULATION).count(), 7
        )

    def test_advance_days_reports_the_net_change_not_the_last_day(self):
        self.currency.refresh_from_db()
        start_buy = self.currency.buy_rate
        _, changes = advance_days(7, rng=random.Random(13))
        change = next(c for c in changes if c.currency.code == "XTS")
        self.currency.refresh_from_db()
        self.assertEqual(change.previous_buy, start_buy, "debe comparar contra el inicio del período")
        self.assertEqual(change.new_buy, self.currency.buy_rate)

    def test_advance_days_rejects_a_nonsense_count(self):
        with self.assertRaises(ValueError):
            advance_days(0)

    def test_the_week_button_advances_seven_days(self):
        before = SimulationState.load().current_date
        response = self.client.post("/simulation/advance-week/", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SimulationState.load().current_date, before + timedelta(days=7))
        self.assertContains(response, "7 días simulados")

    def test_a_get_request_does_not_advance_the_week(self):
        before = SimulationState.load().current_date
        self.client.get("/simulation/advance-week/")
        self.assertEqual(SimulationState.load().current_date, before)

    def test_only_an_admin_can_advance_the_week(self):
        trader = User.objects.create_user(username="not_admin")
        trader.groups.add(Group.objects.get_or_create(name=role_lib.USER)[0])
        self.client.force_login(trader)
        self.assertEqual(self.client.post("/simulation/advance-week/").status_code, 403)


class SimulationTests(TestCase):
    """core/services/simulation.py — el botón de adelantar un día."""

    def setUp(self):
        self.currency = make_test_currency()

    def test_advancing_moves_the_simulated_clock_one_day(self):
        before = SimulationState.load().current_date
        state, _ = advance_one_day(rng=random.Random(1))
        self.assertEqual(state.current_date, before + timedelta(days=1))
        self.assertEqual(state.days_advanced, 1)

    def test_every_active_currency_is_requoted(self):
        _, changes = advance_one_day(rng=random.Random(2))
        codes = {change.currency.code for change in changes}
        self.assertIn("XTS", codes)
        self.assertIn("USD", codes)
        self.assertNotIn(BASE_CURRENCY_CODE, codes, "el guaraní es la base y no se recotiza")

    def test_an_inactive_currency_is_left_alone(self):
        self.currency.is_active = False
        self.currency.save()
        _, changes = advance_one_day(rng=random.Random(3))
        self.assertNotIn("XTS", {change.currency.code for change in changes})

    def test_each_change_stays_within_minus_five_and_plus_seven_percent(self):
        for seed in range(25):
            SimulationState.objects.all().delete()
            _, changes = advance_one_day(rng=random.Random(seed))
            for change in changes:
                self.assertGreaterEqual(change.change_percent, MIN_DAILY_CHANGE * Decimal("100"))
                self.assertLessEqual(change.change_percent, MAX_DAILY_CHANGE * Decimal("100"))

    def test_the_buy_price_stays_below_the_sell_price(self):
        for seed in range(25):
            advance_one_day(rng=random.Random(seed))
        for currency in Currency.objects.exclude(code=BASE_CURRENCY_CODE):
            self.assertLess(currency.buy_rate, currency.sell_rate, f"{currency.code} invirtió sus precios")

    def test_advancing_adds_a_history_point_for_the_new_day(self):
        state, _ = advance_one_day(rng=random.Random(4))
        point = CurrencyRate.objects.get(currency=self.currency, date=state.current_date)
        self.currency.refresh_from_db()
        self.assertEqual(point.buy_rate, self.currency.buy_rate)
        self.assertEqual(point.sell_rate, self.currency.sell_rate)
        self.assertEqual(point.source, CurrencyRate.SIMULATION)

    def test_ten_days_leave_ten_history_points(self):
        for day in range(10):
            advance_one_day(rng=random.Random(day))
        self.assertEqual(
            CurrencyRate.objects.filter(currency=self.currency, source=CurrencyRate.SIMULATION).count(), 10
        )


class SimulationViewTests(TestCase):
    """Vista del panel de simulación."""

    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")
        self.admin = User.objects.create_user(username="sim_admin")
        self.admin.groups.add(Group.objects.get_or_create(name=role_lib.ADMIN)[0])
        self.client.force_login(self.admin)

    def test_the_button_advances_the_day(self):
        before = SimulationState.load().current_date
        response = self.client.post("/simulation/advance-day/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SimulationState.load().current_date, before + timedelta(days=1))

    def test_the_panel_shows_what_changed_after_advancing(self):
        response = self.client.post("/simulation/advance-day/", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Qué cambió en el último día simulado")
        self.assertContains(response, "USD")

    def test_the_summary_is_shown_only_once(self):
        self.client.post("/simulation/advance-day/", follow=True)
        response = self.client.get("/simulation/")
        self.assertNotContains(response, "Qué cambió en el último día simulado")

    def test_a_get_request_does_not_advance_the_day(self):
        before = SimulationState.load().current_date
        self.client.get("/simulation/advance-day/")
        self.assertEqual(SimulationState.load().current_date, before)


class CurrencyHistoryViewTests(TestCase):
    """Vista del historial de cotizaciones (pestaña Cotizaciones)."""

    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")
        self.usd = Currency.objects.get(code="USD")

    def test_the_default_view_picks_the_first_currency(self):
        response = self.client.get("/history/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["chart"].has_data)

    def test_a_currency_can_be_selected(self):
        response = self.client.get("/history/", {"currency": self.usd.id, "days": "30"})
        self.assertEqual(response.context["currency"], self.usd)
        self.assertLessEqual(len(response.context["rates"]), 30)

    def test_a_shorter_period_returns_fewer_points(self):
        week = self.client.get("/history/", {"currency": self.usd.id, "days": "7"})
        month = self.client.get("/history/", {"currency": self.usd.id, "days": "30"})
        self.assertLess(len(week.context["rates"]), len(month.context["rates"]))

    def test_the_table_is_ordered_newest_first(self):
        response = self.client.get("/history/", {"currency": self.usd.id, "days": "30"})
        dates = [point.date for point in response.context["rates"]]
        self.assertEqual(dates, sorted(dates, reverse=True))


class RateChartTests(TestCase):
    """core/services/charts.py — cálculo de los puntos del gráfico."""

    def setUp(self):
        self.currency = make_test_currency()
        self.day = SimulationState.load().current_date

    def _rates(self, count):
        return [
            CurrencyRate.objects.create(
                currency=self.currency,
                date=self.day - timedelta(days=offset),
                buy_rate=Decimal("7000") + offset * 10,
                sell_rate=Decimal("8000") + offset * 10,
            )
            for offset in reversed(range(count))
        ]

    def test_an_empty_history_produces_an_empty_chart(self):
        chart = build_rate_chart([])
        self.assertFalse(chart.has_data)
        self.assertEqual(chart.buy_points, "")

    def test_one_point_per_rate_in_each_series(self):
        chart = build_rate_chart(self._rates(5))
        self.assertTrue(chart.has_data)
        self.assertEqual(len(chart.buy_points.split(" ")), 5)
        self.assertEqual(len(chart.sell_points.split(" ")), 5)

    def test_a_flat_series_does_not_divide_by_zero(self):
        points = [
            CurrencyRate.objects.create(
                currency=self.currency,
                date=self.day - timedelta(days=offset),
                buy_rate=Decimal("7000"),
                sell_rate=Decimal("7000"),
            )
            for offset in reversed(range(3))
        ]
        chart = build_rate_chart(points)
        self.assertTrue(chart.has_data)

    def test_the_x_axis_is_labelled_without_crowding(self):
        chart = build_rate_chart(self._rates(30))
        self.assertLessEqual(len(chart.x_labels), 8)
        self.assertGreaterEqual(len(chart.x_labels), 2)


class CheckoutTests(TestCase):
    """Pantalla de pago: la compra se abre, se congela el precio y se paga o cancela.

    Divisas de prueba con números redondos:
    * XTS — compra 7000 / venta 8000 (la que se compra en casi todas las pruebas).
    * XTA — compra 4000 / venta 5000 (la que se usa para pagar con otra divisa).
    """

    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")
        self.user = User.objects.create_user(username="buyer")
        self.user.groups.add(Group.objects.get_or_create(name=role_lib.USER)[0])
        self.client.force_login(self.user)
        self.currency = make_test_currency()  # compra 7000 / venta 8000
        self.other = Currency.objects.create(
            code="XTA", name="Otra divisa de prueba", buy_rate=Decimal("4000"), sell_rate=Decimal("5000")
        )
        self.wallet = Wallet.objects.create(user=self.user)

    def _fund(self, pyg="8000000"):
        self.wallet.pyg_balance = Decimal(pyg)
        self.wallet.save()

    def _hold(self, currency, amount):
        self.wallet.holdings.update_or_create(currency=currency, defaults={"amount": Decimal(amount)})

    def _open(self, pyg_amount="8000000", currency=None):
        """Abre una orden por la pizarra y devuelve la respuesta y la orden creada."""
        currency = currency or self.currency
        response = self.client.post("/trade/", buy_post(currency, pyg_amount))
        return response, self.wallet.purchase_orders.order_by("-id").first()

    def _confirm(self, order, source="balance"):
        return self.client.post(f"/trade/checkout/{order.id}/confirm/", {"payment_source": source})

    def _holding(self, currency):
        holding = self.wallet.holdings.filter(currency=currency).first()
        return holding.amount if holding else Decimal("0")

    # --- Abrir la orden ---------------------------------------------------

    def test_the_buy_form_opens_a_pending_order_and_goes_to_the_payment_tab(self):
        self._fund()
        response, order = self._open()
        self.assertRedirects(response, f"/trade/checkout/{order.id}/?pay=balance")
        self.assertEqual(order.status, PurchaseOrder.PENDING)
        self.assertEqual(order.currency_amount, Decimal("1000.000000"))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.pyg_balance, Decimal("8000000.00"), "abrir la orden no cobra nada")
        self.assertEqual(self._holding(self.currency), Decimal("0"))

    def test_the_order_freezes_the_sell_rate_and_the_buy_rate_of_every_currency(self):
        _, order = self._open()
        self.assertEqual(order.rate, Decimal("8000"))
        self.assertEqual(order.frozen_buy_rate(self.other), Decimal("4000"))
        self.assertIn("USD", order.frozen_rates)
        self.assertNotIn(BASE_CURRENCY_CODE, order.frozen_rates)

    def test_the_order_expires_five_minutes_after_it_is_opened(self):
        _, order = self._open()
        self.assertEqual(PURCHASE_TIMEOUT, timedelta(minutes=5))
        self.assertAlmostEqual(
            (order.expires_at - order.created_at).total_seconds(), PURCHASE_TIMEOUT.total_seconds(), delta=2
        )

    def test_an_amount_that_buys_less_than_a_millionth_does_not_open_an_order(self):
        pricey = Currency.objects.create(
            code="XTP", name="Divisa carísima", buy_rate=Decimal("9000000"), sell_rate=Decimal("10000000")
        )
        response, order = self._open(pyg_amount="5", currency=pricey)  # 5 / 10.000.000 < 0,000001
        self.assertIsNone(order)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El monto es demasiado bajo")

    def test_opening_a_new_order_cancels_the_previous_pending_one(self):
        _, first = self._open()
        _, second = self._open(pyg_amount="16000")
        first.refresh_from_db()
        self.assertEqual(first.status, PurchaseOrder.CANCELLED)
        self.assertEqual(second.status, PurchaseOrder.PENDING)

    def test_the_trade_tab_links_back_to_a_pending_order(self):
        _, order = self._open()
        response = self.client.get("/trade/")
        self.assertEqual(response.context["pending_order"], order)
        self.assertContains(response, f'href="/trade/checkout/{order.id}/"')

    # --- Montos vinculados en la compra --------------------------------------

    def test_the_buyer_can_type_how_much_to_receive(self):
        response = self.client.post("/trade/", buy_post(self.currency, "100", anchor="out"))
        order = PurchaseOrder.objects.get(pk=order_id_from(response))
        self.assertEqual(order.currency_amount, Decimal("100.000000"), "se respeta lo que pidió")
        self.assertEqual(order.pyg_amount, Decimal("800000"))

    def test_guaranies_to_pay_are_rounded_up_when_typing_what_to_receive(self):
        response = self.client.post("/trade/", buy_post(self.currency, "0.0001", anchor="out"))
        order = PurchaseOrder.objects.get(pk=order_id_from(response))
        self.assertEqual(order.pyg_amount, Decimal("1"), "0,8 Gs. se cobra como 1 Gs.")

    def test_paying_with_a_held_currency_goes_to_checkout_with_it_selected(self):
        self._hold(self.other, "200")
        response = self.client.post("/trade/", buy_post(self.currency, "100", pay_currency=self.other))
        order = PurchaseOrder.objects.get(pk=order_id_from(response))
        # 100 XTA × 4000 (compra) = 400.000 Gs. / 8000 (venta) = 50 XTS
        self.assertEqual(order.pyg_amount, Decimal("400000"))
        self.assertEqual(order.currency_amount, Decimal("50.000000"))
        self.assertTrue(response.url.endswith(f"?pay=currency%3A{self.other.id}"))
        checkout_page = self.client.get(response.url)
        self.assertEqual(checkout_page.context["selected_key"], f"currency:{self.other.id}")
        self.client.post(f"/trade/checkout/{order.id}/confirm/", {"payment_source": f"currency:{self.other.id}"})
        self.assertEqual(self._holding(self.other), Decimal("100.000000"), "se debitaron justo los 100 XTA")
        self.assertEqual(self._holding(self.currency), Decimal("50.000000"))

    def test_only_guaranies_and_held_currencies_can_pay(self):
        self._hold(self.other, "5")
        form = self.client.get("/trade/").context["buy_form"]
        codes = sorted(c.code for c in form.fields["pay_currency"].queryset)
        self.assertEqual(codes, ["PYG", "XTA"])
        response = self.client.post("/trade/", buy_post(self.currency, "1", pay_currency=Currency.objects.get(code="USD")))
        self.assertIsNone(order_id_from(response))

    def test_paying_and_buying_the_same_currency_is_refused(self):
        self._hold(self.currency, "5")
        response = self.client.post("/trade/", buy_post(self.currency, "1", pay_currency=self.currency))
        self.assertIsNone(order_id_from(response))
        self.assertContains(response, "Elegí dos divisas distintas")

    def test_guaranies_to_pay_cannot_have_decimals(self):
        response = self.client.post("/trade/", buy_post(self.currency, "8000.5"))
        self.assertIsNone(order_id_from(response))
        self.assertContains(response, "sin decimales")

    def test_the_buy_form_without_javascript_uses_the_filled_amount(self):
        data = buy_post(self.currency, "100", anchor="out")
        del data["anchor"]
        order = PurchaseOrder.objects.get(pk=order_id_from(self.client.post("/trade/", data)))
        self.assertEqual(order.currency_amount, Decimal("100.000000"))

    def test_the_buy_form_needs_an_amount(self):
        data = buy_post(self.currency, "1")
        del data["pay_amount"]
        response = self.client.post("/trade/", data)
        self.assertIsNone(order_id_from(response))
        self.assertContains(response, "Escribí cuánto se entrega o cuánto se recibe")

    def test_the_buy_page_uses_the_linked_amounts_script(self):
        html = self.client.get("/trade/").content.decode()
        for marker in ('data-linked-amounts', 'data-value-step="1"', 'data-amount="in"', 'data-amount="out"',
                       '/static/js/linked-amounts.js'):
            self.assertIn(marker, html)
        self.assertNotIn("{#", html, "ningún comentario de plantilla debe verse en la página")

    # --- La pantalla de pago ----------------------------------------------

    def test_the_payment_tab_shows_every_payment_method_and_the_timer(self):
        self._fund()
        self._hold(self.other, "500")
        card = make_card(self.wallet)
        _, order = self._open()
        response = self.client.get(f"/trade/checkout/{order.id}/")
        self.assertEqual(response.status_code, 200)
        keys = [option.key for option in response.context["options"]]
        self.assertEqual(keys, ["balance", f"currency:{self.other.id}", f"card:{card.id}", "qr"])
        self.assertContains(response, 'id="timer-value"')
        self.assertContains(response, "Confirmar pago")
        self.assertContains(response, "Cancelar compra")
        self.assertContains(response, order.qr_reference)
        self.assertGreater(response.context["seconds_left"], 290)

    def test_the_currency_being_bought_is_not_offered_as_a_way_to_pay(self):
        self._hold(self.currency, "5")
        _, order = self._open()
        keys = [option.key for option in self.client.get(f"/trade/checkout/{order.id}/").context["options"]]
        self.assertNotIn(f"currency:{self.currency.id}", keys)

    def test_methods_that_do_not_cover_the_amount_are_shown_disabled(self):
        self._hold(self.other, "1")
        _, order = self._open()
        response = self.client.get(f"/trade/checkout/{order.id}/")
        options = {option.key: option for option in response.context["options"]}
        self.assertFalse(options["balance"].available)
        self.assertFalse(options[f"currency:{self.other.id}"].available)
        self.assertTrue(options["qr"].available)
        self.assertEqual(response.context["selected_key"], "qr", "se preselecciona el primer medio que alcanza")

    def test_without_cards_the_tab_points_to_the_wallet(self):
        _, order = self._open()
        self.assertContains(self.client.get(f"/trade/checkout/{order.id}/"), "Agregá una en tu billetera")

    def test_an_order_of_another_user_is_not_found(self):
        _, order = self._open()
        intruder = User.objects.create_user(username="intruder")
        intruder.groups.add(Group.objects.get_or_create(name=role_lib.USER)[0])
        other_client = Client(SERVER_NAME="localhost")
        other_client.force_login(intruder)
        self.assertEqual(other_client.get(f"/trade/checkout/{order.id}/").status_code, 404)
        self.assertEqual(other_client.post(f"/trade/checkout/{order.id}/confirm/", {"payment_source": "qr"}).status_code, 404)
        self.assertEqual(other_client.post(f"/trade/checkout/{order.id}/cancel/").status_code, 404)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.PENDING)

    # --- Pagar ------------------------------------------------------------

    def test_paying_with_the_balance(self):
        self._fund()
        _, order = self._open()
        response = self._confirm(order, "balance")
        self.assertRedirects(response, "/trade/")
        order.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.COMPLETED)
        self.assertEqual(order.payment_kind, PurchaseOrder.PAY_BALANCE)
        self.assertEqual(self.wallet.pyg_balance, Decimal("0.00"))
        self.assertEqual(self._holding(self.currency), Decimal("1000.000000"))
        purchase = self.wallet.transactions.get(kind=WalletTransaction.BUY)
        self.assertEqual(purchase.rate_used, Decimal("8000"))
        self.assertIn(f"#{order.id}", purchase.note)

    def test_paying_with_a_balance_that_is_too_low_is_refused(self):
        self._fund("100")
        _, order = self._open()
        response = self._confirm(order, "balance")
        self.assertRedirects(response, f"/trade/checkout/{order.id}/?pay=balance", fetch_redirect_response=False)
        order.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.PENDING, "la orden sigue abierta para elegir otro medio")
        self.assertEqual(self.wallet.pyg_balance, Decimal("100.00"))
        self.assertEqual(self._holding(self.currency), Decimal("0"))

    def test_paying_with_a_card_does_not_touch_the_balance(self):
        self._fund("50000")
        card = make_card(self.wallet)
        _, order = self._open()
        self._confirm(order, f"card:{card.id}")
        order.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(order.payment_kind, PurchaseOrder.PAY_CARD)
        self.assertEqual(order.payment_method, card)
        self.assertEqual(self.wallet.pyg_balance, Decimal("50000.00"))
        self.assertEqual(self._holding(self.currency), Decimal("1000.000000"))
        self.assertEqual(self.wallet.transactions.get(kind=WalletTransaction.BUY).payment_method, card)

    def test_an_expired_card_cannot_pay(self):
        card = make_card(self.wallet)
        card.expiry_year = 2001
        card.save()
        _, order = self._open()
        self._confirm(order, f"card:{card.id}")
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.PENDING)
        self.assertEqual(self._holding(self.currency), Decimal("0"))

    def test_a_card_of_another_user_cannot_pay(self):
        foreign = make_card(Wallet.objects.create(user=User.objects.create_user(username="victim")))
        _, order = self._open()
        self._confirm(order, f"card:{foreign.id}")
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.PENDING)
        self.assertEqual(self._holding(self.currency), Decimal("0"))

    def test_paying_with_another_currency_converts_it_to_guaranies_behind_the_scenes(self):
        # 700.000 Gs. de XTS (venta 8000) = 87,5 XTS. Se pagan con XTA, que la
        # casa de cambio compra a 4000: 700.000 / 4000 = 175 XTA.
        self._hold(self.other, "200")
        _, order = self._open(pyg_amount="700000")
        self._confirm(order, f"currency:{self.other.id}")
        order.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.COMPLETED)
        self.assertEqual(order.payment_kind, PurchaseOrder.PAY_CURRENCY)
        self.assertEqual(order.paid_currency, self.other)
        self.assertEqual(order.paid_amount, Decimal("175"))
        self.assertEqual(self._holding(self.other), Decimal("25.000000"))
        self.assertEqual(self._holding(self.currency), Decimal("87.500000"))
        self.assertEqual(self.wallet.pyg_balance, Decimal("0.00"), "la conversión no pasa por el saldo")
        conversion = self.wallet.transactions.get(kind=WalletTransaction.SELL)
        self.assertEqual(conversion.currency, self.other)
        self.assertEqual(conversion.pyg_amount, Decimal("700000"))
        self.assertEqual(conversion.rate_used, Decimal("4000"))

    def test_the_converted_amount_is_rounded_up_in_favour_of_the_house(self):
        _, order = self._open(pyg_amount="1000001")
        needed = checkout.required_amount(order, Decimal("7000"))
        self.assertEqual(needed, Decimal("142.857286"))
        self.assertGreaterEqual(needed * Decimal("7000"), order.pyg_amount)

    def test_not_enough_of_the_other_currency_is_refused(self):
        self._hold(self.other, "10")
        _, order = self._open(pyg_amount="700000")
        self._confirm(order, f"currency:{self.other.id}")
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.PENDING)
        self.assertEqual(self._holding(self.other), Decimal("10.000000"))

    def test_paying_with_the_mock_qr(self):
        self._fund("1000")
        _, order = self._open()
        self._confirm(order, "qr")
        order.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.COMPLETED)
        self.assertEqual(order.payment_kind, PurchaseOrder.PAY_QR)
        self.assertEqual(self.wallet.pyg_balance, Decimal("1000.00"))
        self.assertEqual(self._holding(self.currency), Decimal("1000.000000"))
        self.assertIn(order.qr_reference, self.wallet.transactions.get(kind=WalletTransaction.BUY).note)

    def test_an_unknown_payment_method_is_refused(self):
        _, order = self._open()
        self._confirm(order, "bitcoin")
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.PENDING)

    def test_a_get_request_does_not_pay(self):
        self._fund()
        _, order = self._open()
        response = self.client.get(f"/trade/checkout/{order.id}/confirm/")
        self.assertRedirects(response, f"/trade/checkout/{order.id}/")
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.PENDING)

    def test_confirming_twice_charges_only_once(self):
        self._fund("16000000")
        _, order = self._open()
        self._confirm(order, "balance")
        self._confirm(order, "balance")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.pyg_balance, Decimal("8000000.00"))
        self.assertEqual(self._holding(self.currency), Decimal("1000.000000"))
        self.assertEqual(self.wallet.transactions.filter(kind=WalletTransaction.BUY).count(), 1)

    def test_a_paid_order_shows_a_receipt(self):
        _, order = self._open()
        self._confirm(order, "qr")
        response = self.client.get(f"/trade/checkout/{order.id}/")
        self.assertContains(response, "Pagada")
        self.assertNotContains(response, "Confirmar pago")

    # --- Cancelar ---------------------------------------------------------

    def test_cancelling_moves_nothing(self):
        self._fund()
        _, order = self._open()
        response = self.client.post(f"/trade/checkout/{order.id}/cancel/")
        self.assertRedirects(response, "/trade/")
        order.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.CANCELLED)
        self.assertIsNotNone(order.closed_at)
        self.assertEqual(self.wallet.pyg_balance, Decimal("8000000.00"))
        self.assertFalse(self.wallet.transactions.exists())

    def test_a_cancelled_order_cannot_be_paid(self):
        self._fund()
        _, order = self._open()
        self.client.post(f"/trade/checkout/{order.id}/cancel/")
        self._confirm(order, "balance")
        order.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.CANCELLED)
        self.assertEqual(self.wallet.pyg_balance, Decimal("8000000.00"))
        self.assertContains(self.client.get(f"/trade/checkout/{order.id}/"), "Cancelaste esta compra")

    # --- Vencimiento ------------------------------------------------------

    def _age(self, order):
        """Hace que la orden haya vencido sin esperar cinco minutos de verdad."""
        PurchaseOrder.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(seconds=1))

    def test_an_expired_order_cannot_be_paid(self):
        self._fund()
        _, order = self._open()
        self._age(order)
        self._confirm(order, "balance")
        order.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.EXPIRED, "el vencimiento queda guardado aunque se rechace")
        self.assertEqual(self.wallet.pyg_balance, Decimal("8000000.00"))
        self.assertEqual(self._holding(self.currency), Decimal("0"))

    def test_viewing_an_expired_order_marks_it_expired(self):
        _, order = self._open()
        self._age(order)
        response = self.client.get(f"/trade/checkout/{order.id}/")
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.EXPIRED)
        self.assertContains(response, "Vencida")
        self.assertNotContains(response, "Confirmar pago")

    def test_an_expired_order_is_not_offered_on_the_trade_tab(self):
        _, order = self._open()
        self._age(order)
        self.assertIsNone(self.client.get("/trade/").context["pending_order"])

    def test_cancelling_after_the_deadline_records_it_as_expired(self):
        _, order = self._open()
        self._age(order)
        self.client.post(f"/trade/checkout/{order.id}/cancel/")
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.EXPIRED)

    def test_seconds_left_never_goes_negative(self):
        _, order = self._open()
        self._age(order)
        order.refresh_from_db()
        self.assertEqual(order.seconds_left(), 0)

    # --- Precio congelado -------------------------------------------------

    def test_a_rate_change_during_checkout_does_not_change_the_price(self):
        self._fund()
        _, order = self._open()
        self.currency.sell_rate = Decimal("10000")
        self.currency.buy_rate = Decimal("9000")
        self.currency.save()

        response = self.client.get(f"/trade/checkout/{order.id}/")
        self.assertTrue(response.context["rate_changed"])
        self.assertContains(response, 'id="rate-changed"')

        self._confirm(order, "balance")
        self.wallet.refresh_from_db()
        self.assertEqual(self._holding(self.currency), Decimal("1000.000000"), "a 10.000 serían sólo 800")
        self.assertEqual(self.wallet.pyg_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.transactions.get(kind=WalletTransaction.BUY).rate_used, Decimal("8000"))

    def test_the_conversion_rate_of_the_paying_currency_is_frozen_too(self):
        self._hold(self.other, "175")
        _, order = self._open(pyg_amount="700000")
        self.other.buy_rate = Decimal("2000")  # a este precio harían falta 350 XTA
        self.other.sell_rate = Decimal("3000")
        self.other.save()
        self._confirm(order, f"currency:{self.other.id}")
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.COMPLETED)
        self.assertEqual(self._holding(self.other), Decimal("0.000000"))

    def test_advancing_the_simulation_during_checkout_keeps_the_frozen_price(self):
        self._fund()
        _, order = self._open()
        advance_days(3)
        self.currency.refresh_from_db()
        self.assertNotEqual(self.currency.sell_rate, Decimal("8000"))
        self._confirm(order, "balance")
        self.assertEqual(self._holding(self.currency), Decimal("1000.000000"))


class MockQrTests(TestCase):
    """core/services/qr.py — el QR simulado de la pantalla de pago."""

    def test_the_same_reference_always_draws_the_same_code(self):
        self.assertEqual(mock_qr_path("GEX-ABC"), mock_qr_path("GEX-ABC"))

    def test_different_references_draw_different_codes(self):
        self.assertNotEqual(mock_qr_path("GEX-ABC")["path"], mock_qr_path("GEX-ABD")["path"])

    def test_the_three_position_squares_are_drawn(self):
        grid = mock_qr_modules("GEX-ABC")
        self.assertEqual(len(grid), MODULES)
        for top, left in ((0, 0), (0, MODULES - 7), (MODULES - 7, 0)):
            self.assertTrue(grid[top][left], "esquina oscura del cuadro de posición")
            self.assertFalse(grid[top + 1][left + 1], "anillo claro del cuadro de posición")
            self.assertTrue(grid[top + 3][left + 3], "centro oscuro del cuadro de posición")

    def test_the_countdown_filter_formats_minutes_and_seconds(self):
        self.assertEqual(countdown(300), "5:00")
        self.assertEqual(countdown(59), "0:59")
        self.assertEqual(countdown(-4), "0:00")


def make_cashier(username="cajero1", *roles):
    user = User.objects.create_user(username=username, first_name="Carla", last_name="Caja")
    for role in roles or (role_lib.CASHIER,):
        user.groups.add(Group.objects.get_or_create(name=role)[0])
    return user


class CashierRoleTests(TestCase):
    """El rol cajero: acceso a la pestaña Caja y alta del rol en Keycloak."""

    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")

    def test_cajero_is_a_known_role(self):
        self.assertIn(role_lib.CASHIER, role_lib.ALL_ROLES)
        self.assertEqual(role_lib.ROLE_LABELS[role_lib.CASHIER], "Cajero")
        claims = {"realm_access": {"roles": ["cajero", "offline_access"]}}
        self.assertEqual(role_lib.roles_from_claims(claims), {"cajero"})

    def test_a_cashier_and_an_admin_can_operate_the_desk(self):
        self.assertTrue(role_lib.is_cashier(make_cashier()))
        self.assertTrue(role_lib.is_cashier(make_cashier("jefa", role_lib.ADMIN)))
        self.assertFalse(role_lib.is_cashier(make_cashier("cliente", role_lib.USER)))
        self.assertFalse(role_lib.is_cashier(make_cashier("gestor", role_lib.MANAGER)))

    def test_the_caja_tab_is_shown_only_to_who_can_use_it(self):
        self.client.force_login(make_cashier())
        html = self.client.get("/dashboard/").content.decode()
        self.assertIn('href="/caja/"', html)
        self.assertNotIn("Mi billetera", html, "un cajero no opera una billetera propia")

        self.client.force_login(make_cashier("cliente", role_lib.USER))
        self.assertNotIn('href="/caja/"', self.client.get("/dashboard/").content.decode())

    def test_other_roles_cannot_enter_the_caja(self):
        for role in (role_lib.USER, role_lib.MANAGER):
            self.client.force_login(make_cashier(f"no-{role}", role))
            self.assertEqual(self.client.get("/caja/").status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get("/caja/").status_code, 302, "sin sesión se pide iniciar sesión")

    def test_assigning_a_role_missing_in_keycloak_creates_it_first(self):
        """El realm ya importado no tiene "cajero": hay que crearlo al asignarlo."""
        client = KeycloakAdminClient()
        client._token = "token"
        created = {"name": "cajero", "id": "role-id"}
        responses = {
            ("GET", "/roles/cajero"): [mock.Mock(status_code=404, text="not found")],
        }

        def fake_get(url, **kwargs):
            return responses[("GET", "/roles/cajero")].pop(0)

        calls = []

        def fake_request(method, url, ok=(200,), **kwargs):
            calls.append((method, url.rsplit("/admin/realms/", 1)[-1], kwargs.get("json")))
            response = mock.Mock(status_code=ok[0])
            response.json.return_value = created if method == "GET" else []
            return response

        with mock.patch.object(client.session, "get", side_effect=fake_get), \
                mock.patch.object(client, "_request", side_effect=fake_request):
            role = client.ensure_realm_role("cajero")

        self.assertEqual(role, created)
        post = [call for call in calls if call[0] == "POST"]
        self.assertEqual(len(post), 1)
        self.assertTrue(post[0][1].endswith("/roles"))
        self.assertEqual(post[0][2]["name"], "cajero")

    def test_an_existing_role_is_not_created_again(self):
        client = KeycloakAdminClient()
        client._token = "token"
        existing = mock.Mock(status_code=200)
        existing.json.return_value = {"name": "cajero"}
        with mock.patch.object(client.session, "get", return_value=existing), \
                mock.patch.object(client, "_request") as request:
            self.assertEqual(client.ensure_realm_role("cajero"), {"name": "cajero"})
        request.assert_not_called()


class CashDeskTests(TestCase):
    """Caja física: apertura, operaciones de ventanilla, cierre y reporte.

    Divisas de prueba con números redondos:
    * XTS — compra 7000 / venta 8000.
    * XTA — compra 4000 / venta 5000.
    """

    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")
        self.user = make_cashier()
        self.client.force_login(self.user)
        self.pyg = Currency.objects.get(code=BASE_CURRENCY_CODE)
        self.xts = make_test_currency()
        self.xta = Currency.objects.create(
            code="XTA", name="Otra divisa de prueba", buy_rate=Decimal("4000"), sell_rate=Decimal("5000")
        )

    # --- Ayudantes --------------------------------------------------------

    def _open(self, pyg="10000000", xts="5000", xta="0", **extra):
        data = {"amount_PYG": pyg, "amount_XTS": xts, "amount_XTA": xta}
        data.update(extra)
        return self.client.post("/caja/abrir/", data)

    def _session(self):
        return CashSession.objects.get(cashier=self.user, status=CashSession.OPEN)

    def _cash(self, currency, session=None):
        balance = (session or self._session()).balances.filter(currency=currency).first()
        return balance.current_amount if balance else Decimal("0")

    def _quote(self, currency_in, currency_out, amount, anchor="out"):
        """Cotiza una operación; ``amount`` es lo que recibe (``out``) o entrega (``in``) el cliente."""
        data = {"currency_in": currency_in.id, "currency_out": currency_out.id, "anchor": anchor}
        data["amount_out" if anchor == "out" else "amount_in"] = amount
        response = self.client.post("/caja/operaciones/nueva/", data)
        tx = CashTransaction.objects.order_by("-id").first()
        return response, tx

    VISA = {"card_number": "4111 1111 1111 1111", "card_holder": "JUAN PEREZ", "card_expiry": "12/30", "card_cvv": "123"}

    def _confirm(self, tx, method="cash", name="Juan Pérez", document="4.567.890", **card):
        data = {"payment_method": method, "customer_name": name, "customer_document": document}
        if method == "card":
            data.update({**self.VISA, **card})
        return self.client.post(f"/caja/operaciones/{tx.id}/confirmar/", data)

    # --- Apertura ---------------------------------------------------------

    def test_without_a_session_the_tab_asks_for_the_opening_cash(self):
        response = self.client.get("/caja/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Abrir caja")
        codes = [currency.code for currency, _ in response.context["open_form"].currency_fields()]
        self.assertEqual(codes[0], "PYG", "el guaraní va primero")
        self.assertIn("XTS", codes)

    def test_opening_records_the_starting_cash_of_each_currency(self):
        response = self._open(pyg="10000000", xts="5000", xta="0")
        self.assertRedirects(response, "/caja/")
        session = self._session()
        self.assertEqual(session.cashier_name, "Carla Caja")
        balance = session.balances.get(currency=self.pyg)
        self.assertEqual(balance.opening_amount, Decimal("10000000"))
        self.assertEqual(balance.current_amount, Decimal("10000000"))
        self.assertEqual(self._cash(self.xts), Decimal("5000"))
        self.assertEqual(self._cash(self.xta), Decimal("0"))

    def test_opening_with_no_cash_at_all_is_refused(self):
        response = self._open(pyg="0", xts="0", xta="0")
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "al menos una divisa", status_code=400)
        self.assertFalse(CashSession.objects.exists())

    def test_guaranies_cannot_have_decimals_and_foreign_cash_has_cents(self):
        self.assertEqual(self._open(pyg="1000.5").status_code, 400)
        self.assertEqual(self._open(xts="10.555").status_code, 400)
        self.assertFalse(CashSession.objects.exists())
        self.assertRedirects(self._open(xts="10.55"), "/caja/")

    def test_negative_opening_cash_is_refused(self):
        self.assertEqual(self._open(xts="-5").status_code, 400)

    def test_a_cashier_cannot_have_two_open_sessions(self):
        self._open()
        with self.assertRaises(cashier.CashierError):
            cashier.open_session(self.user, {self.pyg: Decimal("1")})
        self.assertEqual(CashSession.objects.filter(cashier=self.user).count(), 1)

    def test_an_admin_can_open_a_session_too(self):
        admin = make_cashier("jefa", role_lib.ADMIN)
        self.client.force_login(admin)
        self._open()
        self.assertTrue(CashSession.objects.filter(cashier=admin, status=CashSession.OPEN).exists())

    def test_with_a_session_the_tab_shows_the_cash_and_the_quote_form(self):
        self._open()
        response = self.client.get("/caja/")
        self.assertContains(response, "Efectivo en caja")
        self.assertContains(response, "Nueva operación")
        self.assertContains(response, 'href="/caja/cerrar/"')
        form = response.context["quote_form"]
        self.assertEqual(form.initial["currency_in"], self.pyg.pk, "lo habitual: el cliente paga en guaraníes")

    # --- Cotizar ----------------------------------------------------------

    def test_quoting_opens_the_operation_tab_without_moving_cash(self):
        self._open()
        response, tx = self._quote(self.pyg, self.xts, "100")
        self.assertRedirects(response, f"/caja/operaciones/{tx.id}/")
        self.assertEqual(tx.status, CashTransaction.PENDING)
        self.assertEqual(tx.amount_out, Decimal("100.00"))
        self.assertEqual(tx.amount_in, Decimal("800000"), "100 XTS a 8000 (precio de venta)")
        self.assertEqual(tx.description, "El cliente compra XTS")
        self.assertEqual(self._cash(self.pyg), Decimal("10000000"))
        self.assertEqual(self._cash(self.xts), Decimal("5000"))

    def test_the_quote_form_has_two_linked_amounts_each_with_its_currency(self):
        self._open()
        response = self.client.get("/caja/")
        form = response.context["quote_form"]
        self.assertEqual(form.fields["amount_in"].label, "Monto que entrega el cliente")
        self.assertEqual(form.fields["amount_out"].label, "Monto que recibe el cliente")
        self.assertNotIn("side", form.fields, "ya no hay selector de lado: se escribe en uno de los dos campos")
        html = response.content.decode()
        for marker in ('data-linked-amounts', 'data-amount="in"', 'data-amount="out"',
                       'data-currency="in"', 'data-currency="out"', 'data-anchor', '/static/js/linked-amounts.js'):
            self.assertIn(marker, html)
        table = response.context["rate_table"]
        self.assertEqual(table[str(self.xts.id)]["decimals"], 2, "en caja las divisas van en centavos")
        self.assertEqual(table[str(self.pyg.id)]["decimals"], 0)

    def test_the_cashier_can_type_what_the_customer_hands_over(self):
        self._open()
        _, tx = self._quote(self.pyg, self.xts, "1000001", anchor="in")
        self.assertEqual(tx.amount_in, Decimal("1000001"), "se respeta lo que escribió el cajero")
        self.assertEqual(tx.amount_out, Decimal("125.00"), "125,000125 se redondea hacia abajo a centavos")

    def test_what_the_customer_hands_over_must_buy_at_least_a_cent(self):
        self._open()
        response, tx = self._quote(self.pyg, self.xts, "50", anchor="in")  # 50 / 8000 = 0,006 XTS
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "demasiado bajo", status_code=400)
        self.assertIsNone(tx)

    def test_the_server_recomputes_from_the_anchor_and_ignores_the_other_field(self):
        self._open()
        self.client.post(
            "/caja/operaciones/nueva/",
            {"currency_in": self.pyg.id, "amount_in": "1", "currency_out": self.xts.id, "amount_out": "100",
             "anchor": "out"},
        )
        tx = CashTransaction.objects.latest("id")
        self.assertEqual(tx.amount_in, Decimal("800000"), "el monto manipulado del otro lado no cuenta")

    def test_without_javascript_the_filled_amount_is_used(self):
        self._open()
        self.client.post(
            "/caja/operaciones/nueva/",
            {"currency_in": self.pyg.id, "amount_in": "800000", "currency_out": self.xts.id, "amount_out": ""},
        )
        self.assertEqual(CashTransaction.objects.latest("id").amount_out, Decimal("100.00"))

    def test_a_quote_needs_one_of_the_two_amounts(self):
        self._open()
        response = self.client.post(
            "/caja/operaciones/nueva/", {"currency_in": self.pyg.id, "currency_out": self.xts.id}
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Escribí cuánto se entrega o cuánto se recibe", status_code=400)

    def test_a_customer_selling_currency_gets_the_buy_price(self):
        self._open()
        _, tx = self._quote(self.xts, self.pyg, "700000")
        # 700.000 Gs. / 7000 (precio de compra) = 100 XTS
        self.assertEqual(tx.amount_in, Decimal("100.00"))
        self.assertEqual(tx.description, "El cliente vende XTS")

    def test_exchanging_two_foreign_currencies_goes_through_guaranies(self):
        self._open()
        _, tx = self._quote(self.xta, self.xts, "50")
        # 50 XTS × 8000 (venta) = 400.000 Gs. / 4000 (compra) = 100 XTA
        self.assertEqual(tx.amount_in, Decimal("100.00"))
        self.assertEqual(tx.pyg_value, Decimal("400000.00"))
        self.assertEqual(tx.description, "Cambio XTA → XTS")

    def test_what_the_customer_pays_is_rounded_up_in_favour_of_the_house(self):
        self._open()
        odd = Currency.objects.create(code="XTB", name="Impar", buy_rate=Decimal("3000"), sell_rate=Decimal("3500"))
        _, tx = self._quote(odd, self.xts, "10")
        # 10 XTS × 8000 = 80.000 Gs. / 3000 = 26,666… XTB -> 26,67
        self.assertEqual(tx.amount_in, Decimal("26.67"))
        self.assertGreaterEqual(tx.amount_in * tx.rate_in, tx.amount_out * tx.rate_out)

    def test_a_quote_the_drawer_cannot_pay_out_is_refused(self):
        self._open(xts="50")
        response, tx = self._quote(self.pyg, self.xts, "100")
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "No hay suficiente efectivo", status_code=400)
        self.assertIsNone(tx)

    def test_the_same_currency_on_both_sides_is_refused(self):
        self._open()
        response, tx = self._quote(self.xts, self.xts, "10")
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(tx)

    def test_foreign_cash_with_more_than_two_decimals_is_refused(self):
        self._open()
        response, tx = self._quote(self.pyg, self.xts, "10.005")
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(tx)

    def test_quoting_without_an_open_session_is_refused(self):
        response, tx = self._quote(self.pyg, self.xts, "10")
        self.assertRedirects(response, "/caja/")
        self.assertIsNone(tx)

    def test_a_new_quote_discards_the_previous_pending_one(self):
        self._open()
        _, first = self._quote(self.pyg, self.xts, "10")
        _, second = self._quote(self.pyg, self.xts, "20")
        first.refresh_from_db()
        self.assertEqual(first.status, CashTransaction.CANCELLED)
        self.assertEqual(second.status, CashTransaction.PENDING)
        self.assertEqual(self.client.get("/caja/").context["pending_tx"], second)

    # --- La pestaña de la operación ---------------------------------------

    def test_the_operation_tab_offers_cash_card_and_qr_and_asks_for_the_customer(self):
        self._open()
        _, tx = self._quote(self.pyg, self.xts, "100")
        response = self.client.get(f"/caja/operaciones/{tx.id}/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        for value in ("cash", "card", "qr"):
            self.assertIn(f'name="payment_method" value="{value}"', html)
        self.assertIn('name="customer_name"', html)
        self.assertIn('name="customer_document"', html)
        self.assertIn(tx.qr_reference, html)
        self.assertIn("/static/js/payment-tab.js", html)
        self.assertGreater(response.context["seconds_left"], 290)

    def test_paying_in_cash_moves_both_sides_of_the_drawer(self):
        self._open()
        _, tx = self._quote(self.pyg, self.xts, "100")
        response = self._confirm(tx, "cash", name="  Juan   Pérez ", document="4.567.890")
        self.assertRedirects(response, f"/caja/operaciones/{tx.id}/")
        tx.refresh_from_db()
        self.assertEqual(tx.status, CashTransaction.COMPLETED)
        self.assertEqual(tx.customer_name, "Juan Pérez")
        self.assertEqual(tx.customer_document, "4567890")
        self.assertEqual(self._cash(self.pyg), Decimal("10800000"), "entró lo que pagó el cliente")
        self.assertEqual(self._cash(self.xts), Decimal("4900"), "salió lo que recibió")

    def test_paying_by_card_only_gives_cash_out(self):
        self._open()
        _, tx = self._quote(self.pyg, self.xts, "100")
        self._confirm(tx, "card")
        tx.refresh_from_db()
        self.assertEqual(tx.payment_method, CashTransaction.PAY_CARD)
        self.assertEqual(tx.card_last4, "1111")
        self.assertEqual(tx.card_brand, "visa")
        self.assertEqual(tx.card_holder, "JUAN PEREZ")
        self.assertEqual(self._cash(self.pyg), Decimal("10000000"))
        self.assertEqual(self._cash(self.xts), Decimal("4900"))

    def test_a_card_payment_needs_every_card_detail(self):
        self._open()
        _, tx = self._quote(self.pyg, self.xts, "100")
        bad_cards = [
            {"card_number": ""},
            {"card_number": "4111 1111 1111 1112"},  # no pasa Luhn
            {"card_holder": ""},
            {"card_expiry": ""},
            {"card_expiry": "13/30"},
            {"card_expiry": "01/20"},  # vencida
            {"card_cvv": ""},
            {"card_cvv": "12"},
            {"card_cvv": "12a"},
            {"card_number": "3782 822463 10005", "card_cvv": "123"},  # Amex pide 4 dígitos
        ]
        for bad in bad_cards:
            response = self._confirm(tx, "card", **bad)
            self.assertEqual(response.status_code, 400, bad)
        tx.refresh_from_db()
        self.assertEqual(tx.status, CashTransaction.PENDING)
        self.assertEqual(self._cash(self.xts), Decimal("5000"))

    def test_an_amex_takes_a_four_digit_code(self):
        self._open()
        _, tx = self._quote(self.pyg, self.xts, "100")
        self._confirm(tx, "card", card_number="3782 822463 10005", card_cvv="1234", card_expiry="07/2031")
        tx.refresh_from_db()
        self.assertEqual(tx.status, CashTransaction.COMPLETED)
        self.assertEqual(tx.card_brand, "amex")
        self.assertEqual(tx.card_last4, "0005")

    def test_the_full_card_number_and_security_code_are_never_stored(self):
        self._open()
        _, tx = self._quote(self.pyg, self.xts, "100")
        self._confirm(tx, "card", card_cvv="987")
        stored = " ".join(str(value) for value in CashTransaction.objects.filter(pk=tx.pk).values().get().values())
        self.assertNotIn("4111111111111111", stored)
        self.assertNotIn("4111 1111 1111 1111", stored)
        self.assertNotIn("987", stored)
        receipt = self.client.get(f"/caja/operaciones/{tx.id}/").content.decode()
        self.assertNotIn("4111 1111 1111 1111", receipt)
        self.assertIn("Visa •••• 1111", receipt)

    def test_a_rejected_card_form_does_not_echo_the_security_code(self):
        self._open()
        _, tx = self._quote(self.pyg, self.xts, "100")
        response = self._confirm(tx, "card", card_cvv="987", card_holder="")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('value="987"', response.content.decode())

    def test_paying_with_qr_only_gives_cash_out(self):
        self._open()
        _, tx = self._quote(self.pyg, self.xts, "100")
        self.client.post(
            f"/caja/operaciones/{tx.id}/confirmar/",
            {"payment_method": "qr", "customer_name": "Juan Pérez", "customer_document": "4567890", **self.VISA},
        )
        tx.refresh_from_db()
        self.assertEqual(tx.payment_method, CashTransaction.PAY_QR)
        self.assertEqual(
            (tx.card_brand, tx.card_holder, tx.card_last4), ("", "", ""),
            "los datos de tarjeta sólo se guardan si se pagó con tarjeta",
        )
        self.assertEqual(self._cash(self.pyg), Decimal("10000000"))
        self.assertEqual(self._cash(self.xts), Decimal("4900"))

    def test_cash_in_a_currency_not_declared_at_opening_gets_its_own_balance(self):
        self._open(xta="0")
        brl = Currency.objects.get(code="BRL")
        CashSessionBalance.objects.filter(session=self._session(), currency=brl).delete()
        _, tx = self._quote(brl, self.pyg, "100000")
        self._confirm(tx, "cash")
        self.assertEqual(self._cash(brl), tx.amount_in)
        self.assertGreater(tx.amount_in, 0)

    def test_the_identity_card_is_validated(self):
        self._open()
        _, tx = self._quote(self.pyg, self.xts, "100")
        for bad in ("ABC123", "123", "1234567890"):
            response = self._confirm(tx, document=bad)
            self.assertEqual(response.status_code, 400, bad)
        self.assertEqual(self._confirm(tx, name="", document="1234567").status_code, 400)
        tx.refresh_from_db()
        self.assertEqual(tx.status, CashTransaction.PENDING)

    def test_an_expired_quote_cannot_be_executed(self):
        self._open()
        _, tx = self._quote(self.pyg, self.xts, "100")
        CashTransaction.objects.filter(pk=tx.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        response = self._confirm(tx)
        self.assertEqual(response.status_code, 400)
        tx.refresh_from_db()
        self.assertEqual(tx.status, CashTransaction.EXPIRED)
        self.assertEqual(self._cash(self.xts), Decimal("5000"))
        self.assertContains(self.client.get(f"/caja/operaciones/{tx.id}/"), "Vencida")

    def test_the_quote_is_frozen_if_rates_change(self):
        self._open()
        _, tx = self._quote(self.pyg, self.xts, "100")
        self.xts.sell_rate = Decimal("9000")
        self.xts.buy_rate = Decimal("8500")
        self.xts.save()
        self.assertTrue(self.client.get(f"/caja/operaciones/{tx.id}/").context["rate_changed"])
        self._confirm(tx, "cash")
        self.assertEqual(self._cash(self.pyg), Decimal("10800000"), "se cobró a 8000, no a 9000")

    def test_the_drawer_is_checked_again_when_confirming(self):
        self._open()
        _, tx = self._quote(self.pyg, self.xts, "100")
        CashSessionBalance.objects.filter(currency=self.xts).update(current_amount=Decimal("50"))
        response = self._confirm(tx)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "No hay suficiente efectivo", status_code=400)
        tx.refresh_from_db()
        self.assertEqual(tx.status, CashTransaction.PENDING)
        self.assertEqual(self._cash(self.xts), Decimal("50"))

    def test_confirming_twice_moves_cash_once(self):
        self._open()
        _, tx = self._quote(self.pyg, self.xts, "100")
        self._confirm(tx)
        self._confirm(tx)
        self.assertEqual(self._cash(self.xts), Decimal("4900"))

    def test_a_get_request_does_not_execute(self):
        self._open()
        _, tx = self._quote(self.pyg, self.xts, "100")
        self.assertRedirects(self.client.get(f"/caja/operaciones/{tx.id}/confirmar/"), f"/caja/operaciones/{tx.id}/")
        tx.refresh_from_db()
        self.assertEqual(tx.status, CashTransaction.PENDING)

    def test_cancelling_moves_nothing(self):
        self._open()
        _, tx = self._quote(self.pyg, self.xts, "100")
        self.assertRedirects(self.client.post(f"/caja/operaciones/{tx.id}/cancelar/"), "/caja/")
        tx.refresh_from_db()
        self.assertEqual(tx.status, CashTransaction.CANCELLED)
        self.assertEqual(self._cash(self.xts), Decimal("5000"))

    def test_the_receipt_shows_the_customer_and_both_amounts(self):
        self._open()
        _, tx = self._quote(self.pyg, self.xts, "100")
        self._confirm(tx, "card", card_number="4242 4242 4242 4242")
        response = self.client.get(f"/caja/operaciones/{tx.id}/")
        self.assertContains(response, "Comprobante de operación")
        self.assertContains(response, "4.567.890")
        self.assertContains(response, "Gs. 800.000")
        self.assertContains(response, "100,00 XTS")
        self.assertContains(response, "Visa •••• 4242")
        self.assertContains(response, "titular JUAN PEREZ")
        self.assertNotContains(response, "Confirmar operación")

    def test_another_cashier_cannot_touch_the_operation(self):
        self._open()
        _, tx = self._quote(self.pyg, self.xts, "100")
        self.client.force_login(make_cashier("otro"))
        self.assertEqual(self.client.get(f"/caja/operaciones/{tx.id}/").status_code, 404)
        self.assertEqual(self._confirm(tx).status_code, 404)
        self.assertEqual(self.client.post(f"/caja/operaciones/{tx.id}/cancelar/").status_code, 404)
        tx.refresh_from_db()
        self.assertEqual(tx.status, CashTransaction.PENDING)

    def test_an_admin_can_look_at_another_cashiers_operation_but_not_complete_it(self):
        self._open()
        _, tx = self._quote(self.pyg, self.xts, "100")
        self.client.force_login(make_cashier("jefa", role_lib.ADMIN))
        response = self.client.get(f"/caja/operaciones/{tx.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Confirmar operación")
        self.assertEqual(self._confirm(tx).status_code, 404)

    # --- Cierre y reporte -------------------------------------------------

    def _busy_day(self):
        """Apertura y tres operaciones: efectivo, tarjeta y QR."""
        self._open(pyg="10000000", xts="5000", xta="1000")
        _, tx = self._quote(self.pyg, self.xts, "100")  # entra 800.000 Gs., salen 100 XTS
        self._confirm(tx, "cash", name="Ana Uno", document="1111111")
        _, tx = self._quote(self.xts, self.pyg, "70000")  # entrega 10 XTS con tarjeta, salen 70.000 Gs.
        self._confirm(
            tx, "card", name="Beto Dos", document="2222222", card_number="5555 5555 5555 4444", card_holder="BETO DOS"
        )
        _, tx = self._quote(self.pyg, self.xta, "20")  # QR, salen 20 XTA
        self._confirm(tx, "qr", name="Ceci Tres", document="3333333")
        return self._session()

    def test_the_close_page_previews_the_report_and_warns_about_a_pending_quote(self):
        self._busy_day()
        self._quote(self.pyg, self.xts, "1")
        response = self.client.get("/caja/cerrar/")
        self.assertContains(response, "¿Cerrar la caja?")
        self.assertContains(response, 'id="close-pending-warning"')
        self.assertContains(response, "Ana Uno")

    def test_closing_freezes_the_final_cash_and_cancels_what_was_pending(self):
        session = self._busy_day()
        _, pending = self._quote(self.pyg, self.xts, "1")
        response = self.client.post("/caja/cerrar/")
        self.assertRedirects(response, f"/caja/sesiones/{session.id}/")
        session.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(session.status, CashSession.CLOSED)
        self.assertIsNotNone(session.closed_at)
        self.assertEqual(pending.status, CashTransaction.CANCELLED)
        closing = {b.currency.code: b.closing_amount for b in session.balances.select_related("currency")}
        self.assertEqual(closing["PYG"], Decimal("10730000"))  # +800.000 −70.000
        self.assertEqual(closing["XTS"], Decimal("4900"))
        self.assertEqual(closing["XTA"], Decimal("980"))

    def test_after_closing_no_more_operations_and_a_new_session_can_start(self):
        self._busy_day()
        self.client.post("/caja/cerrar/")
        response, tx = self._quote(self.pyg, self.xts, "1")
        self.assertRedirects(response, "/caja/")
        self.assertEqual(CashTransaction.objects.filter(status=CashTransaction.PENDING).count(), 0)
        self._open()
        self.assertEqual(CashSession.objects.filter(cashier=self.user).count(), 2)

    def test_the_report_has_the_starting_and_ending_state_and_every_operation(self):
        session = self._busy_day()
        self.client.post("/caja/cerrar/")
        report = self.client.get(f"/caja/sesiones/{session.id}/").context["report"]
        rows = {row.currency.code: row for row in report.currencies}
        self.assertEqual(report.currencies[0].currency.code, "PYG")
        self.assertEqual(rows["PYG"].opening, Decimal("10000000"))
        self.assertEqual(rows["PYG"].cash_in, Decimal("800000"))
        self.assertEqual(rows["PYG"].cash_out, Decimal("70000"))
        self.assertEqual(rows["PYG"].closing, Decimal("10730000"))
        self.assertEqual(rows["PYG"].qr_in, Decimal("100000"), "20 XTA × 5000 cobrados por QR")
        self.assertEqual(rows["XTS"].card_in, Decimal("10"))
        for row in report.currencies:
            self.assertEqual(row.closing, row.expected, f"{row.currency.code} debe cuadrar")
        self.assertEqual([tx.customer_name for tx in report.transactions], ["Ana Uno", "Beto Dos", "Ceci Tres"])

        html = self.client.get(f"/caja/sesiones/{session.id}/").content.decode()
        for text in ("Estado inicial y final", "Cobros que no entran a la caja", "Beto Dos", "2.222.222", "Cerrada"):
            self.assertIn(text, html)
        self.assertNotIn("No cuadra", html)

    def test_an_open_session_has_a_partial_report(self):
        session = self._busy_day()
        response = self.client.get(f"/caja/sesiones/{session.id}/")
        self.assertContains(response, "reporte parcial")
        self.assertEqual(response.context["report"].currencies[0].closing, Decimal("10730000"))

    def test_the_report_downloads_as_csv(self):
        session = self._busy_day()
        self.client.post("/caja/cerrar/")
        response = self.client.get(f"/caja/sesiones/{session.id}/csv/")
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn(f"caja-{session.id}.csv", response["Content-Disposition"])
        text = response.content.decode("utf-8-sig")
        self.assertIn("Cajero;Carla Caja", text)
        self.assertIn("PYG;10000000;800000;70000;10730000", text)
        self.assertIn("Ana Uno;1111111", text)
        self.assertIn("Tarjeta de crédito Mastercard •••• 4444 (BETO DOS)", text)

    def test_only_the_cashier_or_an_admin_can_read_a_report(self):
        session = self._busy_day()
        self.client.force_login(make_cashier("otro"))
        self.assertEqual(self.client.get(f"/caja/sesiones/{session.id}/").status_code, 403)
        self.assertEqual(self.client.get(f"/caja/sesiones/{session.id}/csv/").status_code, 403)
        self.client.force_login(make_cashier("jefa", role_lib.ADMIN))
        self.assertEqual(self.client.get(f"/caja/sesiones/{session.id}/").status_code, 200)

    def test_the_desk_lists_past_sessions_and_admins_see_everyone(self):
        session = self._busy_day()
        self.client.post("/caja/cerrar/")
        history = list(self.client.get("/caja/").context["history"])
        self.assertEqual(history, [session])
        self.assertEqual(history[0].done_count, 3)

        self.client.force_login(make_cashier("otro"))
        self.assertEqual(list(self.client.get("/caja/").context["history"]), [])
        self.client.force_login(make_cashier("jefa", role_lib.ADMIN))
        response = self.client.get("/caja/")
        self.assertIn(session, list(response.context["history"]))
        self.assertContains(response, "Carla Caja")

    def test_the_cash_filters(self):
        self.assertEqual(cash(Decimal("1500000"), self.pyg), "Gs. 1.500.000")
        self.assertEqual(cash(Decimal("250"), self.xts), "250,00 XTS")
        self.assertEqual(cedula("4567890"), "4.567.890")
        self.assertEqual(subtract(Decimal("10"), Decimal("4")), Decimal("6"))


class PricingTests(TestCase):
    """core/services/pricing.py — la cuenta que comparten la compra en línea y la caja."""

    def _convert(self, anchor, amount, **kwargs):
        defaults = dict(
            rate_in=Decimal("1"), rate_out=Decimal("8000"),
            quantum_in=Decimal("1"), quantum_out=Decimal("0.01"),
        )
        defaults.update(kwargs)
        return pricing.convert(anchor=anchor, amount=Decimal(amount), **defaults)

    def test_typing_what_is_received_rounds_what_is_paid_up(self):
        result = self._convert("out", "10", rate_in=Decimal("3000"), quantum_in=Decimal("0.01"))
        # 10 × 8000 = 80.000 Gs. / 3000 = 26,666… -> 26,67
        self.assertEqual(result.amount_in, Decimal("26.67"))
        self.assertEqual(result.amount_out, Decimal("10"))

    def test_typing_what_is_paid_rounds_what_is_received_down(self):
        result = self._convert("in", "1000001")
        self.assertEqual(result.amount_out, Decimal("125.00"))
        self.assertEqual(result.value, Decimal("1000001"))

    def test_the_value_can_be_rounded_to_whole_guaranies(self):
        down = self._convert("in", "1", rate_in=Decimal("7190.5"), quantum_in=Decimal("0.000001"),
                             quantum_out=Decimal("0.000001"), value_quantum=Decimal("1"))
        self.assertEqual(down.value, Decimal("7190"), "lo que paga el cliente vale hacia abajo")
        up = self._convert("out", "1", rate_out=Decimal("7409.5"), value_quantum=Decimal("1"))
        self.assertEqual(up.value, Decimal("7410"), "lo que recibe cuesta hacia arriba")

    def test_an_unknown_anchor_is_an_error(self):
        with self.assertRaises(ValueError):
            self._convert("sideways", "1")

    def test_the_rate_table_gives_guaranies_a_rate_of_one_and_no_decimals(self):
        pyg = Currency.objects.get(code=BASE_CURRENCY_CODE)
        usd = Currency.objects.get(code="USD")
        table = pricing.rate_table([pyg, usd], foreign_decimals=2)
        self.assertEqual(table[str(pyg.id)], {"code": "PYG", "name": pyg.name, "buy_rate": "1", "sell_rate": "1", "decimals": 0})
        self.assertEqual(Decimal(table[str(usd.id)]["sell_rate"]), usd.sell_rate)
        self.assertEqual(table[str(usd.id)]["decimals"], 2)
