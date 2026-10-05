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
from decimal import ROUND_DOWN, Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase

from core import roles as role_lib
from core.forms import (
    CurrencyForm,
    DeleteAccountForm,
    PaymentMethodForm,
    WalletDepositForm,
)
from core.models import (
    BASE_CURRENCY_CODE,
    Currency,
    CurrencyRate,
    PaymentMethod,
    SimulationState,
    Wallet,
    WalletTransaction,
)
from core.services.charts import build_rate_chart
from core.services.simulation import (
    MAX_DAILY_CHANGE,
    MIN_DAILY_CHANGE,
    advance_days,
    advance_one_day,
)
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
        self.client.post(
            "/trade/",
            {"action": "buy", "currency": self.currency.id, "pyg_amount": "8000000", "payment_source": "wallet"},
        )
        wallet = self._wallet()
        holding = wallet.holdings.get(currency=self.currency)
        self.assertEqual(wallet.pyg_balance, Decimal("0.00"))
        # 8.000.000 Gs. / 8000 (precio de venta) = 1000 unidades
        self.assertEqual(holding.amount, Decimal("1000.000000"))
        transaction = wallet.transactions.get(kind=WalletTransaction.BUY)
        self.assertEqual(transaction.rate_used, Decimal("8000.000000"))

    def test_buying_with_a_card_does_not_touch_the_balance(self):
        card = self._card()
        self.client.post(
            "/trade/",
            {"action": "buy", "currency": self.currency.id, "pyg_amount": "8000000", "payment_source": str(card.id)},
        )
        wallet = self._wallet()
        self.assertEqual(wallet.pyg_balance, Decimal("0.00"), "pagar con tarjeta no usa el saldo")
        self.assertEqual(wallet.holdings.get(currency=self.currency).amount, Decimal("1000.000000"))
        self.assertEqual(wallet.transactions.get(kind=WalletTransaction.BUY).payment_method_id, card.id)

    def test_cannot_buy_with_a_balance_that_is_too_low(self):
        self.client.post(
            "/trade/",
            {"action": "buy", "currency": self.currency.id, "pyg_amount": "8000000", "payment_source": "wallet"},
        )
        wallet = self._wallet()
        self.assertEqual(wallet.pyg_balance, Decimal("0.00"))
        self.assertFalse(wallet.holdings.filter(currency=self.currency).exists())

    def test_cannot_buy_with_a_card_of_another_user(self):
        other = Wallet.objects.create(user=User.objects.create_user(username="intruder_target"))
        foreign_card = make_card(other)
        self.client.post(
            "/trade/",
            {
                "action": "buy",
                "currency": self.currency.id,
                "pyg_amount": "8000000",
                "payment_source": str(foreign_card.id),
            },
        )
        self.assertFalse(self._wallet().holdings.filter(currency=self.currency).exists())

    def test_selling_uses_the_buy_price_and_credits_the_balance(self):
        card = self._card()
        self.client.post("/wallet/deposit/", {"amount": "8000000", "payment_method": card.id})
        self.client.post(
            "/trade/",
            {"action": "buy", "currency": self.currency.id, "pyg_amount": "8000000", "payment_source": "wallet"},
        )
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
        self.client.post(
            "/trade/",
            {"action": "buy", "currency": self.currency.id, "pyg_amount": "8000000", "payment_source": "wallet"},
        )
        self.client.post("/trade/", {"action": "sell", "currency": self.currency.id, "amount": "1000"})
        self.assertLess(self._wallet().pyg_balance, Decimal("8000000.00"))

    def test_cannot_sell_more_than_the_holding(self):
        card = self._card()
        self.client.post("/wallet/deposit/", {"amount": "8000000", "payment_method": card.id})
        self.client.post(
            "/trade/",
            {"action": "buy", "currency": self.currency.id, "pyg_amount": "8000000", "payment_source": "wallet"},
        )
        self.client.post("/trade/", {"action": "sell", "currency": self.currency.id, "amount": "9999"})
        wallet = self._wallet()
        self.assertEqual(wallet.holdings.get(currency=self.currency).amount, Decimal("1000.000000"))
        self.assertEqual(wallet.pyg_balance, Decimal("0.00"))

    def test_withdraw_test_button_removes_a_small_fixed_amount(self):
        card = self._card()
        self.client.post("/wallet/deposit/", {"amount": "8000000", "payment_method": card.id})
        self.client.post(
            "/trade/",
            {"action": "buy", "currency": self.currency.id, "pyg_amount": "8000000", "payment_source": "wallet"},
        )
        self.client.post(f"/wallet/withdraw/{self.currency.id}/test/")
        self.assertEqual(
            self._wallet().holdings.get(currency=self.currency).amount, Decimal("999.000000")
        )

    def test_cannot_withdraw_more_than_the_holding(self):
        card = self._card()
        self.client.post("/wallet/deposit/", {"amount": "8000000", "payment_method": card.id})
        self.client.post(
            "/trade/",
            {"action": "buy", "currency": self.currency.id, "pyg_amount": "8000000", "payment_source": "wallet"},
        )
        self.client.post("/wallet/withdraw/", {"currency": self.currency.id, "amount": "9999"})
        self.assertEqual(
            self._wallet().holdings.get(currency=self.currency).amount,
            Decimal("1000.000000"),
            "un retiro mayor a la tenencia debe rechazarse",
        )

    def test_holdings_are_valued_at_the_buy_price(self):
        card = self._card()
        self.client.post("/wallet/deposit/", {"amount": "8000000", "payment_method": card.id})
        self.client.post(
            "/trade/",
            {"action": "buy", "currency": self.currency.id, "pyg_amount": "8000000", "payment_source": "wallet"},
        )
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

    def test_the_base_currency_is_not_offered_in_the_preview(self):
        response = self.client.get("/trade/")
        base_id = str(Currency.objects.get(code=BASE_CURRENCY_CODE).id)
        self.assertNotIn(base_id, response.context["rate_table"])

    def test_an_inactive_currency_is_not_offered_in_the_preview(self):
        self.currency.is_active = False
        self.currency.save()
        response = self.client.get("/trade/")
        self.assertNotIn(str(self.currency.id), response.context["rate_table"])

    def test_the_balance_is_published_so_the_preview_can_warn(self):
        card = make_card(Wallet.objects.get_or_create(user=self.user)[0])
        self.client.post("/wallet/deposit/", {"amount": "900000", "payment_method": card.id})
        response = self.client.get("/trade/")
        self.assertEqual(Decimal(response.context["wallet_balance_json"]), Decimal("900000"))

    def test_the_two_forms_do_not_share_field_ids(self):
        """Los dos formularios tienen un campo "currency": sus ids deben diferir."""
        response = self.client.get("/trade/")
        html = response.content.decode()
        self.assertIn('id="id_buy_currency"', html)
        self.assertIn('id="id_buy_pyg_amount"', html)
        self.assertNotIn('id="id_currency"', html)

    def test_the_preview_matches_what_the_purchase_actually_does(self):
        """La cuenta que hace el navegador y la que hace el servidor deben coincidir."""
        entry = self.client.get("/trade/").context["rate_table"][str(self.currency.id)]
        sell_rate = Decimal(entry["sell_rate"])
        amount = Decimal("8000000")
        expected = (amount / sell_rate).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)

        card = make_card(Wallet.objects.get_or_create(user=self.user)[0])
        self.client.post(
            "/trade/",
            {
                "action": "buy",
                "currency": self.currency.id,
                "pyg_amount": str(amount),
                "payment_source": str(card.id),
            },
        )
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
        make_card(self._wallet())
        self.client.post(
            "/trade/",
            {"action": "buy", "currency": self.currency.id, "pyg_amount": "8000000",
             "payment_source": str(self._wallet().payment_methods.first().id)},
        )
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
