"""Pruebas de la facturación electrónica. Nunca llaman a Factura Segura: el API se reemplaza por mocks.

Se ejecutan con ``python manage.py test core.tests_invoicing``.
"""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from core import roles as role_lib
from core.forms import InvoiceCustomerForm
from core.models import BASE_CURRENCY_CODE, CashTransaction, Currency, Invoice, PurchaseOrder, Wallet
from core.services import cashier, factura_segura, invoicing
from core.services.factura_segura import (
    ApiResponse,
    FacturaSeguraClient,
    FacturaSeguraError,
    SimulatedClient,
    build_cdc,
    calculate_de,
    check_digit,
)
from core.tests import buy, make_card, make_cashier, make_test_currency

User = get_user_model()

LIVE = dict(FACTURA_SEGURA_TOKEN="token-de-prueba", FACTURA_SEGURA_URL="https://apitest.example")
SIMULATED = dict(FACTURA_SEGURA_TOKEN="")

CUSTOMER = invoicing.Customer(
    kind=Invoice.TAXPAYER, name="Guillermo González", email="cliente@example.com", ruc="2595733", dv="3"
)


def make_buyer(username="comprador"):
    user = User.objects.create_user(username=username, first_name="Ana", last_name="Compra", email=f"{username}@example.com")
    user.groups.add(Group.objects.get_or_create(name=role_lib.USER)[0])
    wallet = Wallet.objects.create(user=user, pyg_balance=Decimal("100000000"))
    return user, wallet


def paid_order(client, currency, amount="8000000", source="balance"):
    order = buy(client, currency, amount, source)
    assert order.status == PurchaseOrder.COMPLETED, order.status
    return order


class FakeLiveClient:
    """Imita al cliente real: registra las llamadas y responde lo que se le indique."""

    simulated = False

    def __init__(self, *, generar=None, estado=None):
        self.calls = []
        self.generar = generar
        self.estado = estado or {"estado_sifen": "Aprobado", "desc_sifen": "0260 - Aprobado"}

    def calcular_de(self, de):
        self.calls.append("calcular_de")
        return calculate_de(de)

    def generar_de(self, de):
        self.calls.append("generar_de")
        self.sent = de
        if isinstance(self.generar, Exception):
            raise self.generar
        return ApiResponse(0, "OK", "op-1", [{"CDC": build_cdc(de, "123456789")}])

    def get_estado_sifen(self, cdc, ruc):
        self.calls.append("get_estado_sifen")
        return self.estado

    def sol_cancelacion(self, cdc, ruc):
        self.calls.append("sol_cancelacion")
        return ApiResponse(0, "OK", "op-2", [])


# --- Cálculos del SIFEN -----------------------------------------------------


