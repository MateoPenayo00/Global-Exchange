"""Facturación electrónica de las operaciones de la casa de cambio.

**Qué se factura.** La venta de divisas al cliente: una compra en línea ya pagada
(``PurchaseOrder`` completada) o una operación de caja realizada en la que el
cliente recibe divisa extranjera. Cuando el cliente *vende* divisa la casa de
cambio es la compradora, y ese documento (una autofactura) no lo soporta Factura
Segura; esas operaciones conservan su comprobante interno.

El ítem se factura como **exento de IVA**, por el total en guaraníes de la
operación (``ITEM_TAX``). Es una decisión del proyecto a validar con el PO: si
se decidiera facturar sólo el margen con IVA, alcanza con cambiar el ítem.

**Flujo de emisión** (``issue_for_order`` / ``issue_for_cash``):

1. Se comprueba que la operación se pueda facturar y se crea la ``Invoice`` con
   los datos del receptor y el **número reservado** (``_reserve_number``).
2. ``send()`` arma el DE resumido (``build_summary``), le pide a Factura Segura
   que calcule los montos (``calcular_de``) y genera el documento
   (``generar_de``), que devuelve el CDC. La factura queda *En proceso*.
3. El SIFEN aprueba o rechaza en segundo plano; ``refresh_status()`` lo consulta
   con ``get_estado_sifen`` e interpreta la respuesta como indica el manual
   (``interpret_sifen``).

Si el envío falla la factura conserva su número: ``retry()`` vuelve a enviarla
con el mismo. Si Factura Segura no respondió (timeout), el resultado queda *Sin
confirmar* y sólo un administrador puede reintentar, porque el documento pudo
haberse generado igual.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import (
    BASE_CURRENCY_CODE,
    CashTransaction,
    Invoice,
    PaymentMethod,
    PurchaseOrder,
    quantize_pyg,
)
from ..templatetags.money import guarani, units
from .factura_segura import FacturaSeguraClient, FacturaSeguraError, SimulatedClient

# El documento lleva la hora de Paraguay, aunque la base guarde todo en UTC.
INVOICE_TIME_ZONE = ZoneInfo("America/Asuncion")

# Las facturas simuladas usan su propia numeración, sin tope práctico.
SIMULATED_FIRST_NUMBER = 1
MAX_NUMBER = 9_999_999

# Ítem exento: ver la nota del módulo.
ITEM_TAX = {"iAfecIVA": "3", "dPropIVA": "0", "dTasaIVA": "0"}

# El manual recomienda esperar un minuto después de generar antes de consultar.
STATUS_CHECK_INTERVAL = timedelta(seconds=30)

# Códigos del SIFEN para el medio de pago (iTiPago) y la marca de tarjeta (iDenTarj).
PAY_CASH, PAY_CARD, PAY_TRANSFER, PAY_E_WALLET = "1", "3", "5", "7"
CARD_BRANDS = {PaymentMethod.VISA: "1", PaymentMethod.MASTERCARD: "2", PaymentMethod.AMEX: "3"}
CARD_OTHER = "99"
CARD_POS, CARD_ONLINE = "1", "2"  # iForProPa: POS o pago electrónico


class InvoicingError(Exception):
    """No se pudo facturar; el mensaje es apto para el usuario."""


@dataclass
class Customer:
    """Receptor de la factura: contribuyente (RUC y DV) o no contribuyente (cédula)."""

    kind: str
    name: str
    email: str
    ruc: str = ""
    dv: str = ""
    document: str = ""


# --- Qué se puede facturar ------------------------------------------------


def invoice_blocker(operation) -> str:
    """Motivo por el que ``operation`` no se puede facturar, o ``""`` si se puede."""
    if isinstance(operation, PurchaseOrder):
        if operation.status != PurchaseOrder.COMPLETED:
            return "Sólo se factura una compra ya pagada."
        return ""
    if operation.status != CashTransaction.COMPLETED:
        return "Sólo se factura una operación realizada."
    if operation.currency_out.code == BASE_CURRENCY_CODE:
        return "Cuando el cliente vende divisa no se emite factura: la casa de cambio es la compradora."
    return ""


def operation_total(operation) -> Decimal:
    if isinstance(operation, PurchaseOrder):
        return quantize_pyg(operation.pyg_amount)
    return quantize_pyg(operation.pyg_value)


def operation_description(operation) -> str:
    if isinstance(operation, PurchaseOrder):
        return (
            f"Venta de {units(operation.currency_amount)} {operation.currency.code} "
            f"a Gs. {guarani(operation.rate)} c/u - compra en línea #{operation.pk}"
        )
    code = operation.currency_out.code
    text = f"Venta de {units(operation.amount_out)} {code} a Gs. {guarani(operation.rate_out)} c/u"
    if operation.currency_in.code != BASE_CURRENCY_CODE:
        text += f" contra {units(operation.amount_in)} {operation.currency_in.code}"
    return f"{text} - operación de caja #{operation.pk}"


# --- Clientes de Factura Segura -------------------------------------------


def default_client():
    """El cliente real si hay token configurado; si no, el simulado."""
    return FacturaSeguraClient() if settings.FACTURA_SEGURA_TOKEN else SimulatedClient()


def client_for(invoice: Invoice):
    """El cliente con el que se emitió ``invoice`` (una factura simulada nunca va al API real)."""
    if invoice.is_simulated:
        return SimulatedClient()
    if not settings.FACTURA_SEGURA_TOKEN:
        raise InvoicingError("Falta configurar FACTURA_SEGURA_TOKEN para consultar Factura Segura.")
    return FacturaSeguraClient()


def is_live() -> bool:
    return bool(settings.FACTURA_SEGURA_TOKEN)


# --- Numeración -----------------------------------------------------------


def _reserve_number(simulated: bool) -> str:
    """El siguiente número libre del rango, con siete dígitos (``0000314``)."""
    emitter = settings.INVOICE_EMITTER
    first, last = (
        (SIMULATED_FIRST_NUMBER, MAX_NUMBER) if simulated else (emitter["first_number"], emitter["last_number"])
    )
    used = Invoice.objects.filter(
        is_simulated=simulated,
        establishment=emitter["establishment"],
        expedition_point=emitter["expedition_point"],
    ).values_list("number", flat=True)
    taken = [int(number) for number in used if first <= int(number) <= last]
    candidate = max(taken) + 1 if taken else first
    if candidate > last:
        raise InvoicingError(
            f"Se agotó el rango de numeración autorizado ({first:07d} a {last:07d}). "
            "Pedí un rango nuevo a Factura Segura y actualizá FACTURA_NUMERO_DESDE / HASTA."
        )
    return f"{candidate:07d}"


def _create_invoice(operation, customer: Customer, user, simulated: bool) -> Invoice:
    emitter = settings.INVOICE_EMITTER
    source = {"purchase_order": operation} if isinstance(operation, PurchaseOrder) else {"cash_transaction": operation}
    # Dos emisiones simultáneas pueden elegir el mismo número; la restricción única
    # frena a la segunda y se reintenta con el siguiente.
    for _ in range(3):
        try:
            with transaction.atomic():
                return Invoice.objects.create(
                    **source,
                    requested_by=user,
                    is_simulated=simulated,
                    stamp=emitter["stamp"],
                    establishment=emitter["establishment"],
                    expedition_point=emitter["expedition_point"],
                    number=_reserve_number(simulated),
                    customer_kind=customer.kind,
                    customer_name=customer.name,
                    customer_ruc=customer.ruc,
                    customer_dv=customer.dv,
                    customer_document=customer.document,
                    customer_email=customer.email,
                    description=operation_description(operation)[:200],
                    total=operation_total(operation),
                    issued_at=timezone.now(),
                )
        except IntegrityError:
            if Invoice.objects.filter(**source).exists():
                raise InvoicingError("Esta operación ya tiene factura.")
    raise InvoicingError("No se pudo reservar un número de factura. Probá de nuevo.")


# --- El documento electrónico ---------------------------------------------


def _operation(invoice: Invoice):
    return invoice.purchase_order or invoice.cash_transaction


def _card(brand: str, processing: str, label: str = "") -> dict:
    code = CARD_BRANDS.get(brand, CARD_OTHER)
    card = {"iTiPago": PAY_CARD, "iDenTarj": code, "iForProPa": processing}
    if code == CARD_OTHER:
        card["dDesDenTarj"] = label or "Otra"
    return card


def _payment(invoice: Invoice) -> dict:
    """El medio de pago en códigos del SIFEN, siempre por el total en guaraníes."""
    operation = _operation(invoice)
    if isinstance(operation, PurchaseOrder):
        kind = operation.payment_kind
        if kind == PurchaseOrder.PAY_CARD:
            brand = operation.payment_method.brand if operation.payment_method else PaymentMethod.OTHER
            payment = _card(brand, CARD_ONLINE)
        elif kind == PurchaseOrder.PAY_STRIPE:
            payment = _card(PaymentMethod.OTHER, CARD_ONLINE, "Stripe")
        elif kind == PurchaseOrder.PAY_QR:
            payment = {"iTiPago": PAY_TRANSFER}
        else:  # saldo u otra divisa de la billetera
            payment = {"iTiPago": PAY_E_WALLET}
    else:
        method = operation.payment_method
        if method == CashTransaction.PAY_CARD:
            payment = _card(operation.card_brand, CARD_POS)
        elif method == CashTransaction.PAY_QR:
            payment = {"iTiPago": PAY_TRANSFER}
        else:
            payment = {"iTiPago": PAY_CASH}
    payment.update({"dMonTiPag": str(invoice.total), "cMoneTiPag": "PYG", "dTiCamTiPag": "1"})
    # El orden de las claves no importa al API, pero así se lee como el manual.
    return {"iTiPago": payment.pop("iTiPago"), **payment}


def _receiver(invoice: Invoice) -> dict:
    if invoice.customer_kind == Invoice.TAXPAYER:
        # Los RUC de personas jurídicas empiezan en 80 y tienen ocho dígitos.
        company = len(invoice.customer_ruc) == 8 and invoice.customer_ruc.startswith("80")
        return {
            "iNatRec": "1",
            "iTiOpe": "1",
            "cPaisRec": "PRY",
            "iTiContRec": "2" if company else "1",
            "dRucRec": invoice.customer_ruc,
            "dDVRec": invoice.customer_dv,
            "iTipIDRec": "0",
            "dNumIDRec": "0",
        }
    return {
        "iNatRec": "2",
        "iTiOpe": "2",
        "cPaisRec": "PRY",
        "iTiContRec": "0",
        "dRucRec": "0",
        "dDVRec": "0",
        "iTipIDRec": "1",  # cédula paraguaya
        "dNumIDRec": invoice.customer_document,
    }


def build_summary(invoice: Invoice) -> dict:
    """El DE resumido de ``invoice`` (formato "JSON resumido de Factura Electrónica")."""
    emitter = settings.INVOICE_EMITTER
    operation = _operation(invoice)
    online = isinstance(operation, PurchaseOrder)
    currency = operation.currency if online else operation.currency_out

    de = {"iTipEmi": "1", "iTiDE": "1"}
    if emitter["stamp"]:
        de.update({"dNumTim": emitter["stamp"], "dFeIniT": emitter["stamp_start"]})
    de.update(
        {
            "dEst": invoice.establishment,
            "dPunExp": invoice.expedition_point,
            "dNumDoc": invoice.number,
            "dFeEmiDE": timezone.localtime(invoice.issued_at, INVOICE_TIME_ZONE).strftime("%Y-%m-%dT%H:%M:%S"),
            "iTipTra": "1",
            "iTImp": "1",
            "cMoneOpe": "PYG",
            "dCondTiCam": "1",
            "dTiCam": "1",
            "dRucEm": emitter["ruc"],
            "dDVEmi": emitter["dv"],
            "iTipCont": emitter["taxpayer_type"],
            "dNomEmi": emitter["name"],
            "dDirEmi": emitter["address"],
            "dNumCas": emitter["house_number"],
            "cDepEmi": emitter["department_code"],
            "dDesDepEmi": emitter["department"],
            "cCiuEmi": emitter["city_code"],
            "dDesCiuEmi": emitter["city"],
            "dTelEmi": emitter["phone"],
            "dEmailE": emitter["email"],
            "gActEco": emitter["activities"],
            **_receiver(invoice),
            "dNomRec": invoice.customer_name,
            "dEmailRec": invoice.customer_email,
            "iIndPres": "2" if online else "1",  # operación electrónica o presencial
            "iCondOpe": "1",  # contado
            "gPaConEIni": [_payment(invoice)],
            "iCondCred": "1",
            "dPlazoCre": "0",
            "gCamItem": [
                {
                    "dCodInt": currency.code,
                    "dDesProSer": invoice.description[:120],
                    "cUniMed": "77",
                    "dCantProSer": "1",
                    "dPUniProSer": str(invoice.total),
                    "dDescItem": "0",
                    "dDescGloItem": "0",
                    "dAntPreUniIt": "0",
                    "dAntGloPreUniIt": "0",
                    **ITEM_TAX,
                }
            ],
            "CDC": "0",
            "dCodSeg": "0",
            "dDVId": "0",
            "dSisFact": "1",
            "dInfAdic": f"{invoice.operation_label} de Exchange Pro. Ambiente de prueba, sin validez fiscal.",
        }
    )
    return de


# --- Emitir y reintentar --------------------------------------------------


def issue_for_order(order: PurchaseOrder, customer: Customer, user, *, client=None, allow_unconfirmed=False) -> Invoice:
    return _issue(order, customer, user, client=client, allow_unconfirmed=allow_unconfirmed)


def issue_for_cash(tx: CashTransaction, customer: Customer, user, *, client=None, allow_unconfirmed=False) -> Invoice:
    return _issue(tx, customer, user, client=client, allow_unconfirmed=allow_unconfirmed)


def _issue(operation, customer, user, *, client, allow_unconfirmed) -> Invoice:
    blocker = invoice_blocker(operation)
    if blocker:
        raise InvoicingError(blocker)

    existing = Invoice.objects.filter(
        **({"purchase_order": operation} if isinstance(operation, PurchaseOrder) else {"cash_transaction": operation})
    ).first()
    if existing is not None:
        # Una factura que no llegó a generarse se puede reenviar con datos corregidos.
        if not existing.can_retry:
            raise InvoicingError(f"Esta operación ya tiene la factura {existing.full_number}.")
        for name, value in vars(customer).items():
            setattr(existing, f"customer_{name}", value)
        existing.save()
        return retry(existing, client=client, allow_unconfirmed=allow_unconfirmed)

    client = client or default_client()
    invoice = _create_invoice(operation, customer, user, simulated=client.simulated)
    return send(invoice, client)


def retry(invoice: Invoice, *, client=None, allow_unconfirmed=False) -> Invoice:
    """Vuelve a enviar una factura que falló, con el mismo número."""
    if not invoice.can_retry:
        raise InvoicingError(f"La factura está {invoice.get_status_display().lower()}; no hace falta reenviarla.")
    if invoice.status == Invoice.UNCONFIRMED and not allow_unconfirmed:
        raise InvoicingError(
            "El envío anterior quedó sin confirmar: el documento pudo haberse generado. "
            "Un administrador debe verificarlo con Factura Segura antes de reenviarlo."
        )
    invoice.issued_at = timezone.now()
    invoice.save(update_fields=["issued_at", "updated_at"])
    return send(invoice, client or client_for(invoice))


def send(invoice: Invoice, client) -> Invoice:
    """Calcula y genera el documento en Factura Segura. No lanza: el resultado queda en ``invoice``."""
    de = build_summary(invoice)
    invoice.request_payload = de
    try:
        de = client.calcular_de(de) or de
        invoice.request_payload = de
        response = client.generar_de(de)
    except FacturaSeguraError as exc:
        invoice.status = Invoice.UNCONFIRMED if exc.uncertain else Invoice.ERROR
        invoice.error_message = str(exc)
        invoice.response_payload = {"code": exc.code, "operation_id": exc.operation_id, "results": exc.results}
        invoice.save()
        return invoice

    invoice.cdc = str(response.first.get("CDC", ""))
    invoice.status = Invoice.PROCESSING
    invoice.error_message = ""
    invoice.sifen_status = "Sol.Aprobación"
    invoice.sifen_detail = ""
    invoice.response_payload = {"code": response.code, "operation_id": response.operation_id, "results": response.results}
    invoice.save()
    if client.simulated:
        refresh_status(invoice, client=client)
    return invoice


# --- Estado en el SIFEN ---------------------------------------------------

# estado_sifen -> (estado de la factura, cómo se muestra). Manual de Factura Segura,
# "¿Cómo interpretar los estados de SIFEN retornados por este servicio?".
APPROVAL_STATES = {
    "Aprobado": (Invoice.APPROVED, "Aprobado"),
    "Aprobado con observación": (Invoice.APPROVED, "Aprobado OBS"),
    "Rechazado": (Invoice.REJECTED, "Rechazado"),
    "Reingresado": (Invoice.REJECTED, "Verificar datos (reingresado con otro CDC)"),
    "SOL.APROBACION": (Invoice.PROCESSING, "Sol.Aprobación"),
    "ENVIADO_A_SIFEN": (Invoice.PROCESSING, "Enviado al SIFEN"),
    "REINTENTAR_LOTE": (Invoice.PROCESSING, "Error SIFEN (Apr.), Factura Segura reintenta"),
    "ERROR_SIFEN": (Invoice.REJECTED, "Error SIFEN (Apr.)"),
    "ERROR_ENVIO_LOTE": (Invoice.REJECTED, "Error SIFEN (Apr.)"),
    "ERROR_CONSULTA_LOTE": (Invoice.REJECTED, "Error SIFEN (Apr.)"),
}


def _detail(result: dict, prefix: str) -> str:
    return " · ".join(part for part in (result.get(f"desc_{prefix}"), result.get(f"error_{prefix}")) if part)


def interpret_sifen(result: dict, current: str = Invoice.PROCESSING) -> tuple[str, str, str]:
    """``(estado, etiqueta, detalle)`` a partir de la respuesta de ``get_estado_sifen``.

    El orden lo fija el manual: primero inutilización, después cancelación y por
    último aprobación.
    """
    inu, can = result.get("estado_inu", ""), result.get("estado_can", "")
    if inu == "Aprobado":
        return Invoice.VOIDED, "Inutilizado", _detail(result, "inu")
    if inu in ("Rechazado", "ERROR_SIFEN"):
        label = "Verificar datos (Rech.Inu.)" if inu == "Rechazado" else "Error SIFEN (Inu.)"
        return current, label, _detail(result, "inu")
    if can == "Aprobado":
        return Invoice.CANCELLED, "Cancelado", _detail(result, "can")
    if can in ("Rechazado", "ERROR_SIFEN"):
        label = "Aprobado (Rech.Can.)" if can == "Rechazado" else "Error SIFEN (Can.)"
        return Invoice.APPROVED, label, _detail(result, "can")

    state = result.get("estado_sifen", "")
    status, label = APPROVAL_STATES.get(state, (Invoice.PROCESSING, state or "Sin estado todavía"))
    return status, label, _detail(result, "sifen")


def refresh_status(invoice: Invoice, *, client=None) -> Invoice:
    """Consulta el estado del documento en el SIFEN y lo guarda."""
    if not invoice.cdc:
        raise InvoicingError("La factura todavía no tiene CDC: no llegó a generarse.")
    client = client or client_for(invoice)
    try:
        result = client.get_estado_sifen(invoice.cdc, settings.INVOICE_EMITTER["ruc"])
    except FacturaSeguraError as exc:
        raise InvoicingError(f"No se pudo consultar el estado: {exc}") from exc
    invoice.status, invoice.sifen_status, invoice.sifen_detail = interpret_sifen(result, invoice.status)
    invoice.status_checked_at = timezone.now()
    invoice.save(update_fields=["status", "sifen_status", "sifen_detail", "status_checked_at", "updated_at"])
    return invoice


def refresh_if_due(invoice: Invoice) -> bool:
    """Actualiza una factura en proceso si pasó el intervalo mínimo. Devuelve si consultó."""
    if invoice.status != Invoice.PROCESSING or not invoice.cdc or invoice.is_simulated:
        return False
    last = invoice.status_checked_at or invoice.created_at
    if timezone.now() - last < STATUS_CHECK_INTERVAL:
        return False
    try:
        refresh_status(invoice)
    except InvoicingError:
        return False
    return True


def request_cancellation(invoice: Invoice, *, client=None) -> Invoice:
    """Pide al SIFEN el evento de cancelación de una factura aprobada."""
    if invoice.status != Invoice.APPROVED:
        raise InvoicingError("Sólo se puede cancelar una factura aprobada.")
    client = client or client_for(invoice)
    try:
        client.sol_cancelacion(invoice.cdc, settings.INVOICE_EMITTER["ruc"])
    except FacturaSeguraError as exc:
        raise InvoicingError(f"Factura Segura no aceptó la cancelación: {exc}") from exc
    if client.simulated:
        invoice.status, invoice.sifen_status = Invoice.CANCELLED, "Cancelado"
    else:
        invoice.sifen_status = "Cancelación solicitada"
    invoice.save(update_fields=["status", "sifen_status", "updated_at"])
    return invoice


def download(invoice: Invoice, kind: str) -> bytes:
    """El KuDE (PDF) o el XML firmado de una factura real."""
    if not invoice.can_download:
        raise InvoicingError("Esta factura no tiene archivos en Factura Segura.")
    try:
        return client_for(invoice).download(kind, settings.INVOICE_EMITTER["ruc"], invoice.cdc)
    except FacturaSeguraError as exc:
        raise InvoicingError(str(exc)) from exc
