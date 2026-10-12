from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

# El guaraní paraguayo es la divisa base del sistema: todas las cotizaciones se
# expresan en guaraníes por unidad de divisa extranjera.
BASE_CURRENCY_CODE = "PYG"
BASE_CURRENCY_NAME = "Guaraní paraguayo"
BASE_CURRENCY_SYMBOL = "Gs."

# Redondeos usados en todo el dominio.
PYG_QUANTUM = Decimal("1")  # el guaraní no se fracciona en la práctica.
CURRENCY_QUANTUM = Decimal("0.000001")
RATE_QUANTUM = Decimal("0.000001")

# Plazo que tiene el usuario para confirmar el pago de una compra. Pasado ese
# tiempo la orden vence y la cotización congelada deja de valer.
PURCHASE_TIMEOUT = timedelta(minutes=5)


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


class PurchaseOrder(models.Model):
    """Una compra de divisa en curso, con las cotizaciones congeladas al iniciarla.

    Comprar ya no es un único paso: el formulario de la pizarra crea una orden
    *pendiente* y lleva al usuario a la pantalla de pago. Al crearla se fijan:

    * ``rate`` — el precio de venta de la divisa comprada, que define cuánta
      divisa recibe el usuario por ``pyg_amount`` guaraníes.
    * ``frozen_rates`` — el precio de compra de cada divisa activa (código ->
      cotización como texto). Se usa si el usuario paga con otra divisa de su
      billetera: la casa de cambio se la compra a ese precio, por detrás, para
      cubrir los guaraníes de la compra.

    Si las cotizaciones cambian mientras la orden está abierta (por ejemplo, un
    administrador adelanta la simulación), la orden conserva los valores del
    inicio. La orden se cierra al confirmar el pago, al cancelarla o al vencer
    el plazo de ``PURCHASE_TIMEOUT``.
    """

    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    STATUS_CHOICES = [
        (PENDING, "Pendiente de pago"),
        (COMPLETED, "Pagada"),
        (CANCELLED, "Cancelada"),
        (EXPIRED, "Vencida"),
    ]

    PAY_BALANCE = "balance"
    PAY_CURRENCY = "currency"
    PAY_CARD = "card"
    PAY_QR = "qr"

    PAYMENT_KIND_CHOICES = [
        (PAY_BALANCE, "Saldo en guaraníes"),
        (PAY_CURRENCY, "Otra divisa de la billetera"),
        (PAY_CARD, "Tarjeta de crédito"),
        (PAY_QR, "Código QR"),
    ]

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="purchase_orders")
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="purchase_orders")
    pyg_amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency_amount = models.DecimalField(max_digits=18, decimal_places=6)
    rate = models.DecimalField(max_digits=18, decimal_places=6, help_text="Precio de venta congelado.")
    frozen_rates = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    payment_kind = models.CharField(max_length=10, choices=PAYMENT_KIND_CHOICES, blank=True)
    payment_method = models.ForeignKey(
        PaymentMethod, on_delete=models.SET_NULL, null=True, blank=True, related_name="purchase_orders"
    )
    paid_currency = models.ForeignKey(
        Currency, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    paid_amount = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    qr_reference = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "orden de compra"
        verbose_name_plural = "órdenes de compra"

    def __str__(self):
        return f"Compra #{self.pk} · {self.currency.code} · {self.get_status_display()}"

    def save(self, *args, **kwargs):
        if self.expires_at is None:
            self.expires_at = timezone.now() + PURCHASE_TIMEOUT
        super().save(*args, **kwargs)

    @property
    def is_pending(self) -> bool:
        return self.status == self.PENDING

    def is_past_deadline(self, now=None) -> bool:
        return (now or timezone.now()) >= self.expires_at

    def seconds_left(self, now=None) -> int:
        if not self.is_pending:
            return 0
        remaining = (self.expires_at - (now or timezone.now())).total_seconds()
        return max(0, int(remaining))

    def frozen_buy_rate(self, currency: Currency):
        """Precio de compra congelado de ``currency``, o ``None`` si no se congeló."""
        value = self.frozen_rates.get(currency.code)
        return Decimal(value) if value is not None else None


# --- Caja física ----------------------------------------------------------

# En ventanilla se entrega y se recibe efectivo: el guaraní va en unidades enteras
# y las divisas extranjeras en centavos, no en millonésimas como en la billetera.
CASH_FOREIGN_QUANTUM = Decimal("0.01")

# La cotización de una operación de caja se congela igual que la de una compra
# en línea, y con el mismo plazo para confirmarla.
CASH_QUOTE_TIMEOUT = PURCHASE_TIMEOUT


def cash_quantum(currency: "Currency") -> Decimal:
    """Unidad mínima de efectivo de ``currency``: 1 Gs. o un centavo."""
    return PYG_QUANTUM if currency.code == BASE_CURRENCY_CODE else CASH_FOREIGN_QUANTUM


class CashSession(models.Model):
    """Un turno de un cajero en la caja física, de la apertura al cierre.

    Al abrirla el cajero declara cuánto efectivo tiene de cada divisa
    (``CashSessionBalance.opening_amount``). Cada operación confirmada mueve
    ``current_amount`` y, al cerrarla, ese saldo queda guardado como
    ``closing_amount`` para el reporte. Un cajero tiene a lo sumo una sesión
    abierta a la vez.
    """

    OPEN = "open"
    CLOSED = "closed"

    STATUS_CHOICES = [(OPEN, "Abierta"), (CLOSED, "Cerrada")]

    # SET_NULL + nombre copiado: el reporte de una sesión es un registro contable
    # y no puede desaparecer si se borra el usuario.
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="cash_sessions"
    )
    cashier_name = models.CharField(max_length=150)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default=OPEN)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-opened_at"]
        verbose_name = "sesión de caja"
        verbose_name_plural = "sesiones de caja"
        constraints = [
            models.UniqueConstraint(
                fields=["cashier"],
                condition=models.Q(status="open"),
                name="one_open_cash_session_per_cashier",
            )
        ]

    def __str__(self):
        return f"Caja #{self.pk} · {self.cashier_name} · {self.get_status_display()}"

    @property
    def is_open(self) -> bool:
        return self.status == self.OPEN


