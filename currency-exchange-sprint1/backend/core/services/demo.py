"""Simulación completa de transacciones con los usuarios de demostración.

``simulate_transactions()`` recorre de punta a punta todos los caminos que puede
tomar una operación, **usando los mismos servicios que las pantallas** (no hay
atajos que escriban en la base por su cuenta):

En línea, con ``demo.customer``:

1. carga de saldo con tarjeta;
2. compras pagadas con cada medio: saldo, tarjeta registrada, otra divisa de la
   billetera, QR y Stripe (el cobro lo confirma ``stripe_payments.handle_event``
   con un evento ``checkout.session.completed`` sintético, igual que el webhook);
3. una compra cancelada por el usuario y otra que vence sin pagarse;
4. una venta de divisa;
5. la factura electrónica de las compras pagadas.

En la caja, con ``demo.cajero``: apertura con efectivo, una operación por cada
caso (el cliente compra, vende y cambia una divisa por otra; en efectivo, tarjeta
y QR), una cotización descartada, facturas de las ventas de divisa y el cierre
con su reporte.

Las facturas se emiten siempre con el cliente **simulado** de Factura Segura,
para no gastar el rango de numeración real. Lo usan el botón *Simular
transacciones* del panel de simulación y el comando ``poblar_demo``.
"""

import random
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .. import roles as role_lib
from ..models import BASE_CURRENCY_CODE, CashSession, CashTransaction, Currency, Invoice, PaymentMethod, PurchaseOrder, Wallet
from ..templatetags.money import guarani, units
from . import cashier, checkout, invoicing, stripe_payments
from .factura_segura import SimulatedClient

# Los mismos usuarios que trae keycloak/realm-export.json: al iniciar sesión por
# Keycloak se reutilizan estas filas (el backend OIDC las busca por username).
DEMO_USERS = {
    "demo.admin": (role_lib.ADMIN, "Ana", "Administradora"),
    "demo.manager": (role_lib.MANAGER, "Gabriel", "Gestor"),
    "demo.customer": (role_lib.USER, "Carlos", "Cliente"),
    "demo.cajero": (role_lib.CASHIER, "Carla", "Cajera"),
}

WALK_IN_CUSTOMERS = [
    ("María Benítez", "3456789"),
    ("José Giménez", "4123456"),
    ("Lucía Ortiz", "5234567"),
    ("Pedro Duarte", "2987654"),
    ("Rosa Cáceres", "6012345"),
]

MIN_BALANCE = Decimal("5000000")
DEPOSIT = Decimal("10000000")
PREFERRED_CODES = ("USD", "EUR", "BRL", "ARS")


@dataclass
class Step:
    area: str
    action: str
    outcome: str
    ok: bool = True
    url: str = ""


@dataclass
class SimulationReport:
    steps: list[Step] = field(default_factory=list)

    def add(self, area, action, outcome, *, ok=True, url=""):
        self.steps.append(Step(area, action, outcome, ok, url))

    @property
    def invoices(self) -> int:
        return sum(1 for step in self.steps if step.action.startswith("Factura"))

    @property
    def failures(self) -> int:
        return sum(1 for step in self.steps if not step.ok)

    def as_dicts(self) -> list[dict]:
        return [vars(step) for step in self.steps]


# --- Usuarios y datos de partida ------------------------------------------


def ensure_demo_users() -> dict:
    """Crea (o completa) los usuarios de demostración con su rol."""
    User = get_user_model()
    users = {}
    for username, (role, first, last) in DEMO_USERS.items():
        user, _ = User.objects.get_or_create(
            username=username,
            defaults={"email": f"{username}@example.com", "first_name": first, "last_name": last},
        )
        user.groups.add(Group.objects.get_or_create(name=role)[0])
        if role == role_lib.ADMIN and not user.is_staff:
            user.is_staff = user.is_superuser = True
            user.save(update_fields=["is_staff", "is_superuser"])
        users[username] = user
    return users


def _currencies() -> list[Currency]:
    """Las divisas activas, primero las habituales; al menos una o no hay nada que simular."""
    active = list(Currency.objects.filter(is_active=True).exclude(code=BASE_CURRENCY_CODE))
    if not active:
        raise checkout.CheckoutError("No hay divisas activas para simular operaciones.")
    active.sort(key=lambda c: (PREFERRED_CODES.index(c.code) if c.code in PREFERRED_CODES else 99, c.code))
    return active


def _pick(currencies, index):
    return currencies[index % len(currencies)]


def _invoice(report, area, operation, customer, user, issue):
    try:
        invoice = issue(operation, customer, user, client=SimulatedClient())
    except invoicing.InvoicingError as exc:
        report.add(area, "Factura", str(exc), ok=False)
        return
    report.add(
        area,
        f"Factura {invoice.full_number}",
        f"{invoice.get_status_display()} · Gs. {guarani(invoice.total)} · {invoice.customer_name}",
        url=reverse("invoice_detail", args=[invoice.id]),
    )


