from datetime import timedelta
from decimal import ROUND_DOWN, Decimal
from functools import wraps
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse

from . import roles as role_lib
from .forms import (
    AdminUserForm,
    AdminUserRolesForm,
    CurrencyBuyForm,
    CurrencyForm,
    CurrencyHistoryForm,
    CurrencySellForm,
    DeleteAccountForm,
    PaymentMethodForm,
    WalletDepositForm,
    WalletWithdrawForm,
)
from .models import (
    BASE_CURRENCY_CODE,
    BASE_CURRENCY_SYMBOL,
    Currency,
    PaymentMethod,
    SimulationState,
    Wallet,
    WalletTransaction,
    quantize_pyg,
)
from .roles import ROLE_LABELS
from .services.charts import build_rate_chart
from .services.keycloak_admin import KeycloakAdminClient, KeycloakAdminError
from .services.simulation import MAX_DAILY_CHANGE, MIN_DAILY_CHANGE, advance_days

# Montos fijos de los botones de prueba de un clic, expresados en guaraníes.
TEST_DEPOSIT_AMOUNT = Decimal("500000.00")
TEST_WITHDRAW_AMOUNT = Decimal("1.000000")

# Atajos de monto en la carga de saldo: rellenan el campo con un clic en lugar de
# obligar a teclear seis dígitos, que es la acción más repetida de la billetera.
DEPOSIT_PRESETS = [Decimal("100000"), Decimal("500000"), Decimal("1000000"), Decimal("5000000")]

CURRENCY_QUANTUM = Decimal("0.000001")


