from decimal import Decimal

from django import forms

from .models import Currency
from .roles import ALL_ROLES, DEFAULT_ROLE, ROLE_LABELS

_ROLE_CHOICES = [(role, ROLE_LABELS[role]) for role in ALL_ROLES]


class DeleteAccountForm(forms.Form):
    confirmation = forms.CharField(
        label="Escribe DELETE para confirmar",
        max_length=20,
        widget=forms.TextInput(attrs={"autocomplete": "off", "placeholder": "DELETE"}),
        help_text="Escribe DELETE completo para confirmar que quieres eliminar la cuenta actual de Keycloak.",
    )

    def clean_confirmation(self):
        value = self.cleaned_data["confirmation"].strip().upper()
        if value != "DELETE":
            raise forms.ValidationError("Escribe DELETE exactamente para confirmar la eliminación de la cuenta.")
        return value


class AdminUserForm(forms.Form):
    """Used by an admin to create a new Keycloak account from the management tab."""

    username = forms.CharField(label="Nombre de usuario", max_length=150)
    email = forms.EmailField(label="Correo")
    first_name = forms.CharField(label="Nombre", max_length=150, required=False)
    last_name = forms.CharField(label="Apellido", max_length=150, required=False)
    password = forms.CharField(label="Contraseña inicial", min_length=8, widget=forms.PasswordInput)
    temporary_password = forms.BooleanField(
        label="Pedir cambio de contraseña en el primer inicio de sesión",
        required=False,
        initial=True,
    )
    roles = forms.MultipleChoiceField(
        label="Roles",
        choices=_ROLE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        initial=[DEFAULT_ROLE],
    )

    def clean_username(self):
        return self.cleaned_data["username"].strip()

    def clean_roles(self):
        return set(self.cleaned_data["roles"])


class CurrencyForm(forms.ModelForm):
    """Create/edit a currency. Admins and managers can raise or lower value_in_usd here."""

    class Meta:
        model = Currency
        fields = ["code", "name", "symbol", "value_in_usd", "is_active"]
        widgets = {
            "code": forms.TextInput(attrs={"placeholder": "EUR"}),
            "symbol": forms.TextInput(attrs={"placeholder": "€"}),
            "value_in_usd": forms.NumberInput(attrs={"step": "0.000001", "min": "0.000001"}),
        }
        labels = {
            "code": "Código",
            "name": "Nombre",
            "symbol": "Símbolo",
            "value_in_usd": "Valor en USD (divisa universal)",
            "is_active": "Activa",
        }

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()

    def clean_value_in_usd(self):
        value = self.cleaned_data["value_in_usd"]
        if value <= 0:
            raise forms.ValidationError("El valor debe ser mayor que cero.")
        return value


class WalletDepositForm(forms.Form):
    amount = forms.DecimalField(
        label="Monto a cargar (USD)",
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )


class WalletWithdrawForm(forms.Form):
    currency = forms.ModelChoiceField(label="Divisa", queryset=Currency.objects.none())
    amount = forms.DecimalField(
        label="Monto a retirar",
        max_digits=18,
        decimal_places=6,
        min_value=Decimal("0.000001"),
    )

    def __init__(self, *args, wallet=None, **kwargs):
        super().__init__(*args, **kwargs)
        held_ids = wallet.holdings.filter(amount__gt=0).values_list("currency_id", flat=True) if wallet else []
        self.fields["currency"].queryset = Currency.objects.filter(id__in=held_ids)


class CurrencyBuyForm(forms.Form):
    currency = forms.ModelChoiceField(
        label="Divisa a comprar",
        queryset=Currency.objects.filter(is_active=True).exclude(code="USD"),
    )
    usd_amount = forms.DecimalField(
        label="Monto a gastar (USD)",
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )


class AdminUserRolesForm(forms.Form):
    """Edit the roles of an existing account."""

    roles = forms.MultipleChoiceField(
        label="Roles",
        choices=_ROLE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

    def clean_roles(self):
        return set(self.cleaned_data["roles"])
