# Exchange Pro — Documentación técnica

Proyecto de la materia **Ingeniería de Software II** (Equipo 10). Casa de cambio
ficticia que opera en guaraníes, construida de forma incremental en sprints.

Este documento es la referencia técnica completa: arquitectura, stack, modelo de
datos, cada función del backend, las pruebas y cómo levantar y operar el proyecto.

Documentos hermanos:

| Documento | Para qué sirve |
|-----------|----------------|
| `MANUAL_DE_USUARIO.md` | Cómo usar cada pantalla del sitio, con los usuarios de prueba. |
| `SECRETOS_Y_CONFIGURACION.md` | Qué secretos existen, qué protege cada uno y cómo cambiarlos. |
| `COLABORACION_CON_IA.md` | Cómo se usó asistencia de IA en el proyecto, qué aportó y dónde se equivocó. |

---

## 1. Qué hace el sistema

Exchange Pro simula una casa de cambio:

- La **divisa base es el guaraní paraguayo (PYG)**. Toda cotización se expresa en
  guaraníes por 1 unidad de divisa extranjera.
- Cada divisa tiene **dos precios**: uno de compra (lo que la casa de cambio paga
  al cliente) y uno de venta (lo que le cobra). El de compra siempre es menor; la
  diferencia es el *spread* o margen del negocio.
- Un usuario registra **tarjetas de crédito** en su billetera, carga saldo en
  guaraníes con ellas, compra divisas (pagando con el saldo o directamente con una
  tarjeta), las vende de vuelta y las retira.
- Cualquiera puede consultar el **historial de cotizaciones** de cada divisa, en
  tabla y en gráfico.
- Un administrador puede **adelantar un día** el reloj del sistema: cada divisa
  activa varía entre −5 % y +7 %, lo que permite ver el comportamiento de las
  cotizaciones a lo largo del tiempo sin esperar días reales.

La identidad (login, registro, roles) no la maneja la aplicación: la maneja
Keycloak. Django sólo consume esa identidad.

---

## 2. Stack y tecnologías

| Pieza | Tecnología | Versión | Rol en el sistema |
|-------|-----------|---------|-------------------|
| Aplicación web | Django | 5.1.2 | Páginas renderizadas en el servidor y toda la lógica de negocio. |
| Servidor de aplicación | Gunicorn | 22.0.0 | Sirve Django en producción dentro del contenedor. |
| Identidad | Keycloak | 25.0.6 | Login, registro, logout, roles de realm, Admin API. |
| Puente OIDC | mozilla-django-oidc | 4.0.1 | Conecta el login de Django con Keycloak por OpenID Connect. |
| Base de datos de la app | PostgreSQL | 16 | Divisas, cotizaciones, billeteras, tarjetas, movimientos. |
| Base de datos de Keycloak | PostgreSQL | 16 | Usuarios, realms, clientes (instancia separada). |
| Driver de base de datos | psycopg2-binary | 2.9.10 | Conexión de Django a PostgreSQL. |
| Proxy inverso | Nginx | 1.27-alpine | Única puerta de entrada: enruta al navegador hacia Django o Keycloak. |
| Cliente HTTP | requests | 2.32.3 | Llamadas de Django al Admin API de Keycloak. |
| Variables de entorno | python-dotenv | 1.0.1 | Carga de configuración desde `.env`. |
| Orquestación | Docker Compose | — | Levanta los cinco servicios con un comando. |

**No se usa ningún framework de frontend ni librería de JavaScript.** La interfaz
son plantillas de Django con CSS propio, y el gráfico de cotizaciones es un SVG
generado en el servidor. Esto mantiene el proyecto sin dependencias de CDN y hace
que el gráfico se vea incluso con JavaScript deshabilitado.

### Flujo de una petición

```
Navegador  ──►  Nginx :80  ──┬──►  web (Gunicorn + Django) :8000  ──►  db (PostgreSQL)
                             │
                             └──►  keycloak :8080  ──►  keycloak-db (PostgreSQL)
```

Nginx enruta por prefijo: `/realms/`, `/admin/`, `/resources/`, `/js/` y
`/.well-known/` van a Keycloak; todo lo demás (incluido `/oidc/`) va a Django.

### Un detalle de integración que cuesta encontrar

Django llama a Keycloak de dos maneras distintas, a propósito:

- Los endpoints a los que se **redirige el navegador** (autorización, logout) usan
  la URL pública, la misma que el usuario escribió en la barra de direcciones.
- Los endpoints que Django llama **servidor a servidor** (token, userinfo, jwks)
  usan la URL interna de Docker (`http://keycloak:8080`), porque `localhost` dentro
  del contenedor `web` apunta al propio contenedor.

Keycloak calcula el *issuer* (`iss`) de cada token a partir de la cabecera `Host`
de la petición que lo generó. Por eso `core/backends.py` fuerza la cabecera `Host`
en las llamadas servidor a servidor (`_issuer_host_header`): si no coincide con la
del paso de autorización, Keycloak rechaza el token con *"Invalid token issuer"*
aunque sea la misma sesión. `core/middleware.py` complementa esto detectando en
cada petición el host público real, de modo que el login funciona tanto por
`localhost` como por la IP de la máquina en la red local, sin reconfigurar nada.

---

## 3. Estructura del repositorio

