from functools import wraps
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.core.exceptions import PermissionDenied
from django.shortcuts import render, redirect
from django.urls import reverse

from . import roles as role_lib
from .forms import AdminUserForm, AdminUserRolesForm, DeleteAccountForm
from .roles import ROLE_LABELS
from .services.keycloak_admin import KeycloakAdminClient, KeycloakAdminError


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


@role_required(role_lib.USER)
def trade(request):
    """Where a user will buy and sell currencies. Placeholder for a future sprint."""
    return render(request, "core/trade.html", _nav_context(request))


@role_required(role_lib.ADMIN, role_lib.MANAGER)
def currency_management(request):
    """Where a manager will set the value of certain currencies. Future capability."""
    return render(request, "core/currency_management.html", _nav_context(request))


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
