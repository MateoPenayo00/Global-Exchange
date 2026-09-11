# Core app

This app contains the public landing page, dashboard, account screens, and the
role-based sections of the currency exchange project.

## Roles

Identity and roles live in Keycloak. The realm defines three realm roles:

| Role      | Capabilities |
|-----------|--------------|
| `admin`   | Create, manage and delete users (admin *Usuarios* tab) plus every `manager` capability. |
| `manager` | Manage the value of certain currencies (`/currencies/`, filled in a later sprint). |
| `user`    | Buy and sell currencies (`/trade/`, filled in a later sprint). |

`core/roles.py` is the single source of truth for role names and checks
(`has_role`, `is_admin`, `is_manager`, `is_trader`). On every login
`core/backends.py` copies the Keycloak realm roles into matching Django groups,
and a self-registered account (regular *Registrarse* flow) is granted the
default `user` role automatically on first login.

## Creating users

* **Regular flow:** *Registrarse* → Keycloak registration form → first login
  grants the `user` role.
* **Admin flow:** an `admin` opens *Usuarios* → *Nuevo usuario*, which creates
  the account and assigns roles directly through the Keycloak Admin API
  (`core/services/keycloak_admin.py`). The same tab edits roles and deletes
  accounts.

The Admin API service account (`exchange-admin-api`) needs the
`realm-management: realm-admin` role, set in `keycloak/realm-export.json`.
