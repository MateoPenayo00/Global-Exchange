"""Pestaña *Caja*: la caja física que opera un cajero (o un administrador).

La lógica de negocio vive en ``services/cashier.py``; acá sólo se arman las
pantallas, se validan los formularios y se traducen los errores a mensajes.
"""

import csv
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from . import roles as role_lib
from .forms import CashPaymentForm, CashQuoteForm, CashSessionOpenForm
from .models import (
    BASE_CURRENCY_CODE,
    CASH_QUOTE_TIMEOUT,
    CashSession,
    CashTransaction,
    Currency,
    cash_quantum,
    quantize_pyg,
)
from .services import cashier, pricing
from .services.qr import mock_qr_path
from .templatetags.money import guarani, units
from .views import _nav_context, role_required

CASH_ROLES = (role_lib.ADMIN, role_lib.CASHIER)
HISTORY_LIMIT = 30


def _money(amount, currency) -> str:
    if currency.code == BASE_CURRENCY_CODE:
        return f"Gs. {guarani(amount)}"
    return f"{units(amount)} {currency.code}"


def _visible_sessions(user):
    """Sesiones que el usuario puede consultar: las propias, o todas si es admin."""
    sessions = CashSession.objects.all()
    if not role_lib.is_admin(user):
        sessions = sessions.filter(cashier=user)
    return sessions


def _own_transaction(request, tx_id) -> CashTransaction:
    """Una operación de una sesión del propio cajero, o 404."""
    return get_object_or_404(
        CashTransaction.objects.select_related("session", "currency_in", "currency_out"),
        pk=tx_id,
        session__cashier=request.user,
    )


def _visible_transaction(request, tx_id) -> CashTransaction:
    """Para consultar: la operación propia, o cualquiera si es admin (sólo lectura)."""
    tx = get_object_or_404(
        CashTransaction.objects.select_related("session", "currency_in", "currency_out"), pk=tx_id
    )
    if tx.session.cashier_id != request.user.id and not role_lib.is_admin(request.user):
        raise Http404
    return tx


def _sorted_balances(session):
    balances = list(session.balances.select_related("currency"))
    return sorted(balances, key=lambda b: (b.currency.code != BASE_CURRENCY_CODE, b.currency.code))


# --- Pantalla principal ---------------------------------------------------


