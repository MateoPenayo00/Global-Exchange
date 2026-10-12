from datetime import date
from decimal import Decimal

from django import forms
from django.db.models import Q

from .models import BASE_CURRENCY_CODE, CashTransaction, Currency, PaymentMethod, cash_quantum
from .roles import ALL_ROLES, DEFAULT_ROLE, ROLE_LABELS
from .services import pricing

_ROLE_CHOICES = [(role, ROLE_LABELS[role]) for role in ALL_ROLES]


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


def _parse_expiry(text: str):
    """``"07/29"`` o ``"07/2029"`` -> ``(2029, 7)``; ``None`` si no se entiende."""
    parts = text.replace(" ", "").replace("-", "/").split("/")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    month, year = int(parts[0]), int(parts[1])
    if len(parts[1]) == 2:
        year += 2000
    elif len(parts[1]) != 4:
        return None
    if not 1 <= month <= 12:
        return None
    return year, month


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


def _amount_widget():
    # step="any": el navegador no debe rechazar un monto que el script completó
    # con otra precisión; la que vale la valida el servidor.
    return forms.NumberInput(attrs={"step": "any", "min": "0", "inputmode": "decimal", "autocomplete": "off"})


class LinkedAmountsMixin:
    """Dos montos vinculados: lo que entrega el cliente y lo que recibe.

    En pantalla el cliente escribe uno y ``static/js/linked-amounts.js`` completa
    el otro. El campo oculto ``anchor`` dice cuál escribió (``in`` u ``out``) y
    es el que el servidor respeta; el otro se recalcula con
    ``services.pricing.convert``. Sin JavaScript no hay ancla: se usa el monto
    que se haya escrito (si se escribieron los dos, el que recibe el cliente).

    Las subclases definen qué campos son el monto y la divisa de cada lado.
    """

    amount_in_field = "amount_in"
    amount_out_field = "amount_out"
    currency_in_field = "currency_in"
    currency_out_field = "currency_out"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Marcas que busca linked-amounts.js para saber qué campo es cuál.
        for side, amount, currency in (
            (pricing.IN, self.amount_in_field, self.currency_in_field),
            (pricing.OUT, self.amount_out_field, self.currency_out_field),
        ):
            self.fields[amount].widget.attrs["data-amount"] = side
            self.fields[currency].widget.attrs["data-currency"] = side
            self.fields[currency].widget.attrs["aria-label"] = self.fields[currency].label
        self.fields["anchor"].widget.attrs["data-anchor"] = True

    def linked_fields(self):
        """Los campos en el orden en que los dibuja ``_linked_amounts.html``."""
        return {
            "amount_in": self[self.amount_in_field],
            "currency_in": self[self.currency_in_field],
            "amount_out": self[self.amount_out_field],
            "currency_out": self[self.currency_out_field],
            "anchor": self["anchor"],
        }

    def clean_linked_amounts(self, cleaned):
        amount_in = cleaned.get(self.amount_in_field)
        amount_out = cleaned.get(self.amount_out_field)
        anchor = cleaned.get("anchor")
        if anchor not in pricing.ANCHORS:
            anchor = pricing.OUT if amount_out else pricing.IN
        amount = amount_in if anchor == pricing.IN else amount_out
        if not amount:
            raise forms.ValidationError("Escribí cuánto se entrega o cuánto se recibe.")
        currency_in = cleaned.get(self.currency_in_field)
        if currency_in is not None and currency_in == cleaned.get(self.currency_out_field):
            raise forms.ValidationError("Elegí dos divisas distintas.")
        cleaned["anchor"] = anchor
        cleaned["amount"] = amount
        return cleaned


