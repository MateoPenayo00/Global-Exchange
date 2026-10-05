from django.contrib import admin

from .models import (
    Currency,
    CurrencyRate,
    PaymentMethod,
    SimulationState,
    Wallet,
    WalletHolding,
    WalletTransaction,
)


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "buy_rate", "sell_rate", "spread_percent", "is_active", "updated_at")
    search_fields = ("code", "name")
    list_filter = ("is_active",)


@admin.register(CurrencyRate)
class CurrencyRateAdmin(admin.ModelAdmin):
    list_display = ("currency", "date", "buy_rate", "sell_rate", "source")
    list_filter = ("currency", "source")
    date_hierarchy = "date"


@admin.register(SimulationState)
class SimulationStateAdmin(admin.ModelAdmin):
    list_display = ("current_date", "days_advanced", "updated_at")


class WalletHoldingInline(admin.TabularInline):
    model = WalletHolding
    extra = 0


class PaymentMethodInline(admin.TabularInline):
    model = PaymentMethod
    extra = 0
    readonly_fields = ("last4", "brand", "created_at")


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "pyg_balance", "updated_at")
    search_fields = ("user__username", "user__email")
    inlines = [WalletHoldingInline, PaymentMethodInline]


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ("wallet", "label", "brand", "masked_number", "expiry_display", "is_active")
    list_filter = ("brand", "is_active")
    search_fields = ("wallet__user__username", "label", "holder_name")


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ("wallet", "kind", "currency", "amount", "pyg_amount", "payment_method", "created_at")
    list_filter = ("kind", "currency")
    search_fields = ("wallet__user__username",)
