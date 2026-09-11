"""Simple unit tests for the Exchange Pro core app.

Run with:
    python manage.py test core
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase

from core import roles as role_lib
from core.forms import CurrencyForm, DeleteAccountForm
from core.models import Currency, Wallet

User = get_user_model()


class RolesTests(TestCase):
    """core/roles.py — role helpers."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice")

    def test_user_with_no_group_has_no_roles(self):
        self.assertEqual(role_lib.user_roles(self.user), set())
        self.assertFalse(role_lib.is_admin(self.user))

    def test_admin_group_grants_admin_role(self):
        self.user.groups.add(Group.objects.get_or_create(name=role_lib.ADMIN)[0])
        self.assertTrue(role_lib.is_admin(self.user))
        self.assertTrue(role_lib.is_manager(self.user), "admin should inherit manager capabilities")

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


class CurrencyModelTests(TestCase):
    """core/models.py — Currency."""

    def test_usd_value_is_locked_to_one(self):
        # USD is seeded by the 0002_seed_usd migration; try to move its value away from 1.
        usd = Currency.objects.get(code="USD")
        usd.value_in_usd = Decimal("5.00")
        usd.save()
        usd.refresh_from_db()
        self.assertEqual(usd.value_in_usd, Decimal("1.000000"), "USD must always be worth exactly 1 USD")
        self.assertTrue(usd.is_base)

    def test_other_currency_keeps_its_own_value(self):
        eur = Currency.objects.create(code="eur", name="Euro", value_in_usd=Decimal("1.10"))
        self.assertEqual(eur.code, "EUR", "code should be upper-cased")
        self.assertEqual(eur.value_in_usd, Decimal("1.10"))
        self.assertFalse(eur.is_base)


class CurrencyFormTests(TestCase):
    """core/forms.py — CurrencyForm."""

    def test_rejects_zero_or_negative_value(self):
        form = CurrencyForm(data={"code": "ARS", "name": "Peso", "symbol": "$", "value_in_usd": "0", "is_active": "on"})
        self.assertFalse(form.is_valid())
        self.assertIn("value_in_usd", form.errors)

    def test_accepts_a_positive_value(self):
        form = CurrencyForm(data={"code": "ars", "name": "Peso", "symbol": "$", "value_in_usd": "0.0011", "is_active": "on"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["code"], "ARS")


class DeleteAccountFormTests(TestCase):
    """core/forms.py — DeleteAccountForm."""

    def test_requires_the_word_delete(self):
        form = DeleteAccountForm(data={"confirmation": "please"})
        self.assertFalse(form.is_valid())

    def test_accepts_delete_case_insensitively(self):
        form = DeleteAccountForm(data={"confirmation": "delete"})
        self.assertTrue(form.is_valid())


class RoleRequiredViewTests(TestCase):
    """URL access control per role, exercised through the real views."""

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
        response = self.client.get("/manage/users/")
        self.assertEqual(response.status_code, 403)

    def test_manager_cannot_reach_user_management(self):
        self.client.force_login(self.manager)
        response = self.client.get("/manage/users/")
        self.assertEqual(response.status_code, 403)

    def test_manager_can_reach_currency_management(self):
        self.client.force_login(self.manager)
        response = self.client.get("/currencies/")
        self.assertEqual(response.status_code, 200)

    def test_trader_cannot_reach_currency_management(self):
        self.client.force_login(self.trader)
        response = self.client.get("/currencies/")
        self.assertEqual(response.status_code, 403)

    def test_trader_can_reach_wallet(self):
        self.client.force_login(self.trader)
        response = self.client.get("/wallet/")
        self.assertEqual(response.status_code, 200)


class WalletTests(TestCase):
    """Deposit / buy / withdraw flows a user drives from the wallet and trade pages."""

    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")
        self.user = User.objects.create_user(username="trader1")
        self.user.groups.add(Group.objects.get_or_create(name=role_lib.USER)[0])
        self.client.force_login(self.user)
        self.eur = Currency.objects.create(code="EUR", name="Euro", value_in_usd=Decimal("1.100000"))

    def test_new_user_starts_with_an_empty_wallet(self):
        response = self.client.get("/wallet/")
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(wallet.usd_balance, Decimal("0.00"))

    def test_deposit_test_button_adds_fixed_demo_amount(self):
        self.client.post("/wallet/deposit/test/")
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(wallet.usd_balance, Decimal("100.00"))

    def test_real_deposit_adds_the_requested_amount(self):
        self.client.post("/wallet/deposit/", {"amount": "25.50"})
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(wallet.usd_balance, Decimal("25.50"))

    def test_buy_converts_usd_into_the_chosen_currency(self):
        self.client.post("/wallet/deposit/test/")  # +100 USD
        self.client.post("/trade/", {"currency": self.eur.id, "usd_amount": "11.00"})
        wallet = Wallet.objects.get(user=self.user)
        holding = wallet.holdings.get(currency=self.eur)
        self.assertEqual(wallet.usd_balance, Decimal("89.00"))
        self.assertEqual(holding.amount, Decimal("10.000000"))  # 11 USD / 1.10

    def test_cannot_buy_more_than_the_usd_balance(self):
        self.client.post("/trade/", {"currency": self.eur.id, "usd_amount": "50.00"})
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(wallet.usd_balance, Decimal("0.00"))
        self.assertFalse(wallet.holdings.filter(currency=self.eur).exists())

    def test_withdraw_test_button_removes_a_small_fixed_amount(self):
        self.client.post("/wallet/deposit/test/")
        self.client.post("/trade/", {"currency": self.eur.id, "usd_amount": "11.00"})  # 10 EUR
        self.client.post(f"/wallet/withdraw/{self.eur.id}/test/")
        wallet = Wallet.objects.get(user=self.user)
        holding = wallet.holdings.get(currency=self.eur)
        self.assertEqual(holding.amount, Decimal("9.000000"))

    def test_cannot_withdraw_more_than_the_holding(self):
        self.client.post("/wallet/deposit/test/")
        self.client.post("/trade/", {"currency": self.eur.id, "usd_amount": "11.00"})  # 10 EUR
        self.client.post("/wallet/withdraw/", {"currency": self.eur.id, "amount": "999"})
        wallet = Wallet.objects.get(user=self.user)
        holding = wallet.holdings.get(currency=self.eur)
        self.assertEqual(holding.amount, Decimal("10.000000"), "an over-withdrawal must be rejected")