class CurrencyBuyForm(LinkedAmountsMixin, forms.Form):
    """Primer paso de una compra: qué divisa, con cuál se paga y cuánto.

    El usuario escribe cuánto paga o cuánto recibe y el otro monto se completa
    solo. El medio de pago se confirma después, en la pantalla de pago, donde la
    cotización queda congelada durante el plazo para confirmar.
    """

    amount_in_field = "pay_amount"
    amount_out_field = "receive_amount"
    currency_in_field = "pay_currency"
    currency_out_field = "currency"

    pay_amount = forms.DecimalField(
        label="Monto que pagás",
        max_digits=18,
        decimal_places=6,
        min_value=Decimal("0.000001"),
        required=False,
        widget=_amount_widget(),
    )
    pay_currency = forms.ModelChoiceField(label="Pagás con", queryset=Currency.objects.none(), empty_label=None)
    receive_amount = forms.DecimalField(
        label="Monto que recibís",
        max_digits=18,
        decimal_places=6,
        min_value=Decimal("0.000001"),
        required=False,
        widget=_amount_widget(),
    )
    currency = forms.ModelChoiceField(
        label="Divisa a comprar",
        queryset=Currency.objects.filter(is_active=True).exclude(code=BASE_CURRENCY_CODE),
    )
    anchor = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, wallet=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.wallet = wallet
        # Se puede pagar con guaraníes o con cualquier divisa activa que haya en
        # la billetera (la pantalla de pago la convierte a guaraníes).
        held_ids = (
            list(wallet.holdings.filter(amount__gt=0).values_list("currency_id", flat=True)) if wallet else []
        )
        self.fields["pay_currency"].queryset = Currency.objects.filter(is_active=True).filter(
            Q(code=BASE_CURRENCY_CODE) | Q(id__in=held_ids)
        )
        if not self.is_bound:
            base = Currency.objects.filter(code=BASE_CURRENCY_CODE).first()
            self.initial.setdefault("pay_currency", base.pk if base else None)

    def clean(self):
        return self.clean_linked_amounts(super().clean())


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


# --- Caja física ------------------------------------------------------------


def _cash_currencies():
    """Divisas que se manejan en caja: el guaraní primero y después las activas."""
    currencies = list(Currency.objects.filter(is_active=True))
    return sorted(currencies, key=lambda c: (c.code != BASE_CURRENCY_CODE, c.code))


