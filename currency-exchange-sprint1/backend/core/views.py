from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.shortcuts import render, redirect
from django.urls import reverse

from .forms import DeleteAccountForm
from .services.keycloak_admin import KeycloakAdminClient, KeycloakAdminError


def home(request):
    return render(request, "core/home.html")


@login_required
def dashboard(request):
    return render(
        request,
        "core/dashboard.html",
        {
            "profile_name": request.user.get_full_name() or request.user.get_username(),
            "profile_email": request.user.email,
        },
    )


@login_required
def account(request):
    return render(
        request,
        "core/account.html",
        {
            "profile_name": request.user.get_full_name() or request.user.get_username(),
            "profile_email": request.user.email,
        },
    )


def login_view(request):
    return redirect(reverse("oidc_authentication_init"))


def register_view(request):
    return redirect(f"{reverse('oidc_authentication_init')}?kc_action=register")


def logout_view(request):
    id_token = request.session.get("oidc_id_token")
    logout(request)

    if not id_token:
        return redirect(settings.LOGOUT_REDIRECT_URL)

    logout_url = f"{settings.KEYCLOAK_PUBLIC_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/logout"
    query = urlencode(
        {
            "id_token_hint": id_token,
            "post_logout_redirect_uri": f"{settings.SITE_URL}{reverse('home')}",
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
                messages.success(request, f"Keycloak account {deleted_username} deleted successfully.")
                return render(
                    request,
                    "core/account_deleted.html",
                    {
                        "deleted_username": deleted_username,
                    },
                )
    else:
        form = DeleteAccountForm()

    return render(
        request,
        "core/delete_account.html",
        {
            "form": form,
            "profile_name": request.user.get_full_name() or request.user.get_username(),
            "profile_email": request.user.email,
        },
    )
