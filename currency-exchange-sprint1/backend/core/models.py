from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models

# El guaraní paraguayo es la divisa base del sistema: todas las cotizaciones se
# expresan en guaraníes por unidad de divisa extranjera.
BASE_CURRENCY_CODE = "PYG"
BASE_CURRENCY_NAME = "Guaraní paraguayo"
BASE_CURRENCY_SYMBOL = "Gs."

# Redondeos usados en todo el dominio.
PYG_QUANTUM = Decimal("1")  # el guaraní no se fracciona en la práctica.
CURRENCY_QUANTUM = Decimal("0.000001")
RATE_QUANTUM = Decimal("0.000001")


def quantize_pyg(amount: Decimal) -> Decimal:
    """Redondea un monto en guaraníes a unidades enteras."""
    return Decimal(amount).quantize(PYG_QUANTUM, rounding=ROUND_HALF_UP)


class Currency(models.Model):
    """Una divisa cotizada contra el guaraní (PYG), con precio de compra y de venta.

    Convención de la casa de cambio (igual a la de una pizarra real):

    * ``buy_rate``  — guaraníes que la casa de cambio **paga** al cliente por
      1 unidad de la divisa. Es el precio más bajo de los dos.
    * ``sell_rate`` — guaraníes que la casa de cambio **cobra** al cliente por
      1 unidad de la divisa. Es el precio más alto de los dos.

    La diferencia entre ambos (el *spread*) es el margen de la casa de cambio.
    """

    code = models.CharField(max_length=8, unique=True, help_text="Código ISO-like, p. ej. USD, EUR, PYG.")
    name = models.CharField(max_length=80)
    symbol = models.CharField(max_length=8, blank=True)
    buy_rate = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        help_text="Precio de compra: guaraníes que la casa de cambio paga por 1 unidad de esta divisa.",
    )
    sell_rate = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        help_text="Precio de venta: guaraníes que la casa de cambio cobra por 1 unidad de esta divisa.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "divisa"
        verbose_name_plural = "divisas"

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        if self.code == BASE_CURRENCY_CODE:
            # El guaraní es la divisa base: por definición vale 1 guaraní.
            self.buy_rate = Decimal("1.000000")
            self.sell_rate = Decimal("1.000000")
        super().save(*args, **kwargs)

    @property
    def is_base(self) -> bool:
        return self.code == BASE_CURRENCY_CODE

    @property
    def mid_rate(self) -> Decimal:
        """Cotización media entre compra y venta (sirve de referencia y para gráficos)."""
        return ((self.buy_rate + self.sell_rate) / Decimal("2")).quantize(RATE_QUANTUM)

    @property
    def spread(self) -> Decimal:
        """Margen absoluto en guaraníes entre el precio de venta y el de compra."""
        return self.sell_rate - self.buy_rate

    @property
    def spread_percent(self) -> Decimal:
        """Margen porcentual respecto de la cotización media."""
        mid = self.mid_rate
        if mid == 0:
            return Decimal("0.00")
        return ((self.spread / mid) * Decimal("100")).quantize(Decimal("0.01"))

    def record_rate(self, on_date=None, source="manual"):
        """Guarda (o actualiza) el punto de historial de esta divisa para una fecha."""
        if self.is_base:
            return None
        rate, _ = CurrencyRate.objects.update_or_create(
            currency=self,
            date=on_date or date.today(),
            defaults={"buy_rate": self.buy_rate, "sell_rate": self.sell_rate, "source": source},
        )
        return rate