def role_required(*required):
    """Allow the view only for users holding at least one of ``required`` roles."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if not role_lib.has_role(request.user, *required):
                raise PermissionDenied("No tienes permisos para acceder a esta sección.")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def _nav_context(request):
    return {
        "profile_name": request.user.get_full_name() or request.user.get_username(),
        "profile_email": request.user.email,
        "user_roles": sorted(role_lib.user_roles(request.user)),
        "role_labels": ROLE_LABELS,
        "can_manage_users": role_lib.is_admin(request.user),
        "can_manage_currencies": role_lib.is_manager(request.user),
        "can_simulate": role_lib.is_admin(request.user),
        "can_trade": role_lib.is_trader(request.user),
        "base_currency_code": BASE_CURRENCY_CODE,
        "base_currency_symbol": BASE_CURRENCY_SYMBOL,
    }


def home(request):
    context = {"featured_currencies": Currency.objects.filter(is_active=True).exclude(code=BASE_CURRENCY_CODE)}
    return render(request, "core/home.html", context)


@login_required
def dashboard(request):
    return render(request, "core/dashboard.html", _nav_context(request))


@login_required
def account(request):
    return render(request, "core/account.html", _nav_context(request))


def _get_wallet(user) -> Wallet:
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


def _simulated_today():
    """Fecha del reloj simulado, que es la que fecha los puntos del historial."""
    return SimulationState.load().current_date


# --- Billetera del usuario ----------------------------------------------


@role_required(role_lib.USER)
def wallet_view(request):
    """La billetera: saldo en guaraníes, tenencias, medios de pago y movimientos."""
    wallet = _get_wallet(request.user)
    payment_methods = wallet.payment_methods.filter(is_active=True)

    context = _nav_context(request)
    context.update(
        {
            "wallet": wallet,
            "holdings": wallet.holdings.select_related("currency").filter(amount__gt=0),
            "deposit_form": WalletDepositForm(wallet=wallet),
            "withdraw_form": WalletWithdrawForm(wallet=wallet),
            "card_form": PaymentMethodForm(),
            "payment_methods": payment_methods,
            "has_payment_method": payment_methods.exists(),
            "test_deposit_amount": TEST_DEPOSIT_AMOUNT,
            "test_withdraw_amount": TEST_WITHDRAW_AMOUNT,
            "deposit_presets": DEPOSIT_PRESETS,
            "transactions": wallet.transactions.select_related("currency", "payment_method")[:20],
        }
    )
    return render(request, "core/wallet.html", context)


@role_required(role_lib.USER)
def payment_method_create(request):
    """Registra una tarjeta de crédito en la billetera del usuario."""
    wallet = _get_wallet(request.user)
    if request.method == "POST":
        form = PaymentMethodForm(request.POST)
        if form.is_valid():
            card = form.build_payment_method(wallet)
            messages.success(request, f"Tarjeta {card.masked_number} agregada a tu billetera.")
            return redirect("wallet")
    else:
        form = PaymentMethodForm()

    context = _nav_context(request)
    context.update({"card_form": form, "wallet": wallet})
    return render(request, "core/payment_method_form.html", context)


@role_required(role_lib.USER)
def payment_method_delete(request, method_id):
    """Elimina una tarjeta de la billetera (los movimientos pasados se conservan)."""
    wallet = _get_wallet(request.user)
    card = get_object_or_404(PaymentMethod, pk=method_id, wallet=wallet)
    if request.method == "POST":
        label = card.masked_number
        card.delete()
        messages.success(request, f"Tarjeta {label} eliminada.")
        return redirect("wallet")

    context = _nav_context(request)
    context.update({"card": card})
    return render(request, "core/payment_method_delete.html", context)


@role_required(role_lib.USER)
def wallet_deposit(request):
    """Carga saldo en guaraníes cobrando la tarjeta elegida por el usuario."""
    if request.method == "POST":
        wallet = _get_wallet(request.user)
        form = WalletDepositForm(request.POST, wallet=wallet)
        if form.is_valid():
            amount = quantize_pyg(form.cleaned_data["amount"])
            card = form.cleaned_data["payment_method"]
            _credit_balance(wallet, amount, card, note=f"Carga con {card.masked_number}")
            messages.success(request, f"Se cargaron Gs. {amount:,.0f} a tu saldo con {card.masked_number}.".replace(",", "."))
        elif not wallet.payment_methods.filter(is_active=True).exists():
            messages.error(request, "Primero agrega una tarjeta de crédito para poder cargar saldo.")
        else:
            messages.error(request, "Revisa el formulario de carga de saldo: " + form.errors.as_text())
    return redirect("wallet")


@role_required(role_lib.USER)
def wallet_deposit_test(request):
    """Botón de prueba de un clic: carga un monto fijo con la primera tarjeta registrada."""
    if request.method == "POST":
        wallet = _get_wallet(request.user)
        card = wallet.payment_methods.filter(is_active=True).first()
        if card is None:
            messages.error(request, "Primero agrega una tarjeta de crédito para poder cargar saldo.")
        else:
            _credit_balance(wallet, TEST_DEPOSIT_AMOUNT, card, note="Carga de saldo de prueba")
            messages.success(
                request,
                f"Se cargaron Gs. {TEST_DEPOSIT_AMOUNT:,.0f} de prueba con {card.masked_number}.".replace(",", "."),
            )
    return redirect("wallet")


def _credit_balance(wallet, amount, card, note):
    """Suma guaraníes al saldo y deja el movimiento registrado."""
    with transaction.atomic():
        wallet.pyg_balance += amount
        wallet.save(update_fields=["pyg_balance", "updated_at"])
        WalletTransaction.objects.create(
            wallet=wallet,
            kind=WalletTransaction.DEPOSIT,
            payment_method=card,
            amount=amount,
            pyg_amount=amount,
            note=note,
        )


@role_required(role_lib.USER)
def wallet_withdraw(request):
    """Retira divisa de la billetera (la saca del sistema, no la convierte a guaraníes)."""
    if request.method == "POST":
        wallet = _get_wallet(request.user)
        form = WalletWithdrawForm(request.POST, wallet=wallet)
        if form.is_valid():
            currency = form.cleaned_data["currency"]
            amount = form.cleaned_data["amount"]
            holding = wallet.holdings.filter(currency=currency).first()
            if not holding or holding.amount < amount:
                messages.error(request, f"No tienes suficiente saldo de {currency.code} para retirar esa cantidad.")
            else:
                with transaction.atomic():
                    holding.amount -= amount
                    holding.save(update_fields=["amount"])
                    WalletTransaction.objects.create(
                        wallet=wallet,
                        kind=WalletTransaction.WITHDRAW,
                        currency=currency,
                        amount=amount,
                        pyg_amount=quantize_pyg(amount * currency.buy_rate),
                        rate_used=currency.buy_rate,
                        note="Retiro de divisa",
                    )
                messages.success(request, f"Retiraste {amount} {currency.code} de tu billetera.")
        else:
            messages.error(request, "Revisa el formulario de retiro.")
    return redirect("wallet")


@role_required(role_lib.USER)
def wallet_withdraw_test(request, currency_id):
    """Botón de prueba de un clic: retira un monto fijo pequeño de una divisa."""
    if request.method == "POST":
        wallet = _get_wallet(request.user)
        currency = get_object_or_404(Currency, pk=currency_id)
        holding = wallet.holdings.filter(currency=currency).first()
        if not holding or holding.amount <= 0:
            messages.error(request, f"No tienes saldo de {currency.code} para retirar.")
        else:
            amount = min(holding.amount, TEST_WITHDRAW_AMOUNT)
            with transaction.atomic():
                holding.amount -= amount
                holding.save(update_fields=["amount"])
                WalletTransaction.objects.create(
                    wallet=wallet,
                    kind=WalletTransaction.WITHDRAW,
                    currency=currency,
                    amount=amount,
                    pyg_amount=quantize_pyg(amount * currency.buy_rate),
                    rate_used=currency.buy_rate,
                    note="Retiro de prueba",
                )
            messages.success(request, f"Retiraste {amount} {currency.code} (prueba) de tu billetera.")
    return redirect("wallet")


# --- Compra y venta de divisas ------------------------------------------


@role_required(role_lib.USER)
def trade(request):
    """Compra y venta de divisas contra guaraníes.

    * **Comprar** usa el precio de *venta* de la divisa (la casa de cambio vende
      más caro) y puede pagarse con el saldo en guaraníes o con una tarjeta.
    * **Vender** usa el precio de *compra* (la casa de cambio compra más barato)
      y acredita guaraníes en el saldo de la billetera.
    """
    wallet = _get_wallet(request.user)
    action = request.POST.get("action") if request.method == "POST" else None
    # Los dos formularios tienen un campo "currency", así que necesitan prefijos de
    # id distintos para no repetir el mismo id en el HTML (y para que la vista
    # previa de conversión pueda apuntar al selector correcto).
    # ``?currency=`` y ``?sell=`` permiten llegar con la divisa ya elegida desde
    # la pizarra o desde la billetera, para no tener que seleccionarla de nuevo.
    buy_form = CurrencyBuyForm(
        request.POST if action == "buy" else None,
        wallet=wallet,
        auto_id="id_buy_%s",
        initial={"currency": request.GET.get("currency") or None},
    )
    sell_form = CurrencySellForm(
        request.POST if action == "sell" else None,
        wallet=wallet,
        auto_id="id_sell_%s",
        initial={"currency": request.GET.get("sell") or None},
    )

    if action == "buy" and buy_form.is_valid():
        if _process_buy(request, wallet, buy_form):
            return redirect("trade")
    elif action == "sell" and sell_form.is_valid():
        if _process_sell(request, wallet, sell_form):
            return redirect("trade")

    currencies = Currency.objects.filter(is_active=True).exclude(code=BASE_CURRENCY_CODE)
    context = _nav_context(request)
    context.update(
        {
            "buy_form": buy_form,
            "sell_form": sell_form,
            "wallet": wallet,
            "holdings": wallet.holdings.select_related("currency").filter(amount__gt=0),
            "currencies": currencies,
            "rate_table": _rate_table(currencies),
            "wallet_balance_json": str(wallet.pyg_balance),
        }
    )
    return render(request, "core/trade.html", context)


def _rate_table(currencies) -> dict:
    """Cotizaciones en un diccionario simple, para la vista previa de conversión.

    La plantilla lo publica con ``json_script`` y el navegador recalcula con él
    cuánto recibirá el usuario antes de confirmar la compra, sin pedir nada al
    servidor. Los decimales viajan como texto para no perder precisión.
    """
    return {
        str(currency.id): {
            "code": currency.code,
            "name": currency.name,
            "buy_rate": str(currency.buy_rate),
            "sell_rate": str(currency.sell_rate),
        }
        for currency in currencies
    }


def _process_buy(request, wallet, form) -> bool:
    """Ejecuta una compra de divisa. Devuelve ``True`` si se concretó."""
    currency = form.cleaned_data["currency"]
    pyg_amount = quantize_pyg(form.cleaned_data["pyg_amount"])
    card = form.cleaned_data["payment_source"]  # ``None`` significa "pagar con el saldo".

    if card is None and pyg_amount > wallet.pyg_balance:
        messages.error(request, "No tienes saldo suficiente en guaraníes para esta compra.")
        return False

    # Se compra al precio de venta de la divisa: es lo que cobra la casa de cambio.
    rate = currency.sell_rate
    currency_amount = (pyg_amount / rate).quantize(CURRENCY_QUANTUM, rounding=ROUND_DOWN)
    if currency_amount <= 0:
        messages.error(
            request,
            f"El monto es demasiado bajo: 1 {currency.code} cuesta Gs. {rate:,.0f}.".replace(",", "."),
        )
        return False

    with transaction.atomic():
        if card is None:
            wallet.pyg_balance -= pyg_amount
            wallet.save(update_fields=["pyg_balance", "updated_at"])
            note = "Compra con saldo en guaraníes"
        else:
            note = f"Compra con {card.masked_number}"
        holding, _ = wallet.holdings.get_or_create(currency=currency)
        holding.amount += currency_amount
        holding.save(update_fields=["amount"])
        WalletTransaction.objects.create(
            wallet=wallet,
            kind=WalletTransaction.BUY,
            currency=currency,
            payment_method=card,
            amount=currency_amount,
            pyg_amount=pyg_amount,
            rate_used=rate,
            note=note,
        )

    messages.success(
        request,
        f"Compraste {currency_amount} {currency.code} por Gs. {pyg_amount:,.0f} "
        f"(precio de venta Gs. {rate:,.0f}).".replace(",", "."),
    )
    return True


def _process_sell(request, wallet, form) -> bool:
    """Ejecuta una venta de divisa hacia guaraníes. Devuelve ``True`` si se concretó."""
    currency = form.cleaned_data["currency"]
    amount = form.cleaned_data["amount"]
    holding = wallet.holdings.filter(currency=currency).first()

    if not holding or holding.amount < amount:
        messages.error(request, f"No tienes suficiente {currency.code} para vender esa cantidad.")
        return False

    # Se vende al precio de compra de la divisa: es lo que paga la casa de cambio.
    rate = currency.buy_rate
    pyg_amount = quantize_pyg(amount * rate)

    with transaction.atomic():
        holding.amount -= amount
        holding.save(update_fields=["amount"])
        wallet.pyg_balance += pyg_amount
        wallet.save(update_fields=["pyg_balance", "updated_at"])
        WalletTransaction.objects.create(
            wallet=wallet,
            kind=WalletTransaction.SELL,
            currency=currency,
            amount=amount,
            pyg_amount=pyg_amount,
            rate_used=rate,
            note="Venta de divisa",
        )

    messages.success(
        request,
        f"Vendiste {amount} {currency.code} y recibiste Gs. {pyg_amount:,.0f} "
        f"(precio de compra Gs. {rate:,.0f}).".replace(",", "."),
    )
    return True


# --- Historial de cotizaciones ------------------------------------------


def currency_history(request):
    """Pestaña *Cotizaciones*: historial de una divisa en tabla y en gráfico.

    Es la pizarra pública de la casa de cambio, así que no exige iniciar sesión;
    el menú y los permisos los aporta ``core.context_processors.roles``.
    """
    currencies = Currency.objects.exclude(code=BASE_CURRENCY_CODE).order_by("code")
    initial_currency = currencies.first()
    form = CurrencyHistoryForm(
        request.GET or None,
        initial={"currency": initial_currency.id if initial_currency else None, "days": "30"},
    )

    currency = initial_currency
    days = 30
    if form.is_valid():
        currency = form.cleaned_data["currency"]
        days = int(form.cleaned_data["days"])

    rates = []
    if currency is not None:
        queryset = currency.rates.order_by("date")
        if days:
            since = _simulated_today() - timedelta(days=days - 1)
            queryset = queryset.filter(date__gte=since)
        rates = list(queryset)

    context = {}
    context.update(
        {
            "form": form,
            "currency": currency,
            "currencies": currencies,
            "rates": list(reversed(rates)),  # la tabla muestra lo más reciente primero
            "chart": build_rate_chart(rates),
            "days": days,
            "simulated_today": _simulated_today(),
            "variation": _variation(rates),
        }
    )
    return render(request, "core/currency_history.html", context)


def _variation(rates):
    """Variación porcentual de la cotización media entre el primer y el último punto."""
    if len(rates) < 2:
        return None
    first, last = rates[0].mid_rate, rates[-1].mid_rate
    if first == 0:
        return None
    return ((last - first) / first * Decimal("100")).quantize(Decimal("0.01"))


# --- Admin/manager: currency CRUD ---------------------------------------


@role_required(role_lib.ADMIN, role_lib.MANAGER)
def currency_management(request):
    context = _nav_context(request)
    context["currencies"] = Currency.objects.all()
    context["simulated_today"] = _simulated_today()
    return render(request, "core/currency_management.html", context)


@role_required(role_lib.ADMIN, role_lib.MANAGER)
def currency_create(request):
    if request.method == "POST":
        form = CurrencyForm(request.POST)
        if form.is_valid():
            currency = form.save()
            # Toda alta o cambio de cotización queda como punto del historial.
            currency.record_rate(on_date=_simulated_today())
            messages.success(request, f"Divisa {currency.code} creada.")
            return redirect("currency_management")
    else:
        form = CurrencyForm()

    context = _nav_context(request)
    context.update({"form": form, "mode": "create"})
    return render(request, "core/currency_form.html", context)


@role_required(role_lib.ADMIN, role_lib.MANAGER)
def currency_edit(request, currency_id):
    currency = get_object_or_404(Currency, pk=currency_id)
    if request.method == "POST":
        form = CurrencyForm(request.POST, instance=currency)
        if currency.is_base:
            form.fields["code"].disabled = True
            form.fields["buy_rate"].disabled = True
            form.fields["sell_rate"].disabled = True
        if form.is_valid():
            currency = form.save()
            currency.record_rate(on_date=_simulated_today())
            messages.success(request, f"Divisa {currency.code} actualizada.")
            return redirect("currency_management")
    else:
        form = CurrencyForm(instance=currency)
        if currency.is_base:
            form.fields["code"].disabled = True
            form.fields["buy_rate"].disabled = True
            form.fields["sell_rate"].disabled = True

    context = _nav_context(request)
    context.update({"form": form, "mode": "edit", "currency": currency})
    return render(request, "core/currency_form.html", context)


@role_required(role_lib.ADMIN, role_lib.MANAGER)
def currency_delete(request, currency_id):
    currency = get_object_or_404(Currency, pk=currency_id)
    if currency.is_base:
        messages.error(request, f"No puedes eliminar la divisa base ({BASE_CURRENCY_CODE}).")
        return redirect("currency_management")

    if request.method == "POST":
        try:
            currency.delete()
        except ProtectedError:
            messages.error(
                request,
                f"No se puede eliminar {currency.code}: hay billeteras con saldo o movimientos en esa divisa.",
            )
        else:
            messages.success(request, f"Divisa {currency.code} eliminada.")
        return redirect("currency_management")

    context = _nav_context(request)
    context["currency"] = currency
    return render(request, "core/currency_confirm_delete.html", context)


# --- Admin: simulación del paso del tiempo ------------------------------


@role_required(role_lib.ADMIN)
def simulation_panel(request):
    """Panel donde un administrador adelanta el reloj y ve moverse las divisas."""
    state = SimulationState.load()
    context = _nav_context(request)
    context.update(
        {
            "state": state,
            "currencies": Currency.objects.filter(is_active=True).exclude(code=BASE_CURRENCY_CODE),
            "min_change_percent": (MIN_DAILY_CHANGE * Decimal("100")).quantize(Decimal("0.1")),
            "max_change_percent": (MAX_DAILY_CHANGE * Decimal("100")).quantize(Decimal("0.1")),
            "last_changes": request.session.pop("last_simulation_changes", None),
            "last_simulation_days": request.session.pop("last_simulation_days", None),
        }
    )
    return render(request, "core/simulation.html", context)


@role_required(role_lib.ADMIN)
def simulation_advance_day(request):
    """Adelanta un día: cada divisa activa varía entre -5 % y +7 %."""
    return _advance(request, 1)


@role_required(role_lib.ADMIN)
def simulation_advance_week(request):
    """Adelanta siete días de una vez, para ver una tendencia con un solo clic."""
    return _advance(request, 7)


def _advance(request, days):
    if request.method != "POST":
        return redirect("simulation")

    state, changes = advance_days(days)
    # Se guarda un resumen en la sesión para mostrarlo después del redirect.
    request.session["last_simulation_changes"] = [
        {
            "code": change.currency.code,
            "name": change.currency.name,
            "previous_buy": f"{change.previous_buy:f}",
            "previous_sell": f"{change.previous_sell:f}",
            "new_buy": f"{change.new_buy:f}",
            "new_sell": f"{change.new_sell:f}",
            "change_percent": f"{change.change_percent:f}",
            "went_up": change.went_up,
        }
        for change in changes
    ]
    request.session["last_simulation_days"] = days
    label = "un día" if days == 1 else f"{days} días"
    messages.success(
        request,
        f"Simulación adelantada {label}, hasta el {state.current_date:%d/%m/%Y}. "
        f"Se recotizaron {len(changes)} divisas.",
    )
    return redirect("simulation")


# --- Admin: user management tab -----------------------------------------


@role_required(role_lib.ADMIN)
def admin_users(request):
    client = KeycloakAdminClient()
    search = request.GET.get("q", "").strip()
    context = _nav_context(request)
    try:
        context["users"] = client.list_users(search or None)
    except KeycloakAdminError as exc:
        context["users"] = []
        messages.error(request, str(exc))
    context["search"] = search
    return render(request, "core/admin_users.html", context)


@role_required(role_lib.ADMIN)
def admin_user_create(request):
    if request.method == "POST":
        form = AdminUserForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            client = KeycloakAdminClient()
            try:
                client.create_user(
                    username=data["username"],
                    email=data["email"],
                    password=data["password"],
                    first_name=data["first_name"],
                    last_name=data["last_name"],
                    roles=data["roles"],
                    temporary_password=data["temporary_password"],
                )
            except KeycloakAdminError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Usuario {data['username']} creado en Keycloak.")
                return redirect("admin_users")
    else:
        form = AdminUserForm()

    context = _nav_context(request)
    context.update({"form": form, "mode": "create"})
    return render(request, "core/admin_user_form.html", context)


@role_required(role_lib.ADMIN)
def admin_user_roles(request, user_id):
    client = KeycloakAdminClient()
    try:
        kc_user = client.get_user(user_id)
        current_roles = client.get_user_roles(user_id)
    except KeycloakAdminError as exc:
        messages.error(request, str(exc))
        return redirect("admin_users")

    if request.method == "POST":
        form = AdminUserRolesForm(request.POST)
        if form.is_valid():
            try:
                client.set_user_roles(user_id, form.cleaned_data["roles"])
            except KeycloakAdminError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Roles actualizados para {kc_user.get('username')}.")
                return redirect("admin_users")
    else:
        form = AdminUserRolesForm(initial={"roles": sorted(current_roles)})

    context = _nav_context(request)
    context.update({"form": form, "mode": "roles", "target_user": kc_user})
    return render(request, "core/admin_user_form.html", context)


@role_required(role_lib.ADMIN)
def admin_user_delete(request, user_id):
    client = KeycloakAdminClient()
    try:
        kc_user = client.get_user(user_id)
    except KeycloakAdminError as exc:
        messages.error(request, str(exc))
        return redirect("admin_users")

    if kc_user.get("username") == request.user.get_username():
        messages.error(request, "No puedes eliminar tu propia cuenta desde este panel.")
        return redirect("admin_users")

    if request.method == "POST":
        try:
            client.delete_user_by_id(user_id)
        except KeycloakAdminError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Usuario {kc_user.get('username')} eliminado.")
        return redirect("admin_users")

    context = _nav_context(request)
    context["target_user"] = kc_user
    return render(request, "core/admin_user_delete.html", context)


# --- Auth flow ---------------------------------------------------------


def login_view(request):
    return redirect(reverse("oidc_authentication_init"))


def register_view(request):
    return redirect(f"{reverse('oidc_authentication_init')}?kc_action=register")


def logout_view(request):
    id_token = request.session.get("oidc_id_token")
    logout(request)

    if not id_token:
        return redirect(settings.LOGOUT_REDIRECT_URL)

    base_url = f"{request.scheme}://{request.get_host()}"
    logout_url = f"{base_url}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/logout"
    query = urlencode(
        {
            "id_token_hint": id_token,
            "post_logout_redirect_uri": request.build_absolute_uri(reverse("home")),
        }
    )
    return redirect(f"{logout_url}?{query}")


@login_required
def delete_account_view(request):
    if request.method == "POST":
        form = DeleteAccountForm(request.POST)
        if form.is_valid():
            client = KeycloakAdminClient()
            try:
                deleted_record = client.delete_django_user_account(request.user)
            except KeycloakAdminError as exc:
                messages.error(request, str(exc))
            else:
                deleted_username = deleted_record.get("username") or request.user.get_username()
                logout(request)
                messages.success(request, f"La cuenta de Keycloak {deleted_username} se ha eliminado correctamente.")
                return render(
                    request,
                    "core/account_deleted.html",
                    {"deleted_username": deleted_username},
                )
    else:
        form = DeleteAccountForm()

    context = _nav_context(request)
    context["form"] = form
    return render(request, "core/delete_account.html", context)