class SifenCalculationTests(TestCase):
    """El cálculo local reproduce el ejemplo del manual de Factura Segura."""

    MANUAL_ITEMS = [
        # (cantidad, precio, descuento, iAfecIVA, dPropIVA, dTasaIVA)
        ("3", "150000", "50000", "1", "100", "5"),
        ("20", "80000", "10000", "1", "100", "10"),
        ("1", "25000", "0", "4", "25", "10"),
        ("3", "34000", "0", "3", "0", "0"),
        ("5", "12000", "0", "2", "0", "0"),
    ]

    def _manual_summary(self):
        items = [
            {
                "dCantProSer": qty, "dPUniProSer": price, "dDescItem": disc,
                "iAfecIVA": affect, "dPropIVA": prop, "dTasaIVA": rate,
            }
            for qty, price, disc, affect, prop, rate in self.MANUAL_ITEMS
        ]
        return {"cMoneOpe": "PYG", "gCamItem": items}

    def test_the_totals_match_the_example_of_the_manual(self):
        de = calculate_de(self._manual_summary())
        expected = {
            "dSubExe": "120293", "dSubExo": "60000", "dSub5": "300000", "dSub10": "1406708",
            "dTotOpe": "1887001", "dTotDesc": "350000", "dDescTotal": "350000", "dTotGralOpe": "1887001",
            "dIVA5": "14286", "dIVA10": "127883", "dTotIVA": "142169",
            "dBaseGrav5": "285714", "dBaseGrav10": "1278825", "dTBasGraIVA": "1564539",
        }
        for key, value in expected.items():
            self.assertEqual(de[key], value, key)

    def test_the_items_match_the_example_of_the_manual(self):
        items = calculate_de(self._manual_summary())["gCamItem"]
        self.assertEqual((items[0]["dTotOpeItem"], items[0]["dBasGravIVA"], items[0]["dLiqIVAItem"]), ("300000", "285714", "14286"))
        self.assertEqual((items[2]["dBasGravIVA"], items[2]["dLiqIVAItem"], items[2]["dBasExe"]), ("6098", "610", "18293"))
        self.assertEqual(items[1]["dPorcDesIt"], "12,5")

    def test_an_exempt_item_has_no_vat(self):
        de = calculate_de({"cMoneOpe": "PYG", "gCamItem": [{"dCantProSer": "1", "dPUniProSer": "8000000", **invoicing.ITEM_TAX}]})
        self.assertEqual((de["dSubExe"], de["dTotOpe"], de["dTotIVA"]), ("8000000", "8000000", "0"))

    def test_the_check_digit_matches_known_rucs(self):
        self.assertEqual(check_digit("2595733"), 3)
        self.assertEqual(check_digit("80002247"), 5)

    def test_the_cdc_of_the_manual_has_a_valid_check_digit(self):
        de = {
            "iTiDE": "1", "dRucEm": "80002247", "dDVEmi": "5", "dEst": "001", "dPunExp": "003",
            "dNumDoc": "0000030", "iTipCont": "2", "dFeEmiDE": "2024-04-16T10:00:00", "iTipEmi": "1",
        }
        cdc = build_cdc(de, "651903317")
        self.assertEqual(cdc, "01800022475001003000003022024041616519033170")
        self.assertEqual(len(cdc), 44)


# --- Formulario del receptor ------------------------------------------------


class InvoiceCustomerFormTests(TestCase):
    def _form(self, tax_id):
        return InvoiceCustomerForm({"customer_name": "  Ana   Pérez ", "tax_id": tax_id, "customer_email": "ana@example.com"})

    def test_a_ruc_with_the_right_check_digit_is_a_taxpayer(self):
        form = self._form("2.595.733-3")
        self.assertTrue(form.is_valid(), form.errors)
        customer = form.customer()
        self.assertEqual((customer.kind, customer.ruc, customer.dv, customer.name), (Invoice.TAXPAYER, "2595733", "3", "Ana Pérez"))

    def test_a_ruc_with_a_wrong_check_digit_is_rejected(self):
        form = self._form("2595733-4")
        self.assertFalse(form.is_valid())
        self.assertIn("es 3", str(form.errors["tax_id"]))

    def test_a_number_without_check_digit_is_a_cedula(self):
        form = self._form("4.567.890")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual((form.customer().kind, form.customer().document), (Invoice.NON_TAXPAYER, "4567890"))

    def test_garbage_is_rejected(self):
        for value in ("abc", "12", "12345678-", "1234567-89"):
            self.assertFalse(self._form(value).is_valid(), value)


# --- Emisión ----------------------------------------------------------------


