from . import roles as role_lib
from .roles import ROLE_LABELS


def roles(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    return {
        "user_roles": sorted(role_lib.user_roles(user)),
        "role_labels": ROLE_LABELS,
        "can_manage_users": role_lib.is_admin(user),
        "can_manage_currencies": role_lib.is_manager(user),
        "can_trade": role_lib.is_trader(user),
    }