class CurrencyRate(models.Model):
    """Un punto del historial de cotizaciones de una divisa (un registro por día)."""

    MANUAL = "manual"
    SIMULATION = "simulacion"
    INITIAL = "inicial"

    SOURCE_CHOICES = [
        (MANUAL, "Carga manual"),
        (SIMULATION, "Simulación de día"),
        (INITIAL, "Valor inicial"),
    ]

    currency = models.ForeignKey(Currency, on_delete=models.CASCADE, related_name="rates")
    date = models.DateField()
    buy_rate = models.DecimalField(max_digits=18, decimal_places=6)
    sell_rate = models.DecimalField(max_digits=18, decimal_places=6)
    source = models.CharField(max_length=12, choices=SOURCE_CHOICES, default=MANUAL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["currency__code", "date"]
        constraints = [
            models.UniqueConstraint(fields=["currency", "date"], name="unique_currency_rate_per_day")
        ]
        verbose_name = "cotización histórica"
        verbose_name_plural = "cotizaciones históricas"

    def __str__(self):
        return f"{self.currency.code} {self.date}: {self.buy_rate} / {self.sell_rate}"

    @property
    def mid_rate(self) -> Decimal:
        return ((self.buy_rate + self.sell_rate) / Decimal("2")).quantize(RATE_QUANTUM)


class SimulationState(models.Model):
    """Reloj simulado del sistema, que un administrador adelanta día por día.

    Existe una sola fila (patrón *singleton*): ``SimulationState.load()`` la crea
    la primera vez que se la necesita.
    """

    current_date = models.DateField(default=date.today)
    days_advanced = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "estado de simulación"
        verbose_name_plural = "estado de simulación"

    def __str__(self):
        return f"Día simulado: {self.current_date} (+{self.days_advanced})"

    @classmethod
    def load(cls) -> "SimulationState":
        state = cls.objects.first()
        if state is None:
            state = cls.objects.create()
        return state


class Wallet(models.Model):
    """La billetera de un usuario: saldo en guaraníes más las divisas que posee."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet")
    pyg_balance = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "billetera"
        verbose_name_plural = "billeteras"

    def __str__(self):
        return f"Billetera de {self.user}"

    def holding_amount(self, currency: Currency) -> Decimal:
        holding = self.holdings.filter(currency=currency).first()
        return holding.amount if holding else Decimal("0")

    @property
    def holdings_value_pyg(self) -> Decimal:
        """Valor de todas las tenencias si el usuario las vendiera hoy (al precio de compra)."""
        total = Decimal("0")
        for holding in self.holdings.select_related("currency").filter(amount__gt=0):
            total += holding.amount * holding.currency.buy_rate
        return quantize_pyg(total)

    @property
    def total_value_pyg(self) -> Decimal:
        return quantize_pyg(self.pyg_balance + self.holdings_value_pyg)


class PaymentMethod(models.Model):
    """Una tarjeta de crédito registrada por el usuario en su billetera.

    Por seguridad **no se guarda el número completo de la tarjeta**: el
    formulario valida el número, deduce la marca y conserva únicamente los
    últimos cuatro dígitos para que el usuario pueda reconocerla.
    """

    VISA = "visa"
    MASTERCARD = "mastercard"
    AMEX = "amex"
    OTHER = "otra"

    BRAND_CHOICES = [
        (VISA, "Visa"),
        (MASTERCARD, "Mastercard"),
        (AMEX, "American Express"),
        (OTHER, "Otra"),
    ]

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="payment_methods")
    label = models.CharField(max_length=40, help_text="Un alias para reconocer la tarjeta, p. ej. 'Visa personal'.")
    holder_name = models.CharField(max_length=80, verbose_name="Titular")
    brand = models.CharField(max_length=12, choices=BRAND_CHOICES, default=OTHER)
    last4 = models.CharField(max_length=4)
    expiry_month = models.PositiveSmallIntegerField()
    expiry_year = models.PositiveSmallIntegerField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "medio de pago"
        verbose_name_plural = "medios de pago"

    def __str__(self):
        return f"{self.get_brand_display()} {self.masked_number}"

    @property
    def masked_number(self) -> str:
        return f"•••• •••• •••• {self.last4}"

    @property
    def expiry_display(self) -> str:
        return f"{self.expiry_month:02d}/{self.expiry_year % 100:02d}"

    def is_expired(self, on_date=None) -> bool:
        today = on_date or date.today()
        return (self.expiry_year, self.expiry_month) < (today.year, today.month)


class WalletHolding(models.Model):
    """Cuánto tiene una billetera de una divisa determinada."""

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="holdings")
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="holdings")
    amount = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0"))

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["wallet", "currency"], name="unique_wallet_currency")
        ]
        ordering = ["currency__code"]
        verbose_name = "tenencia"
        verbose_name_plural = "tenencias"

    def __str__(self):
        return f"{self.wallet.user}: {self.amount} {self.currency.code}"

    @property
    def value_pyg(self) -> Decimal:
        """Cuántos guaraníes recibiría el usuario si vendiera esta tenencia hoy."""
        return quantize_pyg(self.amount * self.currency.buy_rate)


class WalletTransaction(models.Model):
    DEPOSIT = "deposit"
    BUY = "buy"
    SELL = "sell"
    WITHDRAW = "withdraw"

    TYPE_CHOICES = [
        (DEPOSIT, "Carga de saldo"),
        (BUY, "Compra de divisa"),
        (SELL, "Venta de divisa"),
        (WITHDRAW, "Retiro de divisa"),
    ]

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="transactions")
    kind = models.CharField(max_length=10, choices=TYPE_CHOICES)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    payment_method = models.ForeignKey(
        PaymentMethod, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions"
    )
    amount = models.DecimalField(max_digits=18, decimal_places=6)
    pyg_amount = models.DecimalField(max_digits=18, decimal_places=2)
    rate_used = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    note = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "movimiento"
        verbose_name_plural = "movimientos"

    def __str__(self):
        return f"{self.get_kind_display()} · {self.wallet.user} · {self.amount}"