@override_settings(**SIMULATED)
class InvoiceIssueTests(TestCase):
    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")
        self.user, self.wallet = make_buyer()
        self.client.force_login(self.user)
        self.currency = make_test_currency()  # venta 8000
        self.order = paid_order(self.client, self.currency)

    def test_without_a_token_the_invoice_is_simulated_and_approved(self):
        invoice = invoicing.issue_for_order(self.order, CUSTOMER, self.user)
        self.assertTrue(invoice.is_simulated)
        self.assertEqual(invoice.status, Invoice.APPROVED)
        self.assertEqual(invoice.full_number, "001-003-0000001")
        self.assertEqual(len(invoice.cdc), 44)
        self.assertEqual(invoice.total, Decimal("8000000"))

    def test_simulated_invoices_do_not_use_the_real_range(self):
        invoicing.issue_for_order(self.order, CUSTOMER, self.user)
        with override_settings(**LIVE):
            other = paid_order(self.client, self.currency)
            live = invoicing.issue_for_order(other, CUSTOMER, self.user, client=FakeLiveClient())
        self.assertEqual(live.number, "0000314")

    def test_the_document_has_the_data_of_the_operation(self):
        invoice = invoicing.issue_for_order(self.order, CUSTOMER, self.user)
        de = invoice.request_payload
        self.assertEqual((de["dRucEm"], de["dDVEmi"], de["dEst"], de["dPunExp"]), ("2595733", "3", "001", "003"))
        self.assertEqual((de["iTiDE"], de["dNumDoc"], de["cMoneOpe"]), ("1", invoice.number, "PYG"))
        self.assertEqual((de["iNatRec"], de["dRucRec"], de["dDVRec"]), ("1", "2595733", "3"))
        self.assertEqual(de["iIndPres"], "2", "compra en línea: operación electrónica")
        self.assertEqual(de["gActEco"][0]["cActEco"], "62010")
        item = de["gCamItem"][0]
        self.assertEqual((item["dPUniProSer"], item["iAfecIVA"], item["dCodInt"]), ("8000000", "3", "XTS"))
        self.assertEqual(de["gPaConEIni"][0]["iTiPago"], invoicing.PAY_E_WALLET)
        self.assertEqual(de["dTotGralOpe"], "8000000", "los montos los completa calcular_de")

    def test_the_emission_time_is_paraguay_time(self):
        invoice = invoicing.issue_for_order(self.order, CUSTOMER, self.user)
        local = timezone.localtime(invoice.issued_at, invoicing.INVOICE_TIME_ZONE)
        self.assertEqual(invoice.request_payload["dFeEmiDE"], local.strftime("%Y-%m-%dT%H:%M:%S"))

    def test_a_non_taxpayer_is_identified_by_cedula(self):
        customer = invoicing.Customer(kind=Invoice.NON_TAXPAYER, name="Juan Pérez", email="j@example.com", document="4567890")
        de = invoicing.issue_for_order(self.order, customer, self.user).request_payload
        self.assertEqual((de["iNatRec"], de["iTiOpe"], de["iTipIDRec"], de["dNumIDRec"]), ("2", "2", "1", "4567890"))

    def test_a_company_ruc_is_a_legal_entity(self):
        customer = invoicing.Customer(kind=Invoice.TAXPAYER, name="Samera S.A.", email="s@example.com", ruc="80002247", dv="5")
        de = invoicing.issue_for_order(self.order, customer, self.user).request_payload
        self.assertEqual(de["iTiContRec"], "2")

    def test_a_card_payment_reports_the_brand(self):
        card = make_card(self.wallet)
        order = paid_order(self.client, self.currency, source=f"card:{card.id}")
        payment = invoicing.issue_for_order(order, CUSTOMER, self.user).request_payload["gPaConEIni"][0]
        self.assertEqual((payment["iTiPago"], payment["iDenTarj"], payment["iForProPa"]), ("3", "1", "2"))
        self.assertEqual(payment["dMonTiPag"], "8000000")

    def test_a_pending_order_cannot_be_invoiced(self):
        self.client.post("/trade/", {"action": "buy", "currency": self.currency.id,
                                     "pay_currency": Currency.objects.get(code=BASE_CURRENCY_CODE).id,
                                     "anchor": "in", "pay_amount": "8000"})
        pending = self.wallet.purchase_orders.get(status=PurchaseOrder.PENDING)
        with self.assertRaises(invoicing.InvoicingError):
            invoicing.issue_for_order(pending, CUSTOMER, self.user)
        self.assertFalse(Invoice.objects.filter(purchase_order=pending).exists())

    def test_an_operation_is_invoiced_only_once(self):
        invoicing.issue_for_order(self.order, CUSTOMER, self.user)
        with self.assertRaises(invoicing.InvoicingError):
            invoicing.issue_for_order(self.order, CUSTOMER, self.user)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_numbers_are_consecutive(self):
        first = invoicing.issue_for_order(self.order, CUSTOMER, self.user)
        second = invoicing.issue_for_order(paid_order(self.client, self.currency), CUSTOMER, self.user)
        self.assertEqual((first.number, second.number), ("0000001", "0000002"))