class CashSessionOpenForm(forms.Form):
    """Apertura de caja: un campo de efectivo inicial por cada divisa activa."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.currencies = _cash_currencies()
        for currency in self.currencies:
            quantum = cash_quantum(currency)
            self.fields[self.field_name(currency)] = forms.DecimalField(
                label=f"{currency.code} · {currency.name}",
                max_digits=18,
                decimal_places=0 if currency.code == BASE_CURRENCY_CODE else 2,
                min_value=Decimal("0"),
                initial=Decimal("0"),
                required=False,
                widget=forms.NumberInput(attrs={"step": str(quantum), "min": "0", "inputmode": "decimal"}),
            )

    @staticmethod
    def field_name(currency) -> str:
        return f"amount_{currency.code}"

    def currency_fields(self):
        """Pares (divisa, campo) para dibujar el formulario como una tabla."""
        return [(currency, self[self.field_name(currency)]) for currency in self.currencies]

    def clean(self):
        cleaned = super().clean()
        if not any((cleaned.get(self.field_name(c)) or 0) > 0 for c in self.currencies):
            raise forms.ValidationError("Declará el efectivo inicial de al menos una divisa.")
        return cleaned

    def opening_amounts(self) -> dict:
        return {c: self.cleaned_data.get(self.field_name(c)) or Decimal("0") for c in self.currencies}


class CashQuoteForm(LinkedAmountsMixin, forms.Form):
    """Primer paso de una operación de ventanilla: qué entrega y qué recibe el cliente.

    El cajero escribe uno de los dos montos y el otro se completa solo.
    """

    amount_in = forms.DecimalField(
        label="Monto que entrega el cliente",
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
        widget=_amount_widget(),
    )
    currency_in = forms.ModelChoiceField(label="Divisa que entrega", queryset=Currency.objects.none(), empty_label=None)
    amount_out = forms.DecimalField(
        label="Monto que recibe el cliente",
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
        widget=_amount_widget(),
    )
    currency_out = forms.ModelChoiceField(label="Divisa que recibe", queryset=Currency.objects.none(), empty_label=None)
    anchor = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Currency.objects.filter(is_active=True)
        self.fields["currency_in"].queryset = queryset
        self.fields["currency_out"].queryset = queryset
        if not self.is_bound:
            # Lo más común en ventanilla: el cliente paga en guaraníes y se lleva divisa.
            base = queryset.filter(code=BASE_CURRENCY_CODE).first()
            foreign = queryset.exclude(code=BASE_CURRENCY_CODE).first()
            self.initial.setdefault("currency_in", base.pk if base else None)
            self.initial.setdefault("currency_out", foreign.pk if foreign else None)

    def clean(self):
        return self.clean_linked_amounts(super().clean())


class CashPaymentForm(forms.Form):
    """Segundo paso: cómo paga el cliente y quién es."""

    payment_method = forms.ChoiceField(
        label="El cliente paga con",
        choices=CashTransaction.PAYMENT_METHOD_CHOICES,
        widget=forms.RadioSelect,
        initial=CashTransaction.PAY_CASH,
    )
    customer_name = forms.CharField(label="Nombre y apellido del cliente", max_length=120)
    customer_document = forms.CharField(
        label="Cédula de identidad",
        max_length=15,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "placeholder": "1.234.567", "autocomplete": "off"}),
    )
    # Datos de la tarjeta: obligatorios sólo si se paga con tarjeta. Del número
    # y del código de seguridad no se guarda nada (sólo marca y últimos cuatro
    # dígitos); el código no se vuelve a mostrar si el formulario tiene errores.
    card_number = forms.CharField(
        label="Número de tarjeta",
        max_length=25,
        required=False,
        widget=forms.TextInput(
            attrs={"inputmode": "numeric", "placeholder": "4111 1111 1111 1111", "autocomplete": "off"}
        ),
    )
    card_holder = forms.CharField(
        label="Nombre del titular (como figura en la tarjeta)",
        max_length=80,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    card_expiry = forms.CharField(
        label="Vencimiento (MM/AA)",
        max_length=7,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "MM/AA", "inputmode": "numeric", "autocomplete": "off"}),
    )
    card_cvv = forms.CharField(
        label="Código de seguridad",
        max_length=4,
        required=False,
        widget=forms.PasswordInput(attrs={"inputmode": "numeric", "placeholder": "•••", "autocomplete": "off"}),
        help_text="Los 3 dígitos del dorso (4 en el frente si es American Express).",
    )

    def clean_customer_name(self):
        name = " ".join(self.cleaned_data["customer_name"].split())
        if len(name) < 3:
            raise forms.ValidationError("Escribí el nombre completo del cliente.")
        return name

    def clean_customer_document(self):
        raw = self.cleaned_data["customer_document"]
        digits = "".join(char for char in raw if char.isdigit())
        if any(not (char.isdigit() or char in ". -") for char in raw):
            raise forms.ValidationError("La cédula sólo puede tener números (los puntos son opcionales).")
        if not 5 <= len(digits) <= 9:
            raise forms.ValidationError("La cédula debe tener entre 5 y 9 dígitos.")
        return digits

    def clean(self):
        cleaned = super().clean()
        card = {"card_brand": "", "card_holder": "", "card_last4": ""}
        if cleaned.get("payment_method") == CashTransaction.PAY_CARD:
            card = self._clean_card(cleaned) or card
        # El número completo y el código de seguridad no salen del formulario.
        cleaned.pop("card_number", None)
        cleaned.pop("card_cvv", None)
        cleaned.pop("card_expiry", None)
        cleaned.update(card)
        return cleaned

    def _clean_card(self, cleaned):
        """Valida los datos de la tarjeta; devuelve lo que se puede guardar o ``None``."""
        errors = {}
        digits = "".join(char for char in cleaned.get("card_number", "") if char.isdigit())
        if not 13 <= len(digits) <= 19:
            errors["card_number"] = "El número de tarjeta debe tener entre 13 y 19 dígitos."
        elif not _luhn_is_valid(digits):
            errors["card_number"] = "El número de tarjeta no es válido (no pasa el dígito verificador)."

        holder = " ".join((cleaned.get("card_holder") or "").split())
        if len(holder) < 3:
            errors["card_holder"] = "Escribí el nombre del titular como figura en la tarjeta."

        expiry = _parse_expiry(cleaned.get("card_expiry") or "")
        if expiry is None:
            errors["card_expiry"] = "Escribí el vencimiento como MM/AA."
        elif expiry < (date.today().year, date.today().month):
            errors["card_expiry"] = "La tarjeta está vencida."

        brand = _detect_brand(digits) if digits else PaymentMethod.OTHER
        cvv = cleaned.get("card_cvv") or ""
        cvv_length = 4 if brand == PaymentMethod.AMEX else 3
        if len(cvv) != cvv_length or not cvv.isdigit():
            errors["card_cvv"] = f"El código de seguridad tiene {cvv_length} dígitos."

        for field, message in errors.items():
            self.add_error(field, message)
        if errors:
            return None
        return {"card_brand": brand, "card_holder": holder, "card_last4": digits[-4:]}
