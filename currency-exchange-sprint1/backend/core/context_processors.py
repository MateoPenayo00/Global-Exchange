from .models import BASE_CURRENCY_CODE, BASE_CURRENCY_SYMBOL, Notification
from . import roles as role_lib
from .roles import ROLE_LABELS


def unread_notifications(request):
    """Cantidad de avisos sin leer del usuario, para mostrarla en el menú lateral."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"unread_notifications": 0}
    return {"unread_notifications": Notification.objects.filter(user=user, read_at__isnull=True).count()}


def roles(request):
    user = getattr(request, "user", None)
    base = {
        "base_currency_code": BASE_CURRENCY_CODE,
        "base_currency_symbol": BASE_CURRENCY_SYMBOL,
    }
    if user is None or not user.is_authenticated:
        return base
    base.update(
        {
            "user_roles": sorted(role_lib.user_roles(user)),
            "role_labels": ROLE_LABELS,
            "can_manage_users": role_lib.is_admin(user),
            "can_manage_currencies": role_lib.is_manager(user),
            "can_simulate": role_lib.is_admin(user),
            "can_trade": role_lib.is_trader(user),
            "can_operate_cash": role_lib.is_cashier(user),
            "can_view_invoices": role_lib.has_role(user, role_lib.USER, role_lib.CASHIER, role_lib.ADMIN),
        }
    )
    return base