def _render_desk(request, *, open_form=None, quote_form=None, status=200):
    session = cashier.current_session(request.user)
    context = _nav_context(request)
    context["session"] = session
    if session is None:
        context["open_form"] = open_form or CashSessionOpenForm()
    else:
        context.update(
            {
                "balances": _sorted_balances(session),
                "quote_form": quote_form or CashQuoteForm(),
                # En caja las divisas van en centavos (el guaraní, entero).
                "rate_table": pricing.rate_table(
                    Currency.objects.filter(is_active=True), foreign_decimals=2
                ),
                "pending_tx": cashier.pending_transaction(session),
                "done_transactions": session.transactions.filter(status=CashTransaction.COMPLETED)
                .select_related("currency_in", "currency_out")
                .order_by("-closed_at"),
                "timeout_minutes": int(CASH_QUOTE_TIMEOUT.total_seconds() // 60),
            }
        )
    history = _visible_sessions(request.user)
    if session is not None:
        history = history.exclude(pk=session.pk)
    context["history"] = history.annotate(
        done_count=Count("transactions", filter=Q(transactions__status=CashTransaction.COMPLETED))
    )[:HISTORY_LIMIT]
    context["show_cashier_column"] = role_lib.is_admin(request.user)
    return render(request, "core/cash_desk.html", context, status=status)


@role_required(*CASH_ROLES)
def cash_desk(request):
    """Sin sesión: formulario de apertura. Con sesión: saldos, cotizador y operaciones."""
    return _render_desk(request)


@role_required(*CASH_ROLES)
def cash_session_open(request):
    if request.method != "POST":
        return redirect("cash_desk")
    form = CashSessionOpenForm(request.POST)
    if not form.is_valid():
        return _render_desk(request, open_form=form, status=400)
    try:
        session = cashier.open_session(request.user, form.opening_amounts())
    except cashier.CashierError as exc:
        messages.error(request, str(exc))
        return redirect("cash_desk")
    messages.success(request, f"Abriste la caja #{session.id}. Ya podés atender clientes.")
    return redirect("cash_desk")


@role_required(*CASH_ROLES)
def cash_quote(request):
    """Cotiza una operación y abre su pestaña."""
    if request.method != "POST":
        return redirect("cash_desk")
    session = cashier.current_session(request.user)
    if session is None:
        messages.error(request, "Abrí una sesión de caja antes de operar.")
        return redirect("cash_desk")
    form = CashQuoteForm(request.POST)
    if not form.is_valid():
        return _render_desk(request, quote_form=form, status=400)
    data = form.cleaned_data
    try:
        tx = cashier.quote(session, data["currency_in"], data["currency_out"], data["amount"], data["anchor"])
    except cashier.CashierError as exc:
        messages.error(request, str(exc))
        return _render_desk(request, quote_form=form, status=400)
    return redirect("cash_operation", tx_id=tx.id)


# --- Pestaña de una operación ---------------------------------------------


def _render_operation(request, tx, payment_form=None, status=200):
    context = _nav_context(request)
    context["tx"] = tx
    # Un admin puede ver la operación de otro cajero, pero no completarla.
    context["can_operate"] = tx.session.cashier_id == request.user.id
    if tx.is_pending:
        seconds_left = tx.seconds_left()
        timeout_seconds = int(CASH_QUOTE_TIMEOUT.total_seconds())
        current_in = pricing.rate_in(tx.currency_in)
        current_out = pricing.rate_out(tx.currency_out)
        context.update(
            {
                "payment_form": payment_form or CashPaymentForm(),
                "seconds_left": seconds_left,
                "timeout_seconds": timeout_seconds,
                "timer_percent": 100 * seconds_left // timeout_seconds,
                "rate_changed": (current_in, current_out) != (tx.rate_in, tx.rate_out),
                "current_rate_in": current_in,
                "current_rate_out": current_out,
                "cash_out_available": cashier.available_cash(tx.session, tx.currency_out),
                "qr": mock_qr_path(tx.qr_reference),
            }
        )
    return render(request, "core/cash_operation.html", context, status=status)


@role_required(*CASH_ROLES)
def cash_operation(request, tx_id):
    """Pestaña de la operación: medio de pago y cliente, o el comprobante si ya terminó."""
    tx = _visible_transaction(request, tx_id)
    if tx.is_pending and cashier.expire_due(tx.session):
        tx.refresh_from_db()
    return _render_operation(request, tx)


@role_required(*CASH_ROLES)
def cash_operation_confirm(request, tx_id):
    if request.method != "POST":
        return redirect("cash_operation", tx_id=tx_id)
    tx = _own_transaction(request, tx_id)
    form = CashPaymentForm(request.POST)
    if not form.is_valid():
        return _render_operation(request, tx, payment_form=form, status=400)
    data = form.cleaned_data
    try:
        tx = cashier.confirm(
            tx.id,
            tx.session,
            payment_method=data["payment_method"],
            customer_name=data["customer_name"],
            customer_document=data["customer_document"],
            card_brand=data["card_brand"],
            card_holder=data["card_holder"],
            card_last4=data["card_last4"],
        )
    except CashTransaction.DoesNotExist:
        raise Http404
    except cashier.CashierError as exc:
        messages.error(request, str(exc))
        tx.refresh_from_db()
        return _render_operation(request, tx, payment_form=form, status=400)

    messages.success(
        request,
        f"Operación #{tx.id} realizada: {tx.customer_name} entregó {_money(tx.amount_in, tx.currency_in)} "
        f"y recibió {_money(tx.amount_out, tx.currency_out)}.",
    )
    return redirect("cash_operation", tx_id=tx.id)


@role_required(*CASH_ROLES)
def cash_operation_cancel(request, tx_id):
    if request.method != "POST":
        return redirect("cash_operation", tx_id=tx_id)
    tx = _own_transaction(request, tx_id)
    try:
        tx = cashier.cancel(tx.id, tx.session)
    except cashier.CashierError as exc:
        messages.error(request, str(exc))
        return redirect("cash_operation", tx_id=tx_id)
    if tx.status == CashTransaction.EXPIRED:
        messages.warning(request, f"La operación #{tx.id} ya había vencido; no se movió efectivo.")
    else:
        messages.success(request, f"Cancelaste la operación #{tx.id}. No se movió efectivo.")
    return redirect("cash_desk")


# --- Cierre y reporte -----------------------------------------------------


@role_required(*CASH_ROLES)
def cash_session_close(request):
    """GET: vista previa del reporte y confirmación. POST: cierra la sesión."""
    session = cashier.current_session(request.user)
    if session is None:
        messages.error(request, "No tenés una sesión de caja abierta.")
        return redirect("cash_desk")

    if request.method == "POST":
        session = cashier.close_session(session)
        messages.success(request, f"Cerraste la caja #{session.id}. Este es el reporte de la sesión.")
        return redirect("cash_session_report", session_id=session.id)

    cashier.expire_due(session)
    context = _nav_context(request)
    context.update(
        {
            "report": cashier.session_report(session),
            "pending_tx": session.transactions.filter(status=CashTransaction.PENDING).first(),
        }
    )
    return render(request, "core/cash_close.html", context)


def _visible_session(request, session_id) -> CashSession:
    session = get_object_or_404(CashSession, pk=session_id)
    if session.cashier_id != request.user.id and not role_lib.is_admin(request.user):
        raise PermissionDenied("Sólo el cajero de la sesión o un administrador pueden ver este reporte.")
    return session


@role_required(*CASH_ROLES)
def cash_session_report(request, session_id):
    session = _visible_session(request, session_id)
    context = _nav_context(request)
    context["report"] = cashier.session_report(session)
    return render(request, "core/cash_report.html", context)


def _csv_number(value) -> str:
    # Coma decimal y sin separador de miles: es lo que espera una planilla en español.
    return f"{value:f}".replace(".", ",")


def _csv_amount(value, currency) -> str:
    """Monto con la precisión del efectivo de su divisa: Gs. enteros, divisas en centavos."""
    return _csv_number(Decimal(value).quantize(cash_quantum(currency)))


def _csv_rate(value) -> str:
    """Cotización sin ceros sobrantes: ``8000,000000`` -> ``8000``."""
    return _csv_number(value.normalize())


@role_required(*CASH_ROLES)
def cash_session_report_csv(request, session_id):
    """El mismo reporte en CSV (separado por punto y coma, para Excel en español)."""
    session = _visible_session(request, session_id)
    report = cashier.session_report(session)

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="caja-{session.id}.csv"'
    response.write("﻿")  # BOM: Excel reconoce así los acentos
    writer = csv.writer(response, delimiter=";")
    writer.writerow(["Sesión de caja", session.id])
    writer.writerow(["Cajero", session.cashier_name])
    writer.writerow(["Estado", session.get_status_display()])
    writer.writerow(["Apertura", session.opened_at.strftime("%d/%m/%Y %H:%M")])
    writer.writerow(["Cierre", session.closed_at.strftime("%d/%m/%Y %H:%M") if session.closed_at else ""])
    writer.writerow([])
    writer.writerow(
        ["Divisa", "Apertura", "Entró en efectivo", "Salió en efectivo", "Cierre", "Cobrado con tarjeta", "Cobrado con QR"]
    )
    for row in report.currencies:
        writer.writerow(
            [row.currency.code]
            + [
                _csv_amount(amount, row.currency)
                for amount in (row.opening, row.cash_in, row.cash_out, row.closing, row.card_in, row.qr_in)
            ]
        )
    writer.writerow([])
    writer.writerow(
        [
            "Operación",
            "Fecha",
            "Cliente",
            "Cédula",
            "Tipo",
            "Entregó",
            "Divisa entregada",
            "Recibió",
            "Divisa recibida",
            "Medio de pago",
            "Cotización entregada",
            "Cotización recibida",
            "Valor en Gs.",
        ]
    )
    for tx in report.transactions:
        writer.writerow(
            [
                tx.id,
                tx.closed_at.strftime("%d/%m/%Y %H:%M:%S"),
                tx.customer_name,
                tx.customer_document,
                tx.description,
                _csv_amount(tx.amount_in, tx.currency_in),
                tx.currency_in.code,
                _csv_amount(tx.amount_out, tx.currency_out),
                tx.currency_out.code,
                tx.get_payment_method_display()
                + (f" {tx.get_card_brand_display()} •••• {tx.card_last4} ({tx.card_holder})" if tx.card_last4 else ""),
                _csv_rate(tx.rate_in),
                _csv_rate(tx.rate_out),
                _csv_number(quantize_pyg(tx.pyg_value)),
            ]
        )
    return response