```
Global-Exchange/
├── Documentacion/                  ← toda la documentación del proyecto
│   ├── DOCUMENTACION_TECNICA.md    ← este documento
│   ├── MANUAL_DE_USUARIO.md
│   ├── SECRETOS_Y_CONFIGURACION.md
│   ├── COLABORACION_CON_IA.md
│   └── sprint1_guide.md / .pdf     ← enunciado de la cátedra
├── Jira.csv                        ← export del tablero SCRUM
├── LICENSE
└── currency-exchange-sprint1/
    ├── docker-compose.yml          ← definición de los cinco servicios
    ├── .env                        ← valores reales (NO se versiona)
    ├── .env.example                ← plantilla de variables
    ├── nginx/default.conf          ← reglas del proxy inverso
    ├── keycloak/realm-export.json  ← realm, clientes, roles y usuarios de demo
    ├── postgres/init/              ← SQL de referencia para crear las bases
    ├── tools/                      ← utilidades de desarrollo, fuera de la app
    │   └── validate_palette.py     ← comprueba la legibilidad de la paleta
    └── backend/
        ├── Dockerfile
        ├── requirements.txt
        ├── manage.py
        ├── config/
        │   ├── settings.py         ← configuración de Django y de OIDC
        │   ├── urls.py             ← mapa de URLs a vistas
        │   └── wsgi.py / asgi.py
        ├── templates/403.html      ← página de acceso denegado
        ├── static/                 ← servidos por nginx directamente
        │   ├── css/app.css         ← la hoja de estilos completa
        │   ├── fonts/              ← Inter y Space Grotesk autoalojadas
        │   └── img/favicon.svg
        └── core/                   ← la única app de Django
            ├── models.py           ← modelo de datos
            ├── views.py            ← una función por pantalla o acción
            ├── forms.py            ← validación de cada formulario
            ├── roles.py            ← fuente de verdad de los roles
            ├── backends.py         ← login OIDC contra Keycloak
            ├── middleware.py       ← detección del host público
            ├── context_processors.py ← datos del menú en todas las páginas
            ├── admin.py            ← panel /django-admin/
            ├── tests.py            ← 98 pruebas unitarias
            ├── migrations/         ← evolución del esquema
            ├── templatetags/money.py ← formato de montos
            ├── services/
            │   ├── keycloak_admin.py ← cliente del Admin API de Keycloak
            │   ├── simulation.py     ← lógica de "avanzar un día"
            │   └── charts.py         ← cálculo del gráfico de historial
            └── templates/core/     ← las plantillas HTML
                └── _rocket.svg.html ← el logotipo, incluido en línea
```

---

## 4. Modelo de datos

Todo vive en la base `exchange_db`, separada de la base de identidad de Keycloak.
Los usuarios de Django (`auth_user`) se crean automáticamente en el primer login a
partir de los datos que envía Keycloak.

### `Currency` — una divisa

| Campo | Tipo | Significado |
|-------|------|-------------|
| `code` | texto único (8) | Código tipo ISO: `USD`, `EUR`, `PYG`. Se guarda siempre en mayúsculas. |
| `name` | texto (80) | Nombre legible. |
| `symbol` | texto (8) | Símbolo opcional (`US$`, `€`). |
| `buy_rate` | decimal 18,6 | **Precio de compra**: guaraníes que la casa de cambio *paga* por 1 unidad. |
| `sell_rate` | decimal 18,6 | **Precio de venta**: guaraníes que la casa de cambio *cobra* por 1 unidad. |
| `is_active` | booleano | Si está inactiva no aparece para operar ni se recotiza en la simulación. |
| `created_at` / `updated_at` | fecha y hora | Auditoría. |

Propiedades calculadas: `is_base`, `mid_rate` (promedio de ambos precios),
`spread` (diferencia absoluta) y `spread_percent` (margen respecto del promedio).

**El guaraní es especial.** `BASE_CURRENCY_CODE = "PYG"`. `Currency.save()` fuerza
sus dos cotizaciones a `1.000000` sin importar lo que se intente guardar, y la
vista de borrado se niega a eliminarlo. Es la referencia contra la que se mide todo
lo demás, así que no puede flotar ni desaparecer.

El método `record_rate(on_date, source)` guarda la cotización actual como punto del
historial. Usa `update_or_create` sobre `(divisa, fecha)`: varios cambios en el
mismo día actualizan el mismo punto en lugar de ensuciar el gráfico. Sobre el
guaraní no hace nada.

### `CurrencyRate` — historial de cotizaciones

Un punto por divisa y por día (restricción única `unique_currency_rate_per_day`),
con `buy_rate`, `sell_rate` y un `source` que dice de dónde salió: `inicial` (carga
de datos de demostración), `manual` (un gestor cambió el valor a mano) o
`simulacion` (lo generó el botón de avanzar un día).

### `SimulationState` — reloj simulado

Fila única (patrón *singleton*, se obtiene con `SimulationState.load()`) que guarda
`current_date` y `days_advanced`. Es la fecha con la que se fechan los puntos del
historial, de modo que un cambio manual de cotización y la simulación comparten el
mismo calendario.

### `Wallet` — billetera

Una por usuario, con `pyg_balance` (saldo en guaraníes). Propiedades calculadas:
`holdings_value_pyg` (cuánto recibiría el usuario si vendiera hoy todas sus
divisas, valuadas al precio de **compra**) y `total_value_pyg`.

Las billeteras se crean de forma diferida: `views._get_wallet()` usa
`get_or_create`, así que un usuario nuevo no necesita ningún alta previa.

### `PaymentMethod` — tarjeta de crédito

| Campo | Significado |
|-------|-------------|
| `wallet` | A quién pertenece. |
| `label` | Alias elegido por el usuario. |
| `holder_name` | Titular. |
| `brand` | `visa`, `mastercard`, `amex` u `otra`, deducida del número. |
| `last4` | **Los únicos cuatro dígitos del número que se guardan.** |
| `expiry_month` / `expiry_year` | Vencimiento. |
| `is_active` | Permite desactivar sin borrar el historial. |

**El número completo de la tarjeta nunca se almacena.** El formulario lo recibe, lo
valida con el algoritmo de Luhn, deduce la marca de los primeros dígitos, se queda
con los últimos cuatro y descarta el resto. No existe ninguna columna donde pudiera
quedar. Hay una prueba automática que verifica exactamente eso
(`test_only_the_last_four_digits_are_stored`).

### `WalletHolding` — tenencia

Cuánto tiene una billetera de una divisa (`unique_wallet_currency` evita
duplicados). `value_pyg` la valúa al precio de compra vigente.

### `WalletTransaction` — movimiento

Registro de auditoría de toda operación. `kind` puede ser `deposit` (carga de
saldo), `buy` (compra), `sell` (venta) o `withdraw` (retiro). Guarda la cantidad de
divisa (`amount`), el equivalente en guaraníes (`pyg_amount`), la cotización
aplicada (`rate_used`) y, si hubo tarjeta, cuál (`payment_method`).

