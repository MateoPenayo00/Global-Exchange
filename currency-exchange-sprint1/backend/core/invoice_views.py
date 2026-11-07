"""Pestaña *Facturas*: emisión, consulta y descarga de facturas electrónicas.

La lógica vive en ``services/invoicing.py``; acá se arman las pantallas y se
decide quién ve qué:

* un **usuario** ve las facturas de sus compras en línea;
* un **cajero** ve las de las operaciones de sus sesiones de caja;
* un **administrador** ve todas, y es el único que puede cancelar una factura
  o reenviar una que quedó sin confirmar.
"""

from django.conf import settings
from django.contrib import messages
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from . import roles as role_lib
from .cashier_views import CASH_ROLES, _visible_transaction
from .forms import InvoiceCustomerForm
from .models import Invoice, PurchaseOrder
from .services import invoicing
from .services.factura_segura import DOWNLOAD_KUDE, DOWNLOAD_XML
from .views import _get_wallet, _nav_context, role_required

INVOICE_ROLES = (role_lib.USER, *CASH_ROLES)
LIST_LIMIT = 100


def _visible_invoices(user):
    invoices = Invoice.objects.select_related(
        "purchase_order__currency", "cash_transaction__session", "cash_transaction__currency_out"
    )
    if role_lib.is_admin(user):
        return invoices
    return invoices.filter(Q(purchase_order__wallet__user=user) | Q(cash_transaction__session__cashier=user))


def _visible_invoice(request, invoice_id) -> Invoice:
    """La factura si el usuario puede verla; si no, 404 (no se revela que existe)."""
    return get_object_or_404(_visible_invoices(request.user), pk=invoice_id)


def _report(request, invoice: Invoice) -> None:
    """Traduce el resultado de una emisión a un mensaje para el usuario."""
    number = invoice.full_number
    if invoice.status == Invoice.ERROR:
        messages.error(request, f"No se pudo generar la factura {number}: {invoice.error_message}")
    elif invoice.status == Invoice.UNCONFIRMED:
        messages.warning(
            request,
            f"Factura Segura no respondió a tiempo y la factura {number} quedó sin confirmar. "
            "Un administrador debe verificarla antes de reenviarla.",
        )
    elif invoice.is_simulated:
        messages.success(request, f"Factura simulada {number} emitida (no se envió a Factura Segura).")
    elif invoice.status == Invoice.APPROVED:
        messages.success(request, f"Factura {number} aprobada por el SIFEN.")
    else:
        messages.success(
            request,
            f"Factura {number} generada. El SIFEN la está procesando: en un minuto podés actualizar su estado.",
        )


# --- Listado y detalle ----------------------------------------------------


@role_required(*INVOICE_ROLES)
def invoice_list(request):
    invoices = _visible_invoices(request.user)
    status = request.GET.get("status", "")
    if status in dict(Invoice.STATUS_CHOICES):
        invoices = invoices.filter(status=status)
    context = _nav_context(request)
    context.update(
        {
            "invoices": invoices[:LIST_LIMIT],
            "status_filter": status,
            "status_choices": Invoice.STATUS_CHOICES,
            "live": invoicing.is_live(),
        }
    )
    return render(request, "core/invoices.html", context)


@role_required(*INVOICE_ROLES)
def invoice_detail(request, invoice_id):
    """La factura con el formato del KuDE, imprimible, más su estado en el SIFEN."""
    invoice = _visible_invoice(request, invoice_id)
    invoicing.refresh_if_due(invoice)
    if invoice.purchase_order_id:
        payment_label = invoice.purchase_order.get_payment_kind_display()
    else:
        payment_label = invoice.cash_transaction.get_payment_method_display()
    context = _nav_context(request)
    context.update(
        {
            "invoice": invoice,
            "emitter": settings.INVOICE_EMITTER,
            "is_admin": role_lib.is_admin(request.user),
            "payment_label": payment_label,
        }
    )
    return render(request, "core/invoice_detail.html", context)


# --- Solicitar la factura de una operación ---------------------------------


