"""Role model for Exchange Pro.

Identity and roles are owned by Keycloak. The realm defines four realm roles:

* ``admin``   - can create, manage and delete users (plus everything a manager can do).
* ``manager`` - will manage the value of certain currencies (future capability).
* ``user``    - can buy and sell currencies.
* ``cajero``  - operates the physical cash desk ("Caja") on behalf of walk-in customers.

On every login the OIDC backend copies the Keycloak realm roles into matching
Django groups so the rest of the app can rely on ``request.user`` alone.
"""

from __future__ import annotations

ADMIN = "admin"
MANAGER = "manager"
USER = "user"
CASHIER = "cajero"

# All roles the application knows about, broadest first.
ALL_ROLES = (ADMIN, MANAGER, USER, CASHIER)

# The role every self-registered account receives by default.
DEFAULT_ROLE = USER

ROLE_LABELS = {
    ADMIN: "Administrador",
    MANAGER: "Gestor",
    USER: "Usuario",
    CASHIER: "Cajero",
}

# Descriptions used when the app has to create a missing realm role in Keycloak
# (the realm export is only imported the first time Keycloak starts).
ROLE_DESCRIPTIONS = {
    ADMIN: "Crea, gestiona y elimina usuarios; incluye las capacidades de gestor.",
    MANAGER: "Gestiona el valor de ciertas divisas.",
    USER: "Compra y vende divisas.",
    CASHIER: "Opera la caja física: abre sesiones y atiende clientes en ventanilla.",
}


def roles_from_claims(claims: dict) -> set[str]:
    """Extract the Exchange Pro realm roles from an OIDC userinfo/ID-token payload."""
    realm_access = claims.get("realm_access") or {}
    raw = set(realm_access.get("roles") or [])
    # Some Keycloak mappers flatten the claim instead of nesting it.
    raw |= set(claims.get("roles") or [])
    return {role for role in raw if role in ALL_ROLES}


def user_roles(user) -> set[str]:
    if not getattr(user, "is_authenticated", False):
        return set()
    if user.is_superuser:
        return set(ALL_ROLES)
    return {name for name in user.groups.values_list("name", flat=True) if name in ALL_ROLES}


def has_role(user, *roles: str) -> bool:
    return bool(user_roles(user).intersection(roles))


def is_admin(user) -> bool:
    return has_role(user, ADMIN)


def is_manager(user) -> bool:
    # Admins inherit every manager capability.
    return has_role(user, ADMIN, MANAGER)


def is_trader(user) -> bool:
    return has_role(user, USER)


def is_cashier(user) -> bool:
    # Admins can also open a cash session and operate the desk.
    return has_role(user, ADMIN, CASHIER)