`WalletHolding` y `WalletTransaction` apuntan a `Currency` con `on_delete=PROTECT`:
no se puede borrar una divisa con la que alguien operó. La vista de borrado atrapa
el `ProtectedError` y muestra un mensaje claro en lugar de un error 500.

### Migraciones

| Migración | Qué hace |
|-----------|----------|
| `0001_initial` | Crea `Currency`, `Wallet`, `WalletHolding`, `WalletTransaction`. |
| `0002_seed_usd` | Siembra el dólar (del Sprint 2, cuando el dólar era la base). |
| `0003_guarani_base_rates_payments_history` | Esquema del Sprint 3: renombra `value_in_usd` a `buy_rate`, agrega `sell_rate`, renombra `usd_balance` a `pyg_balance` y `usd_amount` a `pyg_amount`, y crea `CurrencyRate`, `SimulationState` y `PaymentMethod`. |
| `0004_seed_guarani_and_history` | Datos del Sprint 3 (ver abajo). |

`0004` hace la transición de dólar a guaraní sin perder nada:

1. Convierte las divisas existentes multiplicando su valor antiguo (expresado en
   dólares) por una cotización de referencia de 7.300 Gs./USD, y le aplica un
   margen de ±1,5 % para obtener precio de compra y de venta.
2. Convierte con la misma referencia los saldos y movimientos ya cargados, para que
   los datos viejos sigan siendo coherentes.
3. Crea el guaraní como divisa base.
4. Siembra USD, EUR, BRL y ARS si no existen.
5. Genera 30 días de historial hacia atrás para cada divisa, de forma determinista
   (semilla fija), de modo que la pestaña de cotizaciones tenga datos y el gráfico
   se vea apenas se levanta el proyecto.
6. Crea la fila del reloj simulado con la fecha de hoy.

> Si la base de datos ya se usó para pruebas manuales, las divisas que el equipo
> haya creado a mano también se convierten. En el entorno de desarrollo actual
> quedaron así algunas filas sueltas del Sprint 2 (`GUA`, `PARG`); se pueden
> eliminar desde *Divisas* sin consecuencias.

---

## 5. Roles y permisos

Los roles viven en Keycloak como *realm roles* y `core/roles.py` es la única fuente
de verdad del lado de Django.

| Rol | Qué puede hacer |
|-----|-----------------|
| `admin` | Todo lo de `manager`, más gestionar usuarios y usar la simulación de días. |
| `manager` | Gestionar divisas y sus precios de compra y venta. |
| `user` | Operar: billetera, tarjetas, comprar, vender y retirar divisas. |

En cada login, `core/backends.py` copia los roles de realm de Keycloak a grupos de
Django con el mismo nombre, de modo que el resto de la aplicación sólo necesita
mirar `request.user`. Una cuenta que se autorregistra recibe el rol `user`
automáticamente en su primer inicio de sesión. Si el rol incluye `admin`, se
activan además `is_staff` e `is_superuser`, que es lo que habilita
`/django-admin/`.

Funciones de `roles.py`: `roles_from_claims(claims)` extrae los roles conocidos del
token, `user_roles(user)` los lee de los grupos, `has_role(user, *roles)` pregunta
por varios a la vez, y `is_admin` / `is_manager` / `is_trader` son los atajos de uso
diario. `is_manager` devuelve verdadero también para un `admin`: la herencia de
capacidades está en un solo lugar.

El decorador `views.role_required(*roles)` envuelve una vista para exigir sesión
iniciada y al menos uno de los roles indicados; si no se cumple lanza
`PermissionDenied`, que Django convierte en un 403 renderizado con
`templates/403.html`.

---

## 6. URLs y funciones del backend

Todas las URLs se declaran en `config/urls.py`. La columna "acceso" indica qué rol
hace falta.

### Público

| URL | Función | Acceso | Qué hace |
|-----|---------|--------|----------|
| `/` | `views.home` | público | Página de inicio con la pizarra de cotizaciones del día. |
| `/history/` | `views.currency_history` | público | Historial de una divisa, en gráfico y tabla. Acepta `?currency=<id>&days=7\|30\|90\|0`. |
| `/login/` | `views.login_view` | público | Redirige al formulario de login de Keycloak. |
| `/register/` | `views.register_view` | público | Igual, pero con `?kc_action=register` para abrir el formulario de alta. |
| `/logout/` | `views.logout_view` | — | Cierra la sesión de Django y la de Keycloak. |

`currency_history` filtra el historial por el período elegido contando hacia atrás
desde la fecha del reloj simulado, arma el gráfico con
`services.charts.build_rate_chart()`, invierte la lista para que la tabla muestre lo
más reciente primero y calcula la variación porcentual del período con
`_variation()`.

### Sesión iniciada (cualquier rol)

| URL | Función | Qué hace |
|-----|---------|----------|
| `/dashboard/` | `views.dashboard` | Panel con los datos de la sesión y los accesos que permite el rol. |
| `/account/` | `views.account` | Datos de la cuenta. |
| `/delete-account/` | `views.delete_account_view` | Elimina la cuenta en Keycloak; exige escribir `DELETE`. |

### Billetera — rol `user`

| URL | Función | Qué hace |
|-----|---------|----------|
| `/wallet/` | `wallet_view` | Saldo, valor de las tenencias, tarjetas, formularios y últimos 20 movimientos. |
| `POST /wallet/payment-methods/new/` | `payment_method_create` | Registra una tarjeta (valida Luhn, guarda sólo los últimos 4 dígitos). |
| `POST /wallet/payment-methods/<id>/delete/` | `payment_method_delete` | Elimina una tarjeta propia. Busca con `wallet=wallet`, así que una tarjeta ajena da 404. |
| `POST /wallet/deposit/` | `wallet_deposit` | Carga saldo cobrando la tarjeta elegida. |
| `POST /wallet/deposit/test/` | `wallet_deposit_test` | Botón de un clic: carga Gs. 500.000 con la primera tarjeta registrada. |
| `POST /wallet/withdraw/` | `wallet_withdraw` | Retira divisa de la billetera. |
| `POST /wallet/withdraw/<id>/test/` | `wallet_withdraw_test` | Botón de un clic: retira 1 unidad (o el resto si es menos). |

