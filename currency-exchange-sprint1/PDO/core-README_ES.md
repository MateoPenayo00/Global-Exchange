# App core

Esta app contiene la página pública de inicio, el panel, las pantallas de cuenta y las
secciones basadas en roles del proyecto de cambio de divisas.

## Roles

La identidad y los roles viven en Keycloak. El realm define tres roles de realm:

| Rol       | Capacidades |
|-----------|--------------|
| `admin`   | Crea, gestiona y elimina usuarios (pestaña *Usuarios* del administrador) además de todas las capacidades de `manager`. |
| `manager` | Gestiona el valor de ciertas divisas (`/currencies/`). |
| `user`    | Compra y vende divisas (`/trade/` y `/wallet/`). |

`core/roles.py` es la única fuente de verdad para los nombres de roles y sus
comprobaciones (`has_role`, `is_admin`, `is_manager`, `is_trader`). En cada inicio de
sesión, `core/backends.py` copia los roles de realm de Keycloak a los grupos de Django
correspondientes, y una cuenta autorregistrada (flujo normal de *Registrarse*) recibe
automáticamente el rol `user` por defecto en su primer inicio de sesión.

## Creación de usuarios

* **Flujo normal:** *Registrarse* → formulario de registro de Keycloak → el primer
  inicio de sesión otorga el rol `user`.
* **Flujo de administrador:** un `admin` abre *Usuarios* → *Nuevo usuario*, lo que crea
  la cuenta y asigna roles directamente a través del Admin API de Keycloak
  (`core/services/keycloak_admin.py`). La misma pestaña permite editar roles y eliminar
  cuentas.

La cuenta de servicio del Admin API (`exchange-admin-api`) necesita el rol
`realm-management: realm-admin`, configurado en `keycloak/realm-export.json`.

## Divisas y billeteras

`core/models.py` añade los datos propios del dominio de la aplicación (en la base de
datos Postgres `exchange_db`, separada de la identidad de Keycloak):

* **`Currency`** — `code`, `name`, `symbol`, `value_in_usd`. **USD es la divisa
  universal**: se crea mediante la migración `0002_seed_usd`, su valor está fijado
  de forma estricta en `1.000000` (ver `Currency.save`) y no puede eliminarse.
  `admin`/`manager` tienen CRUD completo en `/currencies/`, que es cómo suben o bajan
  el valor de cualquier otra divisa frente al dólar.
* **`Wallet`** — una por usuario, contiene el saldo en efectivo en USD.
* **`WalletHolding`** — cuánto de cada divisa tiene una billetera.
* **`WalletTransaction`** — un registro de auditoría de cargas de saldo, compras y
  retiros.

Flujos de cara al usuario (rol `user`, `/wallet/` y `/trade/`):

* **Cargar saldo** — un formulario normal de monto, más un botón de prueba de un solo
  clic "Cargar $100 de prueba" (`wallet_deposit_test`) que acredita un monto de
  demostración fijo sin necesidad de un método de pago real.
* **Comprar** — `/trade/` gasta saldo en USD para adquirir una divisa hacia la
  billetera, a la tasa actual (`value_in_usd`) de esa divisa.
* **Retirar** — un formulario normal (elegir divisa + monto) más un botón de prueba de
  un solo clic por cada divisa, "Retirar 1 (prueba)" (`wallet_withdraw_test`), que
  quita un pequeño monto de demostración fijo de esa tenencia.
