from decimal import Decimal

from django.conf import settings
from django.db import models

BASE_CURRENCY_CODE = "USD"


class Currency(models.Model):
    """A currency that can be traded against the universal currency (USD)."""

    code = models.CharField(max_length=8, unique=True, help_text="Código ISO-like, p. ej. EUR, ARS, USD.")
    name = models.CharField(max_length=80)
    symbol = models.CharField(max_length=8, blank=True)
    value_in_usd = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        help_text="Cuántos dólares estadounidenses (USD) vale 1 unidad de esta divisa.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "currencies"

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        if self.code == BASE_CURRENCY_CODE:
            # USD is the universal currency: its value is fixed by definition.
            self.value_in_usd = Decimal("1.000000")
        super().save(*args, **kwargs)

    @property
    def is_base(self) -> bool:
        return self.code == BASE_CURRENCY_CODE


class Wallet(models.Model):
    """A user's cash (USD) balance plus whatever currencies they hold."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet")
    usd_balance = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Billetera de {self.user}"

    def holding_amount(self, currency: Currency) -> Decimal:
        holding = self.holdings.filter(currency=currency).first()
        return holding.amount if holding else Decimal("0")


class WalletHolding(models.Model):
    """How much of a given currency a wallet currently holds."""

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="holdings")
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="holdings")
    amount = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0"))

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["wallet", "currency"], name="unique_wallet_currency")
        ]
        ordering = ["currency__code"]

    def __str__(self):
        return f"{self.wallet.user}: {self.amount} {self.currency.code}"


class WalletTransaction(models.Model):
    DEPOSIT = "deposit"
    BUY = "buy"
    WITHDRAW = "withdraw"

    TYPE_CHOICES = [
        (DEPOSIT, "Carga de saldo"),
        (BUY, "Compra de divisa"),
        (WITHDRAW, "Retiro de divisa"),
    ]

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="transactions")
    kind = models.CharField(max_length=10, choices=TYPE_CHOICES)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    amount = models.DecimalField(max_digits=18, decimal_places=6)
    usd_amount = models.DecimalField(max_digits=18, decimal_places=2)
    rate_used = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    note = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.wallet.user} · {self.amount}"