`_credit_balance()` concentra el alta de saldo: suma al `pyg_balance` y crea el
`WalletTransaction`, todo dentro de `transaction.atomic()`.

### Compra y venta — rol `user`

`/trade/` (`views.trade`) atiende las dos operaciones en una sola pantalla. Un campo
oculto `action` (`buy` o `sell`) decide qué formulario se valida, de modo que los
errores de uno no borran lo escrito en el otro.

- **`_process_buy()`** — aplica el **precio de venta** de la divisa. Calcula
  `pyg_amount / sell_rate` redondeando hacia abajo (`ROUND_DOWN`), para no regalar
  fracciones. Si se paga con el saldo, lo verifica y lo descuenta; si se paga con
  tarjeta, el saldo no se toca. Rechaza montos tan bajos que no alcancen para
  comprar ni una millonésima de unidad.
- **`_process_sell()`** — aplica el **precio de compra**, descuenta la tenencia y
  acredita guaraníes en el saldo.

Ambas corren dentro de `transaction.atomic()` y dejan el movimiento registrado con
la cotización usada.

**Vista previa de la conversión.** Antes de confirmar la compra, la pantalla muestra
cuántas unidades de divisa va a recibir el usuario, a qué cotización, con qué medio de
pago, y un gráfico de barras que compara lo que paga contra lo que recibiría si
vendiera en ese mismo instante: la diferencia entre las dos barras es el margen de la
casa de cambio, hecho visible en lugar de escondido. La vista avisa además, antes de
enviar el formulario, si se eligió pagar con el saldo y el saldo no alcanza.

Para que funcione, `trade` publica dos datos con `json_script`: `rate_table`
(construida por `_rate_table()`, con el código y los dos precios de cada divisa activa,
como texto para no perder precisión) y el saldo de la billetera. Un `<script>` de la
propia plantilla recalcula en el navegador con las mismas reglas que `_process_buy`
—dividir por el precio de venta y redondear hacia abajo a seis decimales— sin pedirle
nada al servidor.

Dos aclaraciones sobre el diseño:

- **La vista previa es informativa; el servidor es la autoridad.** La cuenta del
  navegador usa punto flotante y la del servidor `Decimal`, así que en casos extremos
  podrían diferir en la última millonésima. Lo que se acredita es siempre lo que
  calcula el servidor.
- **La compra funciona sin JavaScript.** Sin él sólo se pierde la vista previa; el
  formulario se envía igual y se aplica el mismo precio de venta de la pizarra.

Los dos formularios de la página tienen un campo `currency`, así que `trade` les da
prefijos de `id` distintos (`id_buy_%s` e `id_sell_%s`). Sin eso Django emitiría dos
veces el mismo `id="id_currency"`: HTML inválido, y la vista previa no podría saber a
qué selector escuchar. Hay una prueba que falla si el problema reaparece. La consecuencia del diseño es la que se espera de una casa de
cambio real: comprar y vender de inmediato deja al usuario con menos guaraníes que
al empezar, y esa pérdida es exactamente el margen
(`test_the_spread_is_the_cost_of_a_round_trip`).

### Gestión de divisas — roles `admin` y `manager`

| URL | Función | Qué hace |
|-----|---------|----------|
| `/currencies/` | `currency_management` | Lista todas las divisas con ambos precios y el margen. |
| `/currencies/new/` | `currency_create` | Alta. Al guardar deja un punto en el historial. |
| `/currencies/<id>/edit/` | `currency_edit` | Edición. Sobre el guaraní deshabilita código y cotizaciones. |
| `/currencies/<id>/delete/` | `currency_delete` | Baja con confirmación. Rechaza el guaraní y las divisas con operaciones. |

Tanto el alta como la edición llaman a `currency.record_rate(on_date=_simulated_today())`,
así que un cambio manual de cotización aparece en el gráfico igual que uno de la
simulación.

### Simulación de días — rol `admin`

| URL | Función | Qué hace |
|-----|---------|----------|
| `/simulation/` | `simulation_panel` | Muestra el día simulado, los días acumulados, las cotizaciones vigentes y el resumen del último avance. |
| `POST /simulation/advance-day/` | `simulation_advance_day` | Adelanta un día. |

La lógica está en `services/simulation.py`, separada de la vista para poder probarla
sin pasar por HTTP:

```python
advance_one_day(rng=None) -> (SimulationState, list[CurrencyChange])
```

Dentro de una única transacción atómica:

1. Toma la fila de `SimulationState` con `select_for_update()` (bloqueo de fila, para
   que dos administradores pulsando a la vez no desincronicen el calendario) y le
   suma un día.
2. Para cada divisa **activa** que no sea el guaraní, sortea un factor en
   `[-5 %, +7 %]` y lo aplica **al precio de compra y al de venta por igual**. Esa
   decisión es deliberada: conserva el margen relativo de la casa de cambio y
   garantiza que la compra nunca pase por encima de la venta, por muchos días que se
   simulen (`test_the_buy_price_stays_below_the_sell_price` lo verifica sobre 25
   días consecutivos).
3. Aplica un piso de `0.000100` a las cotizaciones, para que una racha larga a la
   baja no lleve una divisa a cero.
4. Guarda el punto del historial del día nuevo con `source="simulacion"`.
5. Devuelve la lista de cambios; la vista la guarda en la sesión para mostrar la
   tabla de "antes y después" una sola vez, después del redirect.

El parámetro `rng` permite inyectar un `random.Random(semilla)` y hacer las pruebas
deterministas.

### Gestión de usuarios — rol `admin`

| URL | Función | Qué hace |
|-----|---------|----------|
| `/manage/users/` | `admin_users` | Lista y busca usuarios de Keycloak. |
| `/manage/users/new/` | `admin_user_create` | Crea la cuenta y le asigna roles. |
| `/manage/users/<id>/roles/` | `admin_user_roles` | Edita los roles de una cuenta. |
| `/manage/users/<id>/delete/` | `admin_user_delete` | Elimina una cuenta (no la propia). |

