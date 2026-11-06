"""Pruebas de las notificaciones por cambio de cotización."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase

from core import roles as role_lib
from core.models import BASE_CURRENCY_CODE, Currency, Notification
from core.services.notifications import mark_read, notify_rate_change
from core.tests import make_test_currency

User = get_user_model()


def make_user(username, role):
    user = User.objects.create_user(username=username)
    user.groups.add(Group.objects.get_or_create(name=role)[0])
    return user


def currency_post(currency, buy, sell):
    """Datos del formulario de edición de una divisa."""
    return {
        "code": currency.code,
        "name": currency.name,
        "symbol": currency.symbol,
        "buy_rate": buy,
        "sell_rate": sell,
        "is_active": "on",
    }


class NotifyRateChangeTests(TestCase):
    """El servicio decide a quién avisar y cuándo."""

    def setUp(self):
        self.customer = make_user("cliente", role_lib.USER)
        self.other = make_user("otro", role_lib.USER)
        self.manager = make_user("gestor", role_lib.MANAGER)
        self.currency = make_test_currency()  # compra 7000 / venta 8000

    def _raise_rates(self):
        self.currency.buy_rate = Decimal("7100")
        self.currency.sell_rate = Decimal("8100")
        self.currency.save()

    def test_every_client_gets_one_notice_and_other_roles_do_not(self):
        self._raise_rates()
        created = notify_rate_change(self.currency, Decimal("7000"), Decimal("8000"))
        self.assertEqual(created, 2)
        notice = Notification.objects.get(user=self.customer)
        self.assertEqual((notice.previous_sell, notice.new_sell), (Decimal("8000"), Decimal("8100")))
        self.assertTrue(notice.went_up)
        self.assertFalse(Notification.objects.filter(user=self.manager).exists())

    def test_no_notice_when_the_rates_did_not_change(self):
        self.assertEqual(notify_rate_change(self.currency, Decimal("7000"), Decimal("8000")), 0)
        self.assertEqual(Notification.objects.count(), 0)

    def test_no_notice_for_the_base_currency(self):
        base = Currency.objects.get(code=BASE_CURRENCY_CODE)
        self.assertEqual(notify_rate_change(base, Decimal("2"), Decimal("3")), 0)

    def test_no_notice_for_an_inactive_currency(self):
        self.currency.is_active = False
        self._raise_rates()
        self.assertEqual(notify_rate_change(self.currency, Decimal("7000"), Decimal("8000")), 0)

    def test_mark_read_only_touches_the_given_user(self):
        self._raise_rates()
        notify_rate_change(self.currency, Decimal("7000"), Decimal("8000"))
        mine = Notification.objects.get(user=self.customer)
        theirs = Notification.objects.get(user=self.other)
        self.assertEqual(mark_read(self.customer, theirs.id), 0, "no puede marcar el aviso de otro")
        self.assertEqual(mark_read(self.customer, "no-es-un-numero"), 0)
        self.assertEqual(mark_read(self.customer, mine.id), 1)
        theirs.refresh_from_db()
        self.assertFalse(theirs.is_read)


class NotificationFlowTests(TestCase):
    """Los avisos se generan desde las vistas y se ven en su pantalla."""

    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")
        self.customer = make_user("cliente", role_lib.USER)
        self.other = make_user("otro", role_lib.USER)
        self.manager = make_user("gestor", role_lib.MANAGER)
        self.admin = make_user("admin1", role_lib.ADMIN)
        self.currency = make_test_currency()  # compra 7000 / venta 8000

    def _edit(self, buy, sell):
        self.client.force_login(self.manager)
        return self.client.post(
            f"/currencies/{self.currency.id}/edit/", currency_post(self.currency, buy, sell)
        )

    def test_editing_a_rate_notifies_the_clients(self):
        response = self._edit("7500", "8500")
        self.assertEqual(response.status_code, 302)
        notice = Notification.objects.get(user=self.customer)
        self.assertEqual((notice.previous_buy, notice.new_buy), (Decimal("7000"), Decimal("7500")))
        self.assertEqual((notice.previous_sell, notice.new_sell), (Decimal("8000"), Decimal("8500")))

    def test_saving_the_same_rates_does_not_notify(self):
        self._edit("7000", "8000")
        self.assertEqual(Notification.objects.count(), 0)

    def test_creating_a_currency_does_not_notify(self):
        self.client.force_login(self.manager)
        self.client.post(
            "/currencies/new/",
            {"code": "XTN", "name": "Nueva", "symbol": "N$", "buy_rate": "100", "sell_rate": "120", "is_active": "on"},
        )
        self.assertTrue(Currency.objects.filter(code="XTN").exists())
        self.assertEqual(Notification.objects.count(), 0)

    def test_advancing_a_week_sends_one_notice_per_currency_not_one_per_day(self):
        tracked = Currency.objects.filter(is_active=True).exclude(code=BASE_CURRENCY_CODE).count()
        self.client.force_login(self.admin)
        self.client.post("/simulation/advance-week/")
        self.assertEqual(Notification.objects.filter(user=self.customer).count(), tracked)

    def test_the_list_shows_only_my_notices_and_the_unread_counter(self):
        self._edit("7500", "8500")
        self.client.force_login(self.customer)
        response = self.client.get("/notifications/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["notifications"]), 1)
        self.assertEqual(response.context["unread_notifications"], 1)
        self.assertContains(response, "XTS")

    def test_marking_one_notice_as_read_lowers_the_counter(self):
        self._edit("7500", "8500")
        self.client.force_login(self.customer)
        notice = Notification.objects.get(user=self.customer)
        self.client.post("/notifications/", {"id": notice.id})
        notice.refresh_from_db()
        self.assertTrue(notice.is_read)
        self.assertEqual(self.client.get("/notifications/").context["unread_notifications"], 0)

    def test_a_client_cannot_mark_someone_elses_notice(self):
        self._edit("7500", "8500")
        theirs = Notification.objects.get(user=self.other)
        self.client.force_login(self.customer)
        self.client.post("/notifications/", {"id": theirs.id})
        theirs.refresh_from_db()
        self.assertFalse(theirs.is_read)

    def test_mark_all_as_read(self):
        self._edit("7500", "8500")
        self._edit("7600", "8600")
        self.client.force_login(self.customer)
        self.client.post("/notifications/")
        self.assertFalse(Notification.objects.filter(user=self.customer, read_at__isnull=True).exists())

    def test_only_clients_can_open_the_notifications_page(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get("/notifications/").status_code, 403)
