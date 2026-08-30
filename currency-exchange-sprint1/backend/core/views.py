from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.shortcuts import render, redirect
from django.urls import reverse


def home(request):
    return render(request, "core/home.html")


@login_required
def dashboard(request):
    return render(request, "core/dashboard.html")


@login_required
def account(request):
    return render(request, "core/account.html")


def login_view(request):
    return redirect(reverse("oidc_authentication_init"))


def register_view(request):
    return redirect(f"{reverse('oidc_authentication_init')}?kc_action=register")


def logout_view(request):
    logout(request)
    return redirect(reverse("oidc_logout"))


@login_required
def delete_account_view(request):
    # Sprint 1 placeholder: wire this to Keycloak account deletion in a later ticket.
    return render(request, "core/delete_account.html")