Todas hablan con Keycloak a través de `services/keycloak_admin.py`, que se autentica
con *client credentials* usando la cuenta de servicio `exchange-admin-api` (necesita
el rol `realm-management: realm-admin`, ya configurado en el realm export) y cachea
el token mientras dura la petición. `set_user_roles` compara los roles pedidos
contra los actuales y hace sólo el `DELETE` y el `POST` necesarios.

### Panel `/django-admin/`

Es el admin autogenerado de Django, distinto de la pestaña *Usuarios*: opera
directamente sobre las tablas, no sobre Keycloak. Sólo entran cuentas con
`is_staff`, es decir, con rol `admin`. `core/admin.py` registra `Currency`,
`CurrencyRate`, `SimulationState`, `Wallet` (con sus tenencias y tarjetas en línea),
`PaymentMethod` y `WalletTransaction`.

### Código que corre en todas las páginas

- **`core.context_processors.roles`** agrega al contexto de cualquier plantilla los
  roles del usuario, los booleanos `can_trade` / `can_manage_currencies` /
  `can_simulate` / `can_manage_users` y el código y símbolo de la divisa base. Es lo
  que usa `base.html` para decidir qué pestañas mostrar. Funciona también para
  visitantes anónimos, porque la pestaña de cotizaciones es pública.
- **`core.middleware.LocalhostAutoDetectMiddleware`** calcula en cada petición el
  host público real y actualiza `SITE_URL`, `KEYCLOAK_PUBLIC_URL` y los endpoints
  OIDC de navegador. Ver la nota de integración de la sección 2.

### Filtros de plantilla

`core/templatetags/money.py` da formato paraguayo (punto para miles, coma para
decimales), que no es el que trae Django por defecto:

| Filtro | Para qué | Ejemplo |
|--------|----------|---------|
| `gs` | Montos en guaraníes, sin decimales. | `7300000` → `7.300.000` |
| `rate` | Cotizaciones, con la precisión justa según la magnitud. | `7409.5` → `7.410` · `6.5` → `6,50` |
| `units` | Cantidades de divisa, sin ceros sobrantes. | `1000.000000` → `1.000,00` |

### El gráfico de historial

`services/charts.py` convierte una lista de `CurrencyRate` en un `RateChart`: dos
cadenas de puntos para los `<polyline>` del SVG (compra y venta), las etiquetas de
ambos ejes y el rango de valores. Detalles que importan:

- El rango vertical se calcula con un 8 % de margen sobre el mínimo y el máximo, así
  las líneas no quedan pegadas al borde.
- Una serie completamente plana recibe un margen artificial, para no dividir por
  cero (`test_a_flat_series_does_not_divide_by_zero`).
- Las etiquetas del eje horizontal se espacian para que nunca haya más de 8, por
  largo que sea el período.

---

## 7. La interfaz

### Sistema de diseño

No hay ningún framework de CSS ni librería de JavaScript: la interfaz es
`backend/static/css/app.css`, escrita a mano, más las plantillas de Django. La
decisión es deliberada — un framework habría impuesto su propio aspecto reconocible,
que es justamente lo que se quería evitar — y mantiene la propiedad de cero
dependencias externas del proyecto.

**Paleta.** Morado oscuro como color estructural, morado claro para los estados
intermedios y rosa pastel como acento. Todo está declarado como variables CSS en
`:root` (`--purple-50` a `--purple-950`, `--pink-50` a `--pink-600`), así que cambiar
la marca es editar un bloque, no buscar colores por los archivos.

**Tipografía.** Space Grotesk para títulos y cifras destacadas, Inter para el resto.
Las dos están **autoalojadas** en `backend/static/fonts/` (sólo el subconjunto latino,
unos 163 kB en total): no se pide nada a Google Fonts en tiempo de ejecución. Todo lo
que sea dinero usa numerales tabulares (`font-variant-numeric: tabular-nums`), sin lo
cual las columnas de una pizarra de cotizaciones se desalinean al cambiar de valor.

**El hexágono** es el motivo de la identidad, y aparece sólo donde no estorba la
lectura: el contenedor del logotipo, las insignias de divisa (el código dentro de un
hexágono), el marcador de cada título de sección (`.hexed`), el indicador de la
pestaña activa en la navegación, las llaves de color de la leyenda del gráfico, la
flecha de la vista previa de compra, y una trama de panal muy tenue en el fondo de la
barra lateral y del encabezado de la portada (un SVG embebido como `data:` URI en la
variable `--hex-texture`).

**El logotipo** es un cohete morado con la estela en rosa pastel, dibujado como SVG en
`core/templates/core/_rocket.svg.html` e incluido en línea para que no haga falta una
petición aparte. El favicon es la misma figura simplificada dentro de un hexágono.

**Los colores de los gráficos se midieron, no se eligieron a ojo.** Las dos series del
historial usan `#6D28D9` (venta) y `#EC4899` (compra), que pasan las cinco
comprobaciones de `tools/validate_palette.py`: banda de luminosidad, piso de croma,
separación bajo protanopía y deuteranopía, piso de visión normal y contraste contra el
fondo. El rosa pastel de la marca **no** sirve como color de línea —queda fuera de la
banda de luminosidad admisible para una marca de datos, igual que el morado más
oscuro—, así que el gráfico usa los pasos intermedios de los mismos tonos y los pastel
quedan para superficies. La variación porcentual, además, nunca se comunica sólo con
color: lleva signo y flecha.

**Lo que se corrigió del diseño anterior.** La versión previa tenía los defectos
típicos de una interfaz sin decisiones tomadas: Arial, una única sombra difusa
repetida en todas las tarjetas, casi todo el texto en 14 px sin jerarquía y un
subtítulo gris debajo de cada título. Ahora hay una escala tipográfica de seis pasos,
borde fino en lugar de sombra (con una sola excepción, `.card-raised`, para destacar
la tarjeta principal de una pantalla), y las tablas son deliberadamente más densas que
el resto de la interfaz, porque una pizarra de cotizaciones se lee de un golpe.