# --- En línea -------------------------------------------------------------


def _buy(report, wallet, currency, pyg_amount, source, label):
    """Abre una compra y la paga con ``source``. Devuelve la orden o ``None``."""
    try:
        order = checkout.create_order(wallet, currency, Decimal(pyg_amount))
        order = checkout.confirm_order(order.id, wallet, source)
    except checkout.CheckoutError as exc:
        report.add("En línea", f"Compra de {currency.code} con {label}", str(exc), ok=False)
        return None
    wallet.refresh_from_db()
    report.add(
        "En línea",
        f"Compra #{order.id} de {currency.code} con {label}",
        f"Pagada: {units(order.currency_amount)} {currency.code} por Gs. {guarani(order.pyg_amount)}",
        url=reverse("purchase_checkout", args=[order.id]),
    )
    return order


def _buy_with_stripe(report, wallet, currency, pyg_amount):
    """Compra cobrada por Stripe: la confirma el mismo código que atiende el webhook."""
    order = checkout.create_order(wallet, currency, Decimal(pyg_amount))
    PurchaseOrder.objects.filter(pk=order.pk).update(stripe_session_id=f"cs_demo_{secrets.token_hex(6)}")
    event = {
        "id": f"evt_demo_{secrets.token_hex(8)}",
        "type": stripe_payments.HANDLED_EVENT,
        "data": {
            "object": {
                "id": f"cs_demo_{order.pk}",
                "payment_status": "paid",
                "payment_intent": f"pi_demo_{secrets.token_hex(6)}",
                "metadata": {"order_id": str(order.pk)},
            }
        },
    }
    outcome = stripe_payments.handle_event(event)
    order.refresh_from_db()
    report.add(
        "En línea",
        f"Compra #{order.id} de {currency.code} con Stripe",
        f"Webhook: {outcome} · {order.get_status_display()}",
        ok=outcome == "completed",
        url=reverse("purchase_checkout", args=[order.id]),
    )
    return order if order.status == PurchaseOrder.COMPLETED else None


