from datetime import date
from decimal import Decimal

from django import forms

from .models import BASE_CURRENCY_CODE, Currency, PaymentMethod
from .roles import ALL_ROLES, DEFAULT_ROLE, ROLE_LABELS

_ROLE_CHOICES = [(role, ROLE_LABELS[role]) for role in ALL_ROLES]

# Fuente de pago elegible al comprar divisas cuando se usa el saldo ya cargado.
WALLET_SOURCE = "wallet"


def _luhn_is_valid(number: str) -> bool:
    """Valida un número de tarjeta con el algoritmo de Luhn (dígito verificador)."""
    total = 0
    for position, char in enumerate(reversed(number)):
        digit = int(char)
        if position % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _detect_brand(number: str) -> str:
    """Deduce la marca de la tarjeta a partir de sus primeros dígitos."""
    if number.startswith("4"):
        return PaymentMethod.VISA
    if number[:2] in {"51", "52", "53", "54", "55"} or number[:4].isdigit() and "2221" <= number[:4] <= "2720":
        return PaymentMethod.MASTERCARD
    if number[:2] in {"34", "37"}:
        return PaymentMethod.AMEX
    return PaymentMethod.OTHER


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
    """Alta/edición de una divisa, con su precio de compra y de venta en guaraníes."""

    class Meta:
        model = Currency
        fields = ["code", "name", "symbol", "buy_rate", "sell_rate", "is_active"]
        widgets = {
            "code": forms.TextInput(attrs={"placeholder": "EUR"}),
            "symbol": forms.TextInput(attrs={"placeholder": "€"}),
            "buy_rate": forms.NumberInput(attrs={"step": "0.000001", "min": "0.000001"}),
            "sell_rate": forms.NumberInput(attrs={"step": "0.000001", "min": "0.000001"}),
        }
        labels = {
            "code": "Código",
            "name": "Nombre",
            "symbol": "Símbolo",
            "buy_rate": "Precio de compra (Gs. que paga la casa de cambio)",
            "sell_rate": "Precio de venta (Gs. que cobra la casa de cambio)",
            "is_active": "Activa",
        }

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()

    def clean_buy_rate(self):
        value = self.cleaned_data["buy_rate"]
        if value <= 0:
            raise forms.ValidationError("El precio de compra debe ser mayor que cero.")
        return value

    def clean_sell_rate(self):
        value = self.cleaned_data["sell_rate"]
        if value <= 0:
            raise forms.ValidationError("El precio de venta debe ser mayor que cero.")
        return value

    def clean(self):
        cleaned = super().clean()
        code = cleaned.get("code")
        buy_rate = cleaned.get("buy_rate")
        sell_rate = cleaned.get("sell_rate")
        if code == BASE_CURRENCY_CODE:
            # El guaraní es la divisa base: su cotización no se edita.
            return cleaned
        if buy_rate is not None and sell_rate is not None and buy_rate >= sell_rate:
            raise forms.ValidationError(
                "El precio de compra debe ser menor que el precio de venta: la casa de cambio "
                "compra más barato de lo que vende."
            )
        return cleaned


class PaymentMethodForm(forms.Form):
    """Registro de una tarjeta de crédito en la billetera del usuario.

    Del número de tarjeta sólo se conservan los últimos cuatro dígitos; el resto
    se usa para validar y deducir la marca, y luego se descarta.
    """

    label = forms.CharField(label="Alias de la tarjeta", max_length=40, initial="Mi tarjeta")
    holder_name = forms.CharField(label="Nombre del titular", max_length=80)
    card_number = forms.CharField(
        label="Número de tarjeta",
        max_length=25,
        widget=forms.TextInput(attrs={"placeholder": "4111 1111 1111 1111", "autocomplete": "off"}),
        help_text="Sólo se guardan los últimos cuatro dígitos. No uses una tarjeta real.",
    )
    expiry_month = forms.IntegerField(label="Mes de vencimiento", min_value=1, max_value=12)
    expiry_year = forms.IntegerField(label="Año de vencimiento", min_value=2000, max_value=2100)

    def clean_card_number(self):
        raw = self.cleaned_data["card_number"]
        digits = "".join(char for char in raw if char.isdigit())
        if not 13 <= len(digits) <= 19:
            raise forms.ValidationError("El número de tarjeta debe tener entre 13 y 19 dígitos.")
        if not _luhn_is_valid(digits):
            raise forms.ValidationError(
                "El número de tarjeta no es válido (no pasa el dígito verificador). "
                "Puedes usar 4111 1111 1111 1111 para probar."
            )
        return digits

    def clean(self):
        cleaned = super().clean()
        month = cleaned.get("expiry_month")
        year = cleaned.get("expiry_year")
        if month and year:
            today = date.today()
            if (year, month) < (today.year, today.month):
                raise forms.ValidationError("La tarjeta ya está vencida.")
        return cleaned

    def build_payment_method(self, wallet) -> PaymentMethod:
        """Crea el ``PaymentMethod`` guardando sólo los datos no sensibles."""
        digits = self.cleaned_data["card_number"]
        return PaymentMethod.objects.create(
            wallet=wallet,
            label=self.cleaned_data["label"],
            holder_name=self.cleaned_data["holder_name"],
            brand=_detect_brand(digits),
            last4=digits[-4:],
            expiry_month=self.cleaned_data["expiry_month"],
            expiry_year=self.cleaned_data["expiry_year"],
        )