### Estructura de la página

`base.html` define una rejilla de dos columnas: la barra lateral fija con toda la
navegación, y el contenido. La barra lateral agrupa los enlaces en *General*,
*Operar* y *Administración*, muestra sólo lo que el rol permite (lo decide
`core.context_processors.roles`) y marca la página actual comparando `request.path`.
Abajo quedan el usuario, sus roles y el cierre de sesión.

Por debajo de 960 px de ancho la rejilla pasa a una sola columna y la navegación se
convierte en una tira horizontal desplazable. **No hay menú hamburguesa y por lo tanto
no hace falta JavaScript** para navegar en un teléfono.

Cada pantalla define además un título y, cuando corresponde, acciones para la barra
superior (`{% block topbar_title %}` y `{% block topbar_actions %}`). Ahí viven los
datos que conviene tener siempre a la vista: el saldo en guaraníes mientras se opera,
o el día simulado mientras se mira el historial.

### Botones y accesibilidad

Un solo juego de variantes (`.btn-primary`, `.btn-accent`, `.btn-secondary`,
`.btn-ghost`, `.btn-danger`, más `.btn-sm`), todas con **44 px de alto mínimo**, estado
de hover con un desplazamiento de 1 px, estado `:active` y transiciones cortas. Hay un
único anillo de foco definido para `:focus-visible`, así que la interfaz se puede
recorrer con el teclado. Las variaciones de cotización nunca se comunican sólo con
color: siempre llevan signo y flecha (`▲ +3,21 %` / `▼ -1,80 %`). Todo el bloque de
transiciones se desactiva bajo `prefers-reduced-motion`.

### Menos clics

Un pedido explícito del sprint fue reducir los clics de cada acción. Lo que se hizo:

| Acción | Antes | Ahora |
|--------|-------|-------|
| Comprar una divisa vista en la pizarra | Ver la pizarra, bajar al formulario y volver a elegir la divisa | El botón *Comprar* de cada fila lleva a `?currency=<id>#comprar`, con la divisa ya seleccionada |
| Vender una tenencia | Ir a *Comprar y vender* y elegir la divisa de nuevo | El botón *Vender* de la billetera lleva a `?sell=<id>#vender`, ya seleccionada |
| Agregar una tarjeta | Cargar una página aparte, completar, volver | Formulario desplegable en la propia billetera (abierto de entrada si no hay ninguna tarjeta) |
| Eliminar una tarjeta | Enlace, página de confirmación, botón | Botón en la propia fila, con confirmación del navegador |
| Cargar un monto habitual | Teclear seis o siete dígitos | Atajos de monto que rellenan el campo con un clic, más el botón de carga de prueba |
| Ver una tendencia simulada | Pulsar *Avanzar un día* siete veces | *Avanzar una semana*, un clic, que informa la variación **neta** del período |
| Consultar el saldo | Ir a la billetera | Visible en la barra superior de todas las pantallas de operación |

La preselección se implementa con el `initial` de los formularios en `views.trade`; un
id inexistente en la URL simplemente no selecciona nada y no rompe la página.

### Archivos estáticos

Al arrancar, el contenedor `web` corre `collectstatic` y deja todos los archivos
estáticos —los propios (CSS, tipografías, logotipo) y los del panel
`/django-admin/`— en un volumen de Docker compartido (`static_files`). nginx lo monta
de sólo lectura y sirve `/static/` desde ahí.

Esto además **arregla un problema que existía desde el Sprint 1**: Django bajo gunicorn
no sirve archivos estáticos por sí mismo (eso sólo lo hace `runserver`), así que
`/static/` nunca respondía y el panel `/django-admin/` se venía mostrando sin estilos.

En `location /static/` se usa `root` y no `alias`, porque `try_files` no se comporta de
forma fiable combinado con `alias`.

---

## 8. Pruebas

98 pruebas unitarias en `backend/core/tests.py`, todas en verde.

```bash
# Con el stack levantado
docker compose exec web python manage.py test core

# Con detalle de cada prueba
docker compose exec web python manage.py test core -v 2
```

Django crea una base de datos temporal (`test_exchange_db`), le aplica todas las
migraciones y la destruye al terminar: las pruebas nunca tocan los datos reales.
Como las migraciones siembran las divisas de demostración, varias pruebas se apoyan
en ellas. Cuando una prueba necesita cotizaciones de números redondos usa el código
`XTS` (reservado por la norma ISO 4217 justamente para pruebas) con compra 7.000 y
venta 8.000, lo que hace que las cuentas sean exactas y verificables a mano.