@override_settings(**LIVE)
class LiveInvoiceTests(TestCase):
    """Con token: se usa el rango real y el SIFEN responde después."""

    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")
        self.user, self.wallet = make_buyer()
        self.client.force_login(self.user)
        self.currency = make_test_currency()
        self.order = paid_order(self.client, self.currency)

    def test_the_invoice_is_calculated_generated_and_left_processing(self):
        fake = FakeLiveClient()
        invoice = invoicing.issue_for_order(self.order, CUSTOMER, self.user, client=fake)
        self.assertEqual(fake.calls, ["calcular_de", "generar_de"])
        self.assertFalse(invoice.is_simulated)
        self.assertEqual((invoice.status, invoice.number), (Invoice.PROCESSING, "0000314"))
        self.assertEqual(invoice.cdc[:2], "01")
        self.assertEqual(fake.sent["dNumDoc"], "0000314")

    @override_settings(INVOICE_EMITTER={**settings.INVOICE_EMITTER, "first_number": 314, "last_number": 315})
    def test_the_authorized_range_cannot_be_exceeded(self):
        invoicing.issue_for_order(self.order, CUSTOMER, self.user, client=FakeLiveClient())
        invoicing.issue_for_order(paid_order(self.client, self.currency), CUSTOMER, self.user, client=FakeLiveClient())
        with self.assertRaisesMessage(invoicing.InvoicingError, "Se agotó el rango"):
            invoicing.issue_for_order(paid_order(self.client, self.currency), CUSTOMER, self.user, client=FakeLiveClient())

    def test_a_rejected_request_keeps_the_number_and_can_be_retried(self):
        error = FacturaSeguraError("El valor del campo iTiOpe no es permitido (código -10001)", code=-10001)
        invoice = invoicing.issue_for_order(self.order, CUSTOMER, self.user, client=FakeLiveClient(generar=error))
        self.assertEqual(invoice.status, Invoice.ERROR)
        self.assertIn("iTiOpe", invoice.error_message)
        self.assertEqual(invoice.number, "0000314")

        retried = invoicing.retry(invoice, client=FakeLiveClient())
        self.assertEqual((retried.status, retried.number), (Invoice.PROCESSING, "0000314"))
        self.assertEqual(Invoice.objects.count(), 1)

    def test_a_timeout_leaves_the_invoice_unconfirmed_and_only_an_admin_can_retry(self):
        timeout = FacturaSeguraError("sin respuesta", uncertain=True)
        invoice = invoicing.issue_for_order(self.order, CUSTOMER, self.user, client=FakeLiveClient(generar=timeout))
        self.assertEqual(invoice.status, Invoice.UNCONFIRMED)
        with self.assertRaisesMessage(invoicing.InvoicingError, "sin confirmar"):
            invoicing.retry(invoice, client=FakeLiveClient())
        self.assertEqual(invoicing.retry(invoice, client=FakeLiveClient(), allow_unconfirmed=True).status, Invoice.PROCESSING)

    def test_requesting_again_after_an_error_updates_the_customer(self):
        error = FacturaSeguraError("email inválido", code=-1)
        invoicing.issue_for_order(self.order, CUSTOMER, self.user, client=FakeLiveClient(generar=error))
        fixed = invoicing.Customer(**{**vars(CUSTOMER), "email": "nuevo@example.com"})
        invoice = invoicing.issue_for_order(self.order, fixed, self.user, client=FakeLiveClient())
        self.assertEqual((invoice.customer_email, invoice.status), ("nuevo@example.com", Invoice.PROCESSING))

    def test_refreshing_records_the_sifen_state(self):
        invoice = invoicing.issue_for_order(self.order, CUSTOMER, self.user, client=FakeLiveClient())
        invoicing.refresh_status(invoice, client=FakeLiveClient())
        invoice.refresh_from_db()
        self.assertEqual((invoice.status, invoice.sifen_status), (Invoice.APPROVED, "Aprobado"))
        self.assertIsNotNone(invoice.status_checked_at)

    def test_cancelling_asks_the_sifen_and_waits_for_its_answer(self):
        invoice = invoicing.issue_for_order(self.order, CUSTOMER, self.user, client=FakeLiveClient())
        invoicing.refresh_status(invoice, client=FakeLiveClient())
        fake = FakeLiveClient()
        invoicing.request_cancellation(invoice, client=fake)
        self.assertIn("sol_cancelacion", fake.calls)
        self.assertEqual((invoice.status, invoice.sifen_status), (Invoice.APPROVED, "Cancelación solicitada"))
        invoicing.refresh_status(invoice, client=FakeLiveClient(estado={"estado_sifen": "Aprobado", "estado_can": "Aprobado"}))
        self.assertEqual(invoice.status, Invoice.CANCELLED)

    def test_only_an_approved_invoice_can_be_cancelled(self):
        invoice = invoicing.issue_for_order(self.order, CUSTOMER, self.user, client=FakeLiveClient())
        with self.assertRaises(invoicing.InvoicingError):
            invoicing.request_cancellation(invoice, client=FakeLiveClient())

    def test_the_status_is_refreshed_when_due(self):
        invoice = invoicing.issue_for_order(self.order, CUSTOMER, self.user, client=FakeLiveClient())
        self.assertFalse(invoicing.refresh_if_due(invoice), "recién generada: todavía no")
        Invoice.objects.filter(pk=invoice.pk).update(created_at=timezone.now() - timedelta(minutes=2))
        invoice.refresh_from_db()
        with mock.patch.object(FacturaSeguraClient, "get_estado_sifen", return_value={"estado_sifen": "Aprobado"}):
            self.assertTrue(invoicing.refresh_if_due(invoice))
        self.assertEqual(invoice.status, Invoice.APPROVED)