class WalletDepositForm(forms.Form):
    """Carga de saldo en guaraníes, siempre asociada a una tarjeta registrada."""

    amount = forms.DecimalField(
        label="Monto a cargar (Gs.)",
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("1"),
    )
    payment_method = forms.ModelChoiceField(
        label="Medio de pago",
        queryset=PaymentMethod.objects.none(),
        empty_label=None,
    )

    def __init__(self, *args, wallet=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payment_method"].queryset = (
            wallet.payment_methods.filter(is_active=True) if wallet else PaymentMethod.objects.none()
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
    """Compra de divisa: se paga en guaraníes al precio de **venta** de la divisa."""

    currency = forms.ModelChoiceField(
        label="Divisa a comprar",
        queryset=Currency.objects.filter(is_active=True).exclude(code=BASE_CURRENCY_CODE),
    )
    pyg_amount = forms.DecimalField(
        label="Monto a gastar (Gs.)",
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("1"),
    )
    payment_source = forms.ChoiceField(
        label="Pagar con",
        choices=[],
        help_text="Puedes usar el saldo que ya tienes en guaraníes o cargar directamente una tarjeta.",
    )

    def __init__(self, *args, wallet=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.wallet = wallet
        self._cards = list(wallet.payment_methods.filter(is_active=True)) if wallet else []
        balance = wallet.pyg_balance if wallet else Decimal("0")
        choices = [(WALLET_SOURCE, f"Saldo en guaraníes (Gs. {balance:,.0f})".replace(",", "."))]
        choices += [(str(card.id), f"{card.label} · {card.get_brand_display()} {card.masked_number}") for card in self._cards]
        self.fields["payment_source"].choices = choices

    def clean_payment_source(self):
        value = self.cleaned_data["payment_source"]
        if value == WALLET_SOURCE:
            return None
        card = next((card for card in self._cards if str(card.id) == value), None)
        if card is None:
            raise forms.ValidationError("Elige un medio de pago válido.")
        return card


class CurrencySellForm(forms.Form):
    """Venta de divisa: la casa de cambio paga en guaraníes al precio de **compra**."""

    currency = forms.ModelChoiceField(label="Divisa a vender", queryset=Currency.objects.none())
    amount = forms.DecimalField(
        label="Cantidad a vender",
        max_digits=18,
        decimal_places=6,
        min_value=Decimal("0.000001"),
    )

    def __init__(self, *args, wallet=None, **kwargs):
        super().__init__(*args, **kwargs)
        held_ids = wallet.holdings.filter(amount__gt=0).values_list("currency_id", flat=True) if wallet else []
        self.fields["currency"].queryset = Currency.objects.filter(id__in=held_ids)


class CurrencyHistoryForm(forms.Form):
    """Selector de la pestaña *Cotizaciones*: qué divisa y cuántos días mostrar."""

    RANGE_CHOICES = [
        ("7", "Últimos 7 días"),
        ("30", "Últimos 30 días"),
        ("90", "Últimos 90 días"),
        ("0", "Todo el historial"),
    ]

    currency = forms.ModelChoiceField(
        label="Divisa",
        queryset=Currency.objects.exclude(code=BASE_CURRENCY_CODE),
        empty_label=None,
    )
    days = forms.ChoiceField(label="Período", choices=RANGE_CHOICES, initial="30")


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
