from django.contrib import admin

from .models import Currency, Wallet, WalletHolding, WalletTransaction


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "value_in_usd", "is_active", "updated_at")
    search_fields = ("code", "name")


class WalletHoldingInline(admin.TabularInline):
    model = WalletHolding
    extra = 0


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "usd_balance", "updated_at")
    search_fields = ("user__username", "user__email")
    inlines = [WalletHoldingInline]


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ("wallet", "kind", "currency", "amount", "usd_amount", "created_at")
    list_filter = ("kind", "currency")
    search_fields = ("wallet__user__username",)