class SifenStateTests(TestCase):
    """Interpretación de get_estado_sifen en el orden que fija el manual."""

    def test_states(self):
        cases = [
            ({"estado_sifen": "Aprobado"}, Invoice.APPROVED, "Aprobado"),
            ({"estado_sifen": "Aprobado con observación"}, Invoice.APPROVED, "Aprobado OBS"),
            ({"estado_sifen": "Rechazado", "desc_sifen": "1001 - CDC duplicado"}, Invoice.REJECTED, "Rechazado"),
            ({"estado_sifen": "SOL.APROBACION"}, Invoice.PROCESSING, "Sol.Aprobación"),
            ({"estado_sifen": "ENVIADO_A_SIFEN"}, Invoice.PROCESSING, "Enviado al SIFEN"),
            ({"estado_sifen": "ERROR_SIFEN"}, Invoice.REJECTED, "Error SIFEN (Apr.)"),
            ({"estado_sifen": "Aprobado", "estado_can": "Aprobado"}, Invoice.CANCELLED, "Cancelado"),
            ({"estado_sifen": "Aprobado", "estado_can": "Rechazado"}, Invoice.APPROVED, "Aprobado (Rech.Can.)"),
            ({"estado_sifen": "Aprobado", "estado_can": "Aprobado", "estado_inu": "Aprobado"}, Invoice.VOIDED, "Inutilizado"),
            ({}, Invoice.PROCESSING, "Sin estado todavía"),
        ]
        for result, status, label in cases:
            with self.subTest(result=result):
                self.assertEqual(invoicing.interpret_sifen(result)[:2], (status, label))

    def test_the_detail_joins_description_and_error(self):
        detail = invoicing.interpret_sifen({"estado_sifen": "Rechazado", "desc_sifen": "Rechazado", "error_sifen": "RUC inválido"})[2]
        self.assertEqual(detail, "Rechazado · RUC inválido")


# --- Caja -------------------------------------------------------------------


