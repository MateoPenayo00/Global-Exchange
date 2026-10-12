from django.contrib import admin

from .models import (
    CashSession,
    CashSessionBalance,
    CashTransaction,
    Currency,
    CurrencyRate,
    PaymentMethod,
    PurchaseOrder,
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


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "wallet", "currency", "pyg_amount", "currency_amount", "rate", "status", "payment_kind", "created_at")
    list_filter = ("status", "payment_kind", "currency")
    search_fields = ("wallet__user__username", "qr_reference")
    readonly_fields = ("frozen_rates", "created_at", "expires_at", "closed_at")


class CashSessionBalanceInline(admin.TabularInline):
    model = CashSessionBalance
    extra = 0
    readonly_fields = ("currency", "opening_amount", "current_amount", "closing_amount")
    can_delete = False


@admin.register(CashSession)
class CashSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "cashier_name", "status", "opened_at", "closed_at")
    list_filter = ("status",)
    search_fields = ("cashier_name", "cashier__username")
    inlines = [CashSessionBalanceInline]


@admin.register(CashTransaction)
class CashTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "id", "session", "status", "customer_name", "customer_document",
        "amount_in", "currency_in", "amount_out", "currency_out", "payment_method", "created_at",
    )
    list_filter = ("status", "payment_method", "currency_in", "currency_out")
    search_fields = ("customer_name", "customer_document", "qr_reference")