def _request_invoice(request, operation, *, issue, initial, back_url):
    existing = getattr(operation, "invoice", None)
    if existing is not None and not existing.can_retry:
        return redirect("invoice_detail", invoice_id=existing.id)
    blocker = invoicing.invoice_blocker(operation)
    if blocker:
        messages.error(request, blocker)
        return redirect(back_url)

    if request.method == "POST":
        form = InvoiceCustomerForm(request.POST)
        if form.is_valid():
            try:
                invoice = issue(
                    operation,
                    form.customer(),
                    request.user,
                    allow_unconfirmed=role_lib.is_admin(request.user),
                )
            except invoicing.InvoicingError as exc:
                messages.error(request, str(exc))
            else:
                _report(request, invoice)
                return redirect("invoice_detail", invoice_id=invoice.id)
    else:
        if existing is not None:
            initial = {
                "customer_name": existing.customer_name,
                "tax_id": existing.customer_tax_id,
                "customer_email": existing.customer_email,
            }
        form = InvoiceCustomerForm(initial=initial)

    context = _nav_context(request)
    context.update(
        {
            "form": form,
            "operation_label": f"{'Compra en línea' if isinstance(operation, PurchaseOrder) else 'Operación de caja'} #{operation.pk}",
            "description": invoicing.operation_description(operation),
            "total": invoicing.operation_total(operation),
            "existing": existing,
            "back_url": back_url,
            "live": invoicing.is_live(),
        }
    )
    return render(request, "core/invoice_request.html", context)


@role_required(role_lib.USER)
def order_invoice(request, order_id):
    """Factura de una compra en línea del propio usuario."""
    wallet = _get_wallet(request.user)
    order = get_object_or_404(PurchaseOrder.objects.select_related("currency"), pk=order_id, wallet=wallet)
    user = request.user
    return _request_invoice(
        request,
        order,
        issue=invoicing.issue_for_order,
        initial={"customer_name": user.get_full_name(), "customer_email": user.email},
        back_url=reverse("purchase_checkout", args=[order.id]),
    )


@role_required(*CASH_ROLES)
def cash_invoice(request, tx_id):
    """Factura de una operación de caja: nombre y cédula ya vienen de la operación."""
    tx = _visible_transaction(request, tx_id)
    return _request_invoice(
        request,
        tx,
        issue=invoicing.issue_for_cash,
        initial={"customer_name": tx.customer_name, "tax_id": tx.customer_document},
        back_url=reverse("cash_operation", args=[tx.id]),
    )


# --- Acciones sobre una factura -------------------------------------------


@role_required(*INVOICE_ROLES)
def invoice_refresh(request, invoice_id):
    if request.method == "POST":
        invoice = _visible_invoice(request, invoice_id)
        try:
            invoicing.refresh_status(invoice)
        except invoicing.InvoicingError as exc:
            messages.error(request, str(exc))
        else:
            messages.info(request, f"Estado en el SIFEN: {invoice.sifen_status or invoice.get_status_display()}.")
    return redirect("invoice_detail", invoice_id=invoice_id)


@role_required(*INVOICE_ROLES)
def invoice_retry(request, invoice_id):
    if request.method == "POST":
        invoice = _visible_invoice(request, invoice_id)
        try:
            invoice = invoicing.retry(invoice, allow_unconfirmed=role_lib.is_admin(request.user))
        except invoicing.InvoicingError as exc:
            messages.error(request, str(exc))
        else:
            _report(request, invoice)
    return redirect("invoice_detail", invoice_id=invoice_id)


@role_required(role_lib.ADMIN)
def invoice_cancel(request, invoice_id):
    if request.method == "POST":
        invoice = _visible_invoice(request, invoice_id)
        try:
            invoicing.request_cancellation(invoice)
        except invoicing.InvoicingError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Factura {invoice.full_number}: {invoice.sifen_status.lower()}.")
    return redirect("invoice_detail", invoice_id=invoice_id)


def _download(request, invoice_id, kind, extension, content_type):
    invoice = _visible_invoice(request, invoice_id)
    try:
        content = invoicing.download(invoice, kind)
    except invoicing.InvoicingError as exc:
        if not invoice.can_download:
            raise Http404(str(exc))
        messages.error(request, str(exc))
        return redirect("invoice_detail", invoice_id=invoice_id)
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="factura-{invoice.full_number}.{extension}"'
    return response


@role_required(*INVOICE_ROLES)
def invoice_kude(request, invoice_id):
    """Descarga el KuDE en PDF generado por Factura Segura."""
    return _download(request, invoice_id, DOWNLOAD_KUDE, "pdf", "application/pdf")


@role_required(*INVOICE_ROLES)
def invoice_xml(request, invoice_id):
    """Descarga el XML firmado del documento electrónico."""
    return _download(request, invoice_id, DOWNLOAD_XML, "xml", "application/xml")