@override_settings(**SIMULATED)
class CashInvoiceTests(TestCase):
    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")
        self.cashier = make_cashier()
        self.client.force_login(self.cashier)
        self.pyg = Currency.objects.get(code=BASE_CURRENCY_CODE)
        self.xts = make_test_currency()
        self.session = cashier.open_session(self.cashier, {self.pyg: Decimal("10000000"), self.xts: Decimal("5000")})

    def _operation(self, currency_in, currency_out, amount_out, method="cash", **card):
        tx = cashier.quote(self.session, currency_in, currency_out, Decimal(amount_out))
        return cashier.confirm(
            tx.id, self.session, payment_method=method, customer_name="Juan Pérez", customer_document="4567890", **card
        )

    def test_a_customer_buying_currency_can_be_invoiced(self):
        tx = self._operation(self.pyg, self.xts, "100")
        invoice = invoicing.issue_for_cash(tx, CUSTOMER, self.cashier)
        self.assertEqual(invoice.total, Decimal("800000"))
        de = invoice.request_payload
        self.assertEqual((de["iIndPres"], de["gPaConEIni"][0]["iTiPago"]), ("1", invoicing.PAY_CASH))

    def test_a_card_payment_at_the_desk_is_a_pos_payment(self):
        tx = self._operation(self.pyg, self.xts, "100", method="card", card_brand="mastercard", card_holder="JUAN", card_last4="4444")
        payment = invoicing.issue_for_cash(tx, CUSTOMER, self.cashier).request_payload["gPaConEIni"][0]
        self.assertEqual((payment["iTiPago"], payment["iDenTarj"], payment["iForProPa"]), ("3", "2", "1"))

    def test_a_customer_selling_currency_is_not_invoiced(self):
        tx = self._operation(self.xts, self.pyg, "70000")
        with self.assertRaisesMessage(invoicing.InvoicingError, "vende divisa"):
            invoicing.issue_for_cash(tx, CUSTOMER, self.cashier)

    def test_the_form_comes_filled_with_the_customer_of_the_operation(self):
        tx = self._operation(self.pyg, self.xts, "100")
        response = self.client.get(f"/caja/operaciones/{tx.id}/factura/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial["tax_id"], "4567890")
        self.assertContains(response, "Emitir factura")

    def test_the_receipt_offers_the_invoice(self):
        tx = self._operation(self.pyg, self.xts, "100")
        self.assertContains(self.client.get(f"/caja/operaciones/{tx.id}/"), f"/caja/operaciones/{tx.id}/factura/")
        sold = self._operation(self.xts, self.pyg, "70000")
        self.assertNotContains(self.client.get(f"/caja/operaciones/{sold.id}/"), "/factura/")

    def test_another_cashier_cannot_invoice_the_operation(self):
        tx = self._operation(self.pyg, self.xts, "100")
        other = Client(SERVER_NAME="localhost")
        other.force_login(make_cashier("cajero2"))
        self.assertEqual(other.get(f"/caja/operaciones/{tx.id}/factura/").status_code, 404)


# --- Pantallas --------------------------------------------------------------


@override_settings(**SIMULATED)
class InvoiceViewTests(TestCase):
    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")
        self.user, self.wallet = make_buyer()
        self.client.force_login(self.user)
        self.currency = make_test_currency()
        self.order = paid_order(self.client, self.currency)

    def _request(self, **data):
        payload = {"customer_name": "Ana Compra", "tax_id": "2595733-3", "customer_email": "ana@example.com"}
        payload.update(data)
        return self.client.post(f"/trade/checkout/{self.order.id}/factura/", payload)

    def test_the_paid_order_offers_an_invoice(self):
        response = self.client.get(f"/trade/checkout/{self.order.id}/")
        self.assertContains(response, "Solicitar factura")

    def test_the_form_comes_filled_with_the_user(self):
        response = self.client.get(f"/trade/checkout/{self.order.id}/factura/")
        self.assertEqual(response.context["form"].initial["customer_email"], "comprador@example.com")

    def test_requesting_an_invoice_issues_it_and_shows_it(self):
        response = self._request()
        invoice = Invoice.objects.get(purchase_order=self.order)
        self.assertRedirects(response, f"/facturas/{invoice.id}/")
        page = self.client.get(f"/facturas/{invoice.id}/")
        self.assertContains(page, invoice.full_number)
        self.assertContains(page, "FACTURA ELECTRÓNICA")
        self.assertContains(page, invoice.cdc)
        self.assertContains(self.client.get(f"/trade/checkout/{self.order.id}/"), "Ver factura")

    def test_an_invalid_form_does_not_issue(self):
        response = self._request(tax_id="2595733-9")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Invoice.objects.exists())

    def test_a_second_request_goes_to_the_existing_invoice(self):
        self._request()
        invoice = Invoice.objects.get()
        self.assertRedirects(self.client.get(f"/trade/checkout/{self.order.id}/factura/"), f"/facturas/{invoice.id}/")

    def test_the_list_shows_only_my_invoices(self):
        self._request()
        mine = Invoice.objects.get()
        stranger, _ = make_buyer("otro")
        other = Client(SERVER_NAME="localhost")
        other.force_login(stranger)
        self.assertContains(self.client.get("/facturas/"), mine.full_number)
        self.assertNotContains(other.get("/facturas/"), mine.full_number)
        self.assertEqual(other.get(f"/facturas/{mine.id}/").status_code, 404)

    def test_an_admin_sees_every_invoice_and_can_cancel(self):
        self._request()
        invoice = Invoice.objects.get()
        admin = Client(SERVER_NAME="localhost")
        admin.force_login(make_cashier("jefa", role_lib.ADMIN))
        self.assertContains(admin.get("/facturas/"), invoice.full_number)
        admin.post(f"/facturas/{invoice.id}/cancelar/")
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.CANCELLED)

    def test_a_user_cannot_cancel(self):
        self._request()
        invoice = Invoice.objects.get()
        self.assertEqual(self.client.post(f"/facturas/{invoice.id}/cancelar/").status_code, 403)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.APPROVED)

    def test_the_list_filters_by_status(self):
        self._request()
        self.assertEqual(len(self.client.get("/facturas/?status=approved").context["invoices"]), 1)
        self.assertEqual(len(self.client.get("/facturas/?status=rejected").context["invoices"]), 0)

    def test_simulated_invoices_have_no_files_to_download(self):
        self._request()
        invoice = Invoice.objects.get()
        self.assertEqual(self.client.get(f"/facturas/{invoice.id}/kude/").status_code, 404)

    def test_the_sidebar_links_the_invoices(self):
        self.assertContains(self.client.get("/wallet/"), 'href="/facturas/"')

    def test_a_manager_has_no_invoices_tab(self):
        manager = Client(SERVER_NAME="localhost")
        manager.force_login(make_cashier("gestor", role_lib.MANAGER))
        self.assertEqual(manager.get("/facturas/").status_code, 403)