def _online_round(user, report, rng):
    wallet, _ = Wallet.objects.get_or_create(user=user)
    card = wallet.payment_methods.filter(is_active=True).first() or PaymentMethod.objects.create(
        wallet=wallet,
        label="Visa de demostración",
        holder_name=user.get_full_name() or user.username,
        brand=PaymentMethod.VISA,
        last4="1111",
        expiry_month=12,
        expiry_year=timezone.now().year + 3,
    )
    if wallet.pyg_balance < MIN_BALANCE:
        checkout.deposit(wallet, DEPOSIT, card, "Carga de saldo (simulación)")
        report.add("En línea", "Carga de saldo", f"Gs. {guarani(DEPOSIT)} con {card.masked_number}")

    currencies = _currencies()
    first, second, third = _pick(currencies, 0), _pick(currencies, 1), _pick(currencies, 2)

    def amount(low, high):
        return rng.randrange(low, high, 10000)  # montos redondos en guaraníes

    paid = [
        _buy(report, wallet, first, amount(700000, 1500000), checkout.BALANCE_KEY, "saldo"),
        _buy(report, wallet, second, amount(500000, 1200000), f"{checkout.CARD_KEY_PREFIX}{card.id}", "tarjeta"),
        _buy_with_stripe(report, wallet, third, amount(300000, 900000)),
        _buy(report, wallet, third, amount(150000, 400000), checkout.QR_KEY, "QR"),
    ]
    # Pagar con otra divisa: la que se acaba de comprar con saldo.
    paid.append(
        _buy(report, wallet, second, amount(100000, 300000), f"{checkout.CURRENCY_KEY_PREFIX}{first.id}", f"{first.code} de la billetera")
    )

    cancelled = checkout.create_order(wallet, second, Decimal("500000"))
    checkout.cancel_order(cancelled.id, wallet)
    report.add("En línea", f"Compra #{cancelled.id} de {second.code}", "Cancelada por el usuario antes de pagar",
               url=reverse("purchase_checkout", args=[cancelled.id]))

    late = checkout.create_order(wallet, first, Decimal("400000"))
    PurchaseOrder.objects.filter(pk=late.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    try:
        checkout.confirm_order(late.id, wallet, checkout.BALANCE_KEY)
    except checkout.CheckoutError as exc:
        report.add("En línea", f"Compra #{late.id} de {first.code}", f"Vencida: {exc}",
                   url=reverse("purchase_checkout", args=[late.id]))

    holding = wallet.holdings.filter(currency=first).first()
    if holding and holding.amount > 0:
        quantity = (holding.amount / 4).quantize(Decimal("0.01"))
        sale = checkout.sell_currency(wallet, first, quantity)
        report.add("En línea", f"Venta de {first.code}", f"{units(quantity)} {first.code} por Gs. {guarani(sale.pyg_amount)}")

    taxpayer = invoicing.Customer(
        kind=Invoice.TAXPAYER, name=user.get_full_name() or user.username, email=user.email or "cliente@example.com",
        ruc="2595733", dv="3",
    )
    consumer = invoicing.Customer(
        kind=Invoice.NON_TAXPAYER, name=taxpayer.name, email=taxpayer.email, document="4567890",
    )
    for index, order in enumerate(order for order in paid if order is not None):
        _invoice(report, "En línea", order, taxpayer if index % 2 == 0 else consumer, user, invoicing.issue_for_order)


# --- Caja -----------------------------------------------------------------


def _cash_round(user, report, rng):
    area = "Caja"
    currencies = _currencies()
    pyg = Currency.objects.get(code=BASE_CURRENCY_CODE)
    session = cashier.current_session(user)
    if session is None:
        opening = {pyg: Decimal("50000000"), **{currency: Decimal("3000") for currency in currencies}}
        session = cashier.open_session(user, opening)
        report.add(area, f"Apertura de caja #{session.id}", f"Gs. {guarani(opening[pyg])} y 3.000 de cada divisa",
                   url=reverse("cash_desk"))

    first, second = _pick(currencies, 0), _pick(currencies, 1)
    customers = rng.sample(WALK_IN_CUSTOMERS, k=4)
    operations = [
        # (entrega, recibe, monto que recibe, medio de pago, tarjeta)
        (pyg, first, Decimal(rng.randrange(50, 300, 10)), CashTransaction.PAY_CASH, {}),
        (second, pyg, Decimal(rng.randrange(200000, 900000, 10000)), CashTransaction.PAY_CASH, {}),
        (second, first, Decimal(rng.randrange(20, 80, 5)), CashTransaction.PAY_CARD,
         {"card_brand": PaymentMethod.MASTERCARD, "card_holder": "CLIENTE DEMO", "card_last4": "4444"}),
        (pyg, second, Decimal(rng.randrange(50, 200, 10)), CashTransaction.PAY_QR, {}),
    ]
    for (currency_in, currency_out, amount_out, method, card), (name, document) in zip(operations, customers):
        try:
            tx = cashier.quote(session, currency_in, currency_out, amount_out)
            tx = cashier.confirm(tx.id, session, payment_method=method, customer_name=name,
                                 customer_document=document, **card)
        except cashier.CashierError as exc:
            report.add(area, f"{currency_in.code} → {currency_out.code}", str(exc), ok=False)
            continue
        report.add(area, f"Operación #{tx.id}: {tx.description}",
                   f"{name} entregó {units(tx.amount_in)} {currency_in.code} ({tx.get_payment_method_display().lower()}) "
                   f"y recibió {units(tx.amount_out)} {currency_out.code}",
                   url=reverse("cash_operation", args=[tx.id]))
        if not invoicing.invoice_blocker(tx):
            customer = invoicing.Customer(kind=Invoice.NON_TAXPAYER, name=name, email="cliente.caja@example.com",
                                          document=document)
            _invoice(report, area, tx, customer, user, invoicing.issue_for_cash)

    discarded = cashier.quote(session, pyg, first, Decimal("10"))
    cashier.cancel(discarded.id, session)
    report.add(area, f"Operación #{discarded.id}", "Cotizada y descartada: no mueve efectivo")

    session = cashier.close_session(session)
    summary = cashier.session_report(session)
    balanced = all(row.expected == row.closing for row in summary.currencies)
    report.add(area, f"Cierre de caja #{session.id}",
               f"{len(summary.transactions)} operaciones · Gs. {guarani(summary.total_pyg_value)} · "
               f"{'cuadra' if balanced else 'NO cuadra'}",
               ok=balanced, url=reverse("cash_session_report", args=[session.id]))


# --- Punto de entrada -----------------------------------------------------


def simulate_transactions(*, rng=None) -> SimulationReport:
    """Ejecuta una ronda completa de operaciones en línea y de caja."""
    rng = rng or random.Random()
    users = ensure_demo_users()
    report = SimulationReport()
    with transaction.atomic():
        _online_round(users["demo.customer"], report, rng)
    with transaction.atomic():
        _cash_round(users["demo.cajero"], report, rng)
    return report


def has_demo_activity() -> bool:
    """Si ya se simularon operaciones (para que ``poblar_demo`` no duplique datos)."""
    return Wallet.objects.filter(user__username="demo.customer", transactions__isnull=False).exists() or (
        CashSession.objects.filter(cashier__username="demo.cajero").exists()
    )
