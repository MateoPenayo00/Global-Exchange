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

## Currencies and wallets

`core/models.py` adds the app's own domain data (in the Postgres `exchange_db`,
separate from Keycloak identity):

* **`Currency`** — `code`, `name`, `symbol`, `value_in_usd`. **USD is the
  universal currency**: it is seeded by the `0002_seed_usd` migration, its
  value is hard-locked to `1.000000` (see `Currency.save`), and it cannot be
  deleted. `admin`/`manager` get full CRUD at `/currencies/`, which is how
  they raise or lower every other currency's value against the dollar.
* **`Wallet`** — one per user, holds the USD cash balance.
* **`WalletHolding`** — how much of each currency a wallet holds.
* **`WalletTransaction`** — an audit log of deposits, buys, and withdrawals.

User-facing flows (role `user`, `/wallet/` and `/trade/`):

* **Add balance** — a normal amount form, plus a one-click "Cargar $100 de
  prueba" test button (`wallet_deposit_test`) that credits a fixed demo amount
  without needing a real payment method.
* **Buy** — `/trade/` spends USD balance to acquire a currency into the
  wallet, at that currency's current `value_in_usd` rate.
* **Withdraw** — a normal form (pick currency + amount) plus a per-currency
  one-click "Retirar 1 (prueba)" test button (`wallet_withdraw_test`) that
  removes a small fixed demo amount from that holding.
