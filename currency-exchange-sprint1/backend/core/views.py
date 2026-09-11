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
    DeleteAccountForm,
    WalletDepositForm,
    WalletWithdrawForm,
)
from .models import Currency, Wallet, WalletTransaction
from .roles import ROLE_LABELS
from .services.keycloak_admin import KeycloakAdminClient, KeycloakAdminError

TEST_DEPOSIT_AMOUNT = Decimal("100.00")
TEST_WITHDRAW_AMOUNT = Decimal("1.000000")


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
        "can_trade": role_lib.is_trader(request.user),
    }


def home(request):
    return render(request, "core/home.html")


@login_required
def dashboard(request):
    return render(request, "core/dashboard.html", _nav_context(request))


@login_required
def account(request):
    return render(request, "core/account.html", _nav_context(request))


def _get_wallet(user) -> Wallet:
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


@role_required(role_lib.USER)
def wallet_view(request):
    """A user's wallet: USD balance, currency holdings, deposit and withdraw."""
    wallet = _get_wallet(request.user)
    deposit_form = WalletDepositForm()
    withdraw_form = WalletWithdrawForm(wallet=wallet)

    context = _nav_context(request)
    context.update(
        {
            "wallet": wallet,
            "holdings": wallet.holdings.select_related("currency").filter(amount__gt=0),
            "deposit_form": deposit_form,
            "withdraw_form": withdraw_form,
            "test_deposit_amount": TEST_DEPOSIT_AMOUNT,
            "test_withdraw_amount": TEST_WITHDRAW_AMOUNT,
            "transactions": wallet.transactions.select_related("currency")[:20],
        }
    )
    return render(request, "core/wallet.html", context)


@role_required(role_lib.USER)
def wallet_deposit(request):
    if request.method == "POST":
        wallet = _get_wallet(request.user)
        form = WalletDepositForm(request.POST)
        if form.is_valid():
            amount = form.cleaned_data["amount"]
            with transaction.atomic():
                wallet.usd_balance += amount
                wallet.save(update_fields=["usd_balance", "updated_at"])
                WalletTransaction.objects.create(
                    wallet=wallet,
                    kind=WalletTransaction.DEPOSIT,
                    amount=amount,
                    usd_amount=amount,
                    note="Carga de saldo",
                )
            messages.success(request, f"Se cargaron ${amount} a tu saldo.")
        else:
            messages.error(request, "Monto inválido. " + " ".join(form.errors.get("amount", [])))
    return redirect("wallet")


@role_required(role_lib.USER)
def wallet_deposit_test(request):
    """One-click test button: adds a fixed demo amount to the USD balance."""
    if request.method == "POST":
        wallet = _get_wallet(request.user)
        with transaction.atomic():
            wallet.usd_balance += TEST_DEPOSIT_AMOUNT
            wallet.save(update_fields=["usd_balance", "updated_at"])
            WalletTransaction.objects.create(
                wallet=wallet,
                kind=WalletTransaction.DEPOSIT,
                amount=TEST_DEPOSIT_AMOUNT,
                usd_amount=TEST_DEPOSIT_AMOUNT,
                note="Carga de saldo de prueba",
            )
        messages.success(request, f"Se cargaron ${TEST_DEPOSIT_AMOUNT} de prueba a tu saldo.")
    return redirect("wallet")


@role_required(role_lib.USER)
def wallet_withdraw(request):
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
                        usd_amount=(amount * currency.value_in_usd).quantize(Decimal("0.01")),
                        rate_used=currency.value_in_usd,
                        note="Retiro de divisa",
                    )
                messages.success(request, f"Retiraste {amount} {currency.code} de tu billetera.")
        else:
            messages.error(request, "Revisa el formulario de retiro.")
    return redirect("wallet")


@role_required(role_lib.USER)
def wallet_withdraw_test(request, currency_id):
    """One-click test button: withdraws a small fixed demo amount of one currency."""
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
                    usd_amount=(amount * currency.value_in_usd).quantize(Decimal("0.01")),
                    rate_used=currency.value_in_usd,
                    note="Retiro de prueba",
                )
            messages.success(request, f"Retiraste {amount} {currency.code} (prueba) de tu billetera.")
    return redirect("wallet")


@role_required(role_lib.USER)
def trade(request):
    """A user buys currencies for their personal wallet using their USD balance."""
    wallet = _get_wallet(request.user)

    if request.method == "POST":
        form = CurrencyBuyForm(request.POST)
        if form.is_valid():
            currency = form.cleaned_data["currency"]
            usd_amount = form.cleaned_data["usd_amount"]
            if usd_amount > wallet.usd_balance:
                messages.error(request, "No tienes saldo suficiente en USD para esta compra.")
            else:
                currency_amount = (usd_amount / currency.value_in_usd).quantize(
                    Decimal("0.000001"), rounding=ROUND_DOWN
                )
                with transaction.atomic():
                    wallet.usd_balance -= usd_amount
                    wallet.save(update_fields=["usd_balance", "updated_at"])
                    holding, _ = wallet.holdings.get_or_create(currency=currency)
                    holding.amount += currency_amount
                    holding.save(update_fields=["amount"])
                    WalletTransaction.objects.create(
                        wallet=wallet,
                        kind=WalletTransaction.BUY,
                        currency=currency,
                        amount=currency_amount,
                        usd_amount=usd_amount,
                        rate_used=currency.value_in_usd,
                        note="Compra de divisa",
                    )
                messages.success(
                    request, f"Compraste {currency_amount} {currency.code} por ${usd_amount}."
                )
                return redirect("trade")
    else:
        form = CurrencyBuyForm()

    context = _nav_context(request)
    context.update({"form": form, "wallet": wallet})
    return render(request, "core/trade.html", context)


# --- Admin/manager: currency CRUD ---------------------------------------


@role_required(role_lib.ADMIN, role_lib.MANAGER)
def currency_management(request):
    context = _nav_context(request)
    context["currencies"] = Currency.objects.all()
    return render(request, "core/currency_management.html", context)


@role_required(role_lib.ADMIN, role_lib.MANAGER)
def currency_create(request):
    if request.method == "POST":
        form = CurrencyForm(request.POST)
        if form.is_valid():
            currency = form.save()
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
            form.fields["value_in_usd"].disabled = True
        if form.is_valid():
            form.save()
            messages.success(request, f"Divisa {currency.code} actualizada.")
            return redirect("currency_management")
    else:
        form = CurrencyForm(instance=currency)
        if currency.is_base:
            form.fields["code"].disabled = True
            form.fields["value_in_usd"].disabled = True

    context = _nav_context(request)
    context.update({"form": form, "mode": "edit", "currency": currency})
    return render(request, "core/currency_form.html", context)


@role_required(role_lib.ADMIN, role_lib.MANAGER)
def currency_delete(request, currency_id):
    currency = get_object_or_404(Currency, pk=currency_id)
    if currency.is_base:
        messages.error(request, "No puedes eliminar la divisa universal (USD).")
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