| Grupo | Nº | Qué cubre |
|-------|----|-----------|
| `RolesTests` | 5 | Los ayudantes de roles, la herencia de `admin` sobre `manager` y que los roles desconocidos del token se descarten. |
| `BaseCurrencyTests` | 3 | El guaraní se siembra como base, sus cotizaciones quedan fijas en 1 aunque se intente cambiarlas, y las demás divisas se cotizan en miles de guaraníes con compra por debajo de venta. |
| `CurrencyRatesTests` | 6 | Mayúsculas del código, `mid_rate`, `spread`, `spread_percent`, un solo punto de historial por día y que el guaraní no tenga historial. |
| `CurrencyFormTests` | 4 | Acepta compra < venta; rechaza compra > venta, compra = venta y precios en cero o negativos. |
| `PaymentMethodFormTests` | 6 | Luhn, longitud, tarjeta vencida, espacios en el número, detección de Visa/Mastercard/Amex y que **el número completo no quede guardado**. |
| `WalletDepositFormTests` | 2 | El selector de tarjeta sólo acepta tarjetas de la billetera propia. |
| `DeleteAccountFormTests` | 2 | La confirmación exige la palabra `DELETE`. |
| `RoleRequiredViewTests` | 8 | Acceso por rol a cada sección, incluido que la simulación sea sólo de `admin` y que el historial sea público. |
| `WalletTests` | 19 | Alta y baja de tarjetas (incluido el render de sus pantallas y que no se pueda borrar una ajena), carga de saldo con y sin tarjeta, compra con saldo y con tarjeta al precio de venta, compra con tarjeta ajena rechazada, venta al precio de compra, el margen como costo de ida y vuelta, y los límites de saldo y tenencia en venta y retiro. |
| `NavigationTests` | 4 | La barra lateral se dibuja, un visitante anónimo no ve las secciones privadas, un usuario no ve las de administración, la página actual queda marcada como activa y la hoja de estilos y el logotipo están enlazados. |
| `FewerClicksTests` | 6 | `?currency=` y `?sell=` dejan la divisa preseleccionada (y una divisa inexistente en la URL no rompe nada), el alta de tarjeta está en la propia billetera, los montos predefinidos se dibujan y una tarjeta se borra desde su fila. |
| `AdvanceWeekTests` | 7 | `advance_days(7)` mueve el reloj siete días, deja siete puntos de historial, informa la variación **neta** del período y rechaza un conteo inválido; el botón funciona sólo por POST y sólo para un administrador. |
| `BuyPreviewTests` | 7 | La vista previa se renderiza, la tabla de cotizaciones publica ambos precios de cada divisa activa (y no la base ni las inactivas), el saldo se publica para poder avisar, los dos formularios no comparten ids y la cuenta de la vista previa coincide con la que hace el servidor. |
| `SimulationTests` | 7 | El reloj avanza, se recotizan todas las activas y ninguna inactiva, el guaraní queda fuera, cada cambio cae dentro de −5 %/+7 % (25 semillas), la compra sigue por debajo de la venta tras 25 días, y cada día deja su punto de historial. |
| `SimulationViewTests` | 4 | El botón avanza el día, un `GET` no, el panel muestra el antes y después, y ese resumen se muestra una sola vez. |
| `CurrencyHistoryViewTests` | 4 | Divisa por defecto, selección por parámetro, un período más corto devuelve menos puntos y la tabla viene ordenada de lo más nuevo a lo más viejo. |
| `RateChartTests` | 4 | Historial vacío, un punto por cotización en cada serie, serie plana sin división por cero y etiquetas del eje sin amontonarse. |

### Qué no cubren las pruebas automáticas

- El login real contra Keycloak y el intercambio de tokens OIDC. Las pruebas usan
  `force_login`, que salta el proveedor de identidad.
- Las llamadas al Admin API de Keycloak (crear, editar y borrar usuarios). Necesitan
  un Keycloak levantado y se verifican a mano.
- La apariencia del SVG en el navegador: se verifica que los puntos se calculen
  bien, no que el gráfico se vea bonito.

Esas tres zonas se cubren con el recorrido manual del `MANUAL_DE_USUARIO.md`.

---

## 9. Levantar y operar el proyecto

### Requisitos

Docker Engine, el plugin de Docker Compose y Git.

### Arranque

```bash
cd currency-exchange-sprint1
cp .env.example .env     # la primera vez; revisar los valores
docker compose up -d --build
```

El contenedor `web` aplica las migraciones y arranca Gunicorn
(`python manage.py migrate --noinput && gunicorn config.wsgi:application`), así que
no hace falta migrar a mano.

Direcciones:

| Qué | Dónde |
|-----|-------|
| Aplicación | `http://localhost` (o la IP de la máquina en la red local) |
| Consola de Keycloak | `http://localhost/admin/` |
| Admin de Django | `http://localhost/django-admin/` |

### Comandos de uso diario

```bash
docker compose ps                      # estado de los cinco servicios
docker compose exec web python manage.py collectstatic --noinput  # tras tocar el CSS
docker compose logs -f web             # logs de Django
docker compose logs -f keycloak        # logs de Keycloak
docker compose restart web             # reiniciar sólo la aplicación
docker compose exec web python manage.py test core       # pruebas
docker compose exec web python manage.py migrate         # migrar a mano
docker compose exec web python manage.py makemigrations  # tras cambiar models.py
docker compose exec web python manage.py createsuperuser # admin de Django sin Keycloak
docker compose exec web python manage.py shell           # consola de Django
```

### Al cambiar código

El `Dockerfile` **copia** el código dentro de la imagen y el servicio `web` no monta
ningún volumen, así que el contenedor no ve los cambios del disco hasta que se
reconstruye la imagen:

```bash
docker compose up -d --build web
```

Para iterar más rápido durante el desarrollo se puede copiar un archivo suelto al
contenedor y reiniciarlo, sin reconstruir:

```bash
docker compose cp backend/core/views.py web:/app/core/views.py
docker compose restart web
```

Si lo que cambió es el CSS o una tipografía, hay que volver a recolectar los estáticos
para que nginx los vea (reiniciar `web` ya lo hace, porque `collectstatic` está en su
comando de arranque).

### Reiniciar los datos desde cero

```bash
docker compose down -v        # ¡borra los volúmenes, se pierden todos los datos!
docker compose up -d --build
```

### Problemas frecuentes

| Síntoma | Causa habitual |
|---------|----------------|
| `Invalid token issuer` al iniciar sesión | Se entró por un host que Keycloak no reconoce. Agregarlo a `DJANGO_ALLOWED_HOSTS` y `DJANGO_CSRF_TRUSTED_ORIGINS`. |
| El contenedor `web` se reinicia en bucle | Casi siempre la base de datos. Revisar `docker compose logs web` y las variables `POSTGRES_*`. |
| 403 CSRF al enviar un formulario | Falta el origen en `DJANGO_CSRF_TRUSTED_ORIGINS`. |
| El login redirige a `localhost` desde otra máquina | `SITE_URL` y `KEYCLOAK_PUBLIC_URL` apuntan a `localhost`; el middleware lo corrige solo, pero `.env` debe permitir ese host. |
| Un cambio de código no se ve | Falta reconstruir la imagen (ver más arriba). |
| El sitio se ve sin estilos | nginx no está sirviendo `/static/`. Revisar que el contenedor `nginx` esté arriba y que `http://localhost/static/css/app.css` responda 200. |

---

## 10. Historial de sprints

### Sprint 1 — infraestructura e identidad