class CashSessionBalance(models.Model):
    """Efectivo de una divisa en una sesión: al abrir, ahora y al cerrar."""

    session = models.ForeignKey(CashSession, on_delete=models.CASCADE, related_name="balances")
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    opening_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    current_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    closing_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["currency__code"]
        constraints = [
            models.UniqueConstraint(fields=["session", "currency"], name="unique_cash_balance_per_currency")
        ]
        verbose_name = "saldo de caja"
        verbose_name_plural = "saldos de caja"

    def __str__(self):
        return f"{self.session}: {self.current_amount} {self.currency.code}"


class CashTransaction(models.Model):
    """Una operación de ventanilla: el cliente entrega una divisa y recibe otra.

    Se valúa siempre pasando por guaraníes, con las mismas reglas que el resto
    del sistema:

    * lo que **entrega** el cliente se toma al precio de **compra** de esa divisa
      (``rate_in``; 1 si son guaraníes);
    * lo que **recibe** se le cobra al precio de **venta** (``rate_out``; 1 si
      son guaraníes).

    Así una sola regla cubre las tres operaciones: el cliente compra divisa con
    guaraníes, vende divisa por guaraníes o cambia una divisa por otra.

    Lo que recibe el cliente se entrega siempre en efectivo. Lo que entrega puede
    pagarlo en efectivo (entra a la caja), con tarjeta o con QR (no entra a la
    caja). Las cotizaciones se congelan al cotizar, y la operación vence si no se
    confirma dentro de ``CASH_QUOTE_TIMEOUT``.
    """

    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    STATUS_CHOICES = [
        (PENDING, "Pendiente"),
        (COMPLETED, "Realizada"),
        (CANCELLED, "Cancelada"),
        (EXPIRED, "Vencida"),
    ]

    PAY_CASH = "cash"
    PAY_CARD = "card"
    PAY_QR = "qr"

    PAYMENT_METHOD_CHOICES = [
        (PAY_CASH, "Efectivo"),
        (PAY_CARD, "Tarjeta de crédito"),
        (PAY_QR, "Código QR"),
    ]

    session = models.ForeignKey(CashSession, on_delete=models.CASCADE, related_name="transactions")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    currency_in = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    amount_in = models.DecimalField(max_digits=18, decimal_places=2, help_text="Lo que entrega el cliente.")
    rate_in = models.DecimalField(max_digits=18, decimal_places=6, help_text="Precio de compra congelado.")
    currency_out = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    amount_out = models.DecimalField(max_digits=18, decimal_places=2, help_text="Lo que recibe el cliente.")
    rate_out = models.DecimalField(max_digits=18, decimal_places=6, help_text="Precio de venta congelado.")
    pyg_value = models.DecimalField(max_digits=18, decimal_places=2)
    payment_method = models.CharField(max_length=8, choices=PAYMENT_METHOD_CHOICES, blank=True)
    # De la tarjeta se guarda sólo lo necesario para el comprobante: nunca el
    # número completo ni el código de seguridad.
    card_brand = models.CharField(max_length=12, choices=PaymentMethod.BRAND_CHOICES, blank=True)
    card_holder = models.CharField(max_length=80, blank=True)
    card_last4 = models.CharField(max_length=4, blank=True)
    qr_reference = models.CharField(max_length=40, blank=True)
    customer_name = models.CharField(max_length=120, blank=True)
    customer_document = models.CharField(max_length=12, blank=True, verbose_name="Cédula de identidad")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "operación de caja"
        verbose_name_plural = "operaciones de caja"

    def __str__(self):
        return f"Operación #{self.pk} · {self.description} · {self.get_status_display()}"

    def save(self, *args, **kwargs):
        if self.expires_at is None:
            self.expires_at = timezone.now() + CASH_QUOTE_TIMEOUT
        super().save(*args, **kwargs)

    @property
    def is_pending(self) -> bool:
        return self.status == self.PENDING

    @property
    def description(self) -> str:
        if self.currency_in.code == BASE_CURRENCY_CODE:
            return f"El cliente compra {self.currency_out.code}"
        if self.currency_out.code == BASE_CURRENCY_CODE:
            return f"El cliente vende {self.currency_in.code}"
        return f"Cambio {self.currency_in.code} → {self.currency_out.code}"

    @property
    def cash_in(self) -> Decimal:
        """Efectivo que entra a la caja con esta operación."""
        return self.amount_in if self.payment_method == self.PAY_CASH else Decimal("0")

    @property
    def cash_out(self) -> Decimal:
        """Efectivo que sale de la caja: lo que recibe el cliente."""
        return self.amount_out

    def is_past_deadline(self, now=None) -> bool:
        return (now or timezone.now()) >= self.expires_at

    def seconds_left(self, now=None) -> int:
        if not self.is_pending:
            return 0
        return max(0, int((self.expires_at - (now or timezone.now())).total_seconds()))