@override_settings(**LIVE)
class InvoiceDownloadTests(TestCase):
    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")
        self.user, self.wallet = make_buyer()
        self.client.force_login(self.user)
        self.order = paid_order(self.client, make_test_currency())
        self.invoice = invoicing.issue_for_order(self.order, CUSTOMER, self.user, client=FakeLiveClient())

    def test_the_kude_is_downloaded_from_factura_segura(self):
        with mock.patch.object(FacturaSeguraClient, "download", return_value=b"%PDF-1.4 ...") as download:
            response = self.client.get(f"/facturas/{self.invoice.id}/kude/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("factura-001-003-0000314.pdf", response["Content-Disposition"])
        download.assert_called_once_with(factura_segura.DOWNLOAD_KUDE, "2595733", self.invoice.cdc)

    def test_the_xml_is_downloaded_from_factura_segura(self):
        with mock.patch.object(FacturaSeguraClient, "download", return_value=b"<rDE/>"):
            response = self.client.get(f"/facturas/{self.invoice.id}/xml/")
        self.assertEqual(response["Content-Type"], "application/xml")

    def test_a_download_error_returns_to_the_invoice(self):
        with mock.patch.object(FacturaSeguraClient, "download", side_effect=FacturaSeguraError("no existe")):
            response = self.client.get(f"/facturas/{self.invoice.id}/kude/")
        self.assertRedirects(response, f"/facturas/{self.invoice.id}/", fetch_redirect_response=False)


# --- Cliente HTTP -----------------------------------------------------------


def http_response(status=200, body=None, content=b"", content_type="application/json"):
    response = mock.Mock(status_code=status, content=content, headers={"Content-Type": content_type})
    response.json.return_value = body if body is not None else {}
    if body is None and content_type != "application/json":
        response.json.side_effect = ValueError
    return response


@override_settings(**LIVE)
class FacturaSeguraClientTests(TestCase):
    def test_a_request_follows_the_protocol(self):
        body = {"code": 0, "description": "OK", "operation_info": {"id": "abc"}, "results": [{"CDC": "1" * 44}]}
        with mock.patch("requests.post", return_value=http_response(body=body)) as post:
            result = FacturaSeguraClient().generar_de({"iTiDE": "1"})
        self.assertEqual(result.first["CDC"], "1" * 44)
        self.assertEqual(result.operation_id, "abc")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://apitest.example/misife00/v1/esi")
        self.assertEqual(kwargs["json"], {"operation": "generar_de", "params": {"DE": {"iTiDE": "1"}}})
        self.assertEqual(kwargs["headers"]["Authentication-Token"], "token-de-prueba")

    def test_a_negative_code_is_an_error_with_the_description(self):
        body = {"code": -10021, "description": "El RUC del emisor se encuentra con estado INACTIVO",
                "operation_info": {"id": "x"}, "results": []}
        with mock.patch("requests.post", return_value=http_response(body=body)):
            with self.assertRaises(FacturaSeguraError) as raised:
                FacturaSeguraClient().get_estado_sifen("1" * 44, "2595733-3")
        self.assertEqual(raised.exception.code, -10021)
        self.assertIn("INACTIVO", str(raised.exception))
        self.assertFalse(raised.exception.uncertain)

    def test_the_ruc_is_sent_without_check_digit(self):
        body = {"code": 0, "description": "OK", "operation_info": {"id": "x"}, "results": [{}]}
        with mock.patch("requests.post", return_value=http_response(body=body)) as post:
            FacturaSeguraClient().get_estado_sifen("1" * 44, "2595733-3")
        self.assertEqual(post.call_args.kwargs["json"]["params"]["dRucEm"], "2595733")

    def test_a_timeout_is_uncertain(self):
        with mock.patch("requests.post", side_effect=requests.Timeout):
            with self.assertRaises(FacturaSeguraError) as raised:
                FacturaSeguraClient().generar_de({})
        self.assertTrue(raised.exception.uncertain)

    def test_a_bad_token_is_reported(self):
        with mock.patch("requests.post", return_value=http_response(status=401)):
            with self.assertRaisesMessage(FacturaSeguraError, "token"):
                FacturaSeguraClient().calcular_de({})

    def test_download_uses_the_get_url_of_the_manual(self):
        pdf = http_response(content=b"%PDF", content_type="application/pdf")
        with mock.patch("requests.get", return_value=pdf) as get:
            content = FacturaSeguraClient().download(factura_segura.DOWNLOAD_KUDE, "2595733", "0" * 44)
        self.assertEqual(content, b"%PDF")
        self.assertEqual(get.call_args.args[0], f"https://apitest.example/misife00/v1/esi/dwn_kude/2595733/{'0' * 44}")

    def test_a_json_answer_to_a_download_is_an_error(self):
        error = http_response(body={"code": -1, "description": "CDC inexistente"})
        with mock.patch("requests.get", return_value=error):
            with self.assertRaisesMessage(FacturaSeguraError, "CDC inexistente"):
                FacturaSeguraClient().download(factura_segura.DOWNLOAD_XML, "2595733", "0" * 44)

    def test_login_returns_the_authentication_token(self):
        body = {"meta": {"code": 200}, "response": {"csrf_token": "c", "user": {"authentication_token": "tok"}}}
        with mock.patch("requests.post", return_value=http_response(body=body)) as post:
            self.assertEqual(factura_segura.login("esi@example.com", "secreta"), "tok")
        self.assertEqual(post.call_args.args[0], "https://apitest.example/login?include_auth_token")

    def test_the_simulated_client_never_downloads(self):
        with self.assertRaises(FacturaSeguraError):
            SimulatedClient().download(factura_segura.DOWNLOAD_KUDE, "2595733", "0" * 44)