Repositorio y ramas, esqueleto de Docker Compose, proyecto Django, conexión a
PostgreSQL, realm y cliente de Keycloak, login por OIDC, alta y baja de usuarios,
proxy inverso Nginx y un frontend básico. 20 story points estimados.

### Sprint 2 — dominio y roles

Modelo y CRUD de divisas, lógica de consulta, roles y permisos (`admin`, `manager`,
`user`), tablero de gestión de usuarios contra el Admin API de Keycloak, y la
billetera con compra y retiro. Se cerraron además dos tareas arrastradas del Sprint 1
(realm de Keycloak y alta/baja de usuarios).

### Sprint 3 — el negocio de verdad (este sprint)

Los objetivos 1 a 6 son los planteados al abrir el sprint; el 7 y el 8 se agregaron
sobre la marcha, ya terminados los anteriores.

| # | Objetivo | Cómo se resolvió |
|---|----------|------------------|
| 1 | Cambiar la divisa base de dólar a guaraní | `BASE_CURRENCY_CODE = "PYG"`, renombrado de campos a `pyg_*` y migración `0004` que convierte los datos existentes. |
| 2 | Precio de compra y de venta separados, compra más baja | Campos `buy_rate` y `sell_rate` con validación en el formulario; la compra usa el precio de venta y la venta el de compra. |
| 3 | Tarjetas de crédito en la billetera, elegibles al operar | Modelo `PaymentMethod` con validación Luhn y almacenamiento de sólo los últimos 4 dígitos; selector de medio de pago en la carga de saldo y en la compra. |
| 4 | Pestaña de historial por divisa, en tabla y gráfico | Modelo `CurrencyRate`, vista pública `/history/` y gráfico SVG generado en el servidor. |
| 5 | Botón de administrador para adelantar un día (−5 % a +7 %) | `services/simulation.py` y el panel `/simulation/`, con `SimulationState` como reloj del sistema. |
| 6 | Unificar la documentación | Los cuatro documentos de `Documentacion/`; se eliminaron `PDO/`, `CHIA.txt`, `PLA.txt`, `PUN.txt` y `FUNC.txt`, cuyo contenido vigente quedó incorporado acá. |
| 7 | Vista previa de la conversión antes de confirmar la compra | Panel con las dos cifras y un gráfico de barras que compara lo pagado con el valor de reventa inmediata, calculado en el navegador a partir de la tabla de cotizaciones que publica la vista. |
| 8 | Rediseño de la interfaz: paleta morada y rosa, motivo hexagonal, logotipo de cohete, navegación en una barra lateral, botones más legibles y menos clics por acción | Hoja de estilos propia en `backend/static/css/app.css` con tipografías autoalojadas; ver la sección 7. |

Se agregó también la **venta de divisas** (`/trade/`, acción `sell`), que no estaba
pedida explícitamente pero hacía falta para que la distinción entre precio de compra
y de venta se pudiera ver de punta a punta: sin ella, el precio de compra nunca
movía dinero del usuario.

### Lecciones de la retrospectiva

Puntos a mejorar que el equipo identificó al cerrar los sprints anteriores y que
siguen vigentes:

- **Carga de trabajo concentrada.** De 13 tareas de los dos primeros sprints, 9
  quedaron en la misma persona. Es un riesgo de *bus factor* y explica buena parte
  de las tareas arrastradas.
- **Estimación incompleta.** Varias tareas entraron al sprint sin story points, lo
  que impide calcular una velocity real y comparar sprints.
- **Registro retroactivo en Jira.** Las tareas del Sprint 2 se crearon y cerraron el
  mismo día, al final del sprint. El tablero sirve así como foto final, pero no para
  detectar problemas mientras el sprint está en curso.
- **Identificadores reutilizados.** El código `FX-09` se usó para dos tareas
  distintas en sprints distintos, lo que complica buscar el histórico.

Acciones propuestas: repartir las tareas de backend entre los tres integrantes,
exigir story points como *definition of ready*, cargar el tiempo trabajado aunque sea
aproximado, mover las tarjetas durante el sprint y no reutilizar códigos `FX-XX`.

---

## 11. Deuda técnica conocida

- **El cobro con tarjeta es simulado.** No hay pasarela de pago: registrar una
  tarjeta y "cobrarla" sólo deja el movimiento anotado. Es lo adecuado para un
  proyecto de aprendizaje, pero es lo primero que habría que cambiar para algo real.
- **La simulación no tiene marcha atrás.** Se puede avanzar días, no volver. Para
  reiniciar el historial hay que borrar `CurrencyRate` y `SimulationState`
  (`docker compose down -v` deja todo como recién instalado).
- **Retiro y venta se parecen demasiado.** *Retirar* saca la divisa del sistema sin
  dar nada a cambio, *vender* la convierte en guaraníes. La interfaz lo explica,
  pero la distinción confunde a quien la ve por primera vez.
- **El movimiento diario es puro azar.** No hay tendencia, volatilidad por divisa ni
  correlación entre divisas: cada día cada divisa sortea su número por separado.
- **La interfaz no se pudo revisar en un navegador desde el entorno de desarrollo.**
  Las pruebas verifican que cada pantalla responda 200, que enlace la hoja de estilos y
  que dibuje la barra lateral, pero eso no es lo mismo que ver la página: en esta
  máquina no hay Chrome (la herramienta de automatización lo requiere) y las capturas
  con Firefox en modo headless se quedan colgadas. Colisiones de etiquetas, desbordes o
  problemas de contraste reales sólo los detecta alguien mirando.
- **Las pruebas no validan el HTML.** Verifican lo que hace el servidor, no que el
  marcado que produce sea correcto. Así pasó inadvertido un `id` duplicado entre los
  formularios de compra y venta hasta que hubo que escribir código que dependía de él.
- **Sin paginación.** La billetera muestra los últimos 20 movimientos y el historial
  el período elegido completo. Con muchos datos ambas vistas van a pesar.
- **El contenedor no monta el código.** Cada cambio exige reconstruir la imagen o
  copiar el archivo a mano. Un volumen de desarrollo en `docker-compose.yml`
  ahorraría ese paso.
