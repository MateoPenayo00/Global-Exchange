from .models import BASE_CURRENCY_CODE, BASE_CURRENCY_SYMBOL
from . import roles as role_lib
from .roles import ROLE_LABELS


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
        }
    )
    return base
