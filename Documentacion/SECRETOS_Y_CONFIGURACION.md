# Exchange Pro — Secretos y configuración

Todo lo que el proyecto necesita como credencial o parámetro de entorno: qué es cada
valor, qué protege, qué pasa si se filtra y cómo cambiarlo.

Documentos hermanos: `DOCUMENTACION_TECNICA.md` (cómo está construido el sistema),
`MANUAL_DE_USUARIO.md` (cómo se usa el sitio), `DESPLIEGUE.md` (el script de
despliegue), `STRIPE.md` y `FACTURACION_ELECTRONICA.md` (los dos servicios externos) y
`COLABORACION_CON_IA.md` (cómo se usó asistencia de IA en el desarrollo).

---

## 1. Advertencia principal

**El proyecto viene con credenciales de ejemplo y está pensado para correr sólo en
la máquina del equipo.** Los valores por defecto (`change-me`, `ChangeMe123!`) son
marcadores de posición públicos. Antes de exponer el proyecto a cualquier red que no
sea la propia máquina hay que cambiarlos todos.

Reglas que el proyecto ya respeta y conviene no romper:

- **`.env` no se versiona.** Está en `.gitignore`. Es el único archivo con valores
  reales.
- **`.env.example` sí se versiona**, pero sólo con marcadores de posición. Nunca
  poner un secreto real ahí.
- **Ningún secreto está escrito en el código.** `settings.py` y `docker-compose.yml`
  leen variables de entorno; `keycloak/realm-export.json` usa valores de ejemplo.
- **El número completo de las tarjetas de crédito no se guarda.** Se valida y se
  descarta; sólo quedan los últimos cuatro dígitos, la marca y el vencimiento. Con
  Stripe el número ni siquiera pasa por la aplicación: se escribe en la página de
  Stripe.
- **Las credenciales de los servicios externos no se versionan.** El correo de
  habilitación de Factura Segura (`Factura Segura Credenciales.txt`) queda excluido
  por el `.gitignore` de la raíz; su token y las claves de Stripe van sólo en `.env`.

---

## 2. Cómo funciona la configuración

```
.env  ──►  docker compose  ──►  variables de entorno del contenedor  ──►  settings.py
```

Docker Compose lee `.env` automáticamente al arrancar e inyecta los valores en los
contenedores. `backend/config/settings.py` los lee con `os.getenv(...)`, siempre con
un valor por defecto, así que el proyecto arranca aunque falte una variable — pero
con las credenciales de ejemplo.

**Al cambiar cualquier valor de `.env` hay que recrear los contenedores**, porque las
variables de entorno se fijan al arrancar:

```bash
cd currency-exchange-sprint1
docker compose up -d
```

---

## 3. Puesta en marcha desde cero

```bash
cd currency-exchange-sprint1
cp .env.example .env
```

Después editar `.env` y reemplazar como mínimo estos cinco valores:

| Variable | Cómo generar un valor |
|----------|----------------------|
| `DJANGO_SECRET_KEY` | `openssl rand -hex 32` |
| `POSTGRES_PASSWORD` | `openssl rand -base64 24` |
| `KEYCLOAK_DB_PASSWORD` | `openssl rand -base64 24` (distinta de la anterior) |
| `KEYCLOAK_ADMIN_PASSWORD` | Una contraseña fuerte, tratada como la más sensible de todas. |
| `KEYCLOAK_CLIENT_SECRET` | `openssl rand -hex 32` |

El secreto del cliente tiene que coincidir en dos lugares: en `.env` y en la
configuración del cliente dentro de Keycloak (consola → realm `exchange-learning` →
*Clients* → `exchange-web` → *Credentials*). Si no coinciden, el login falla al
intercambiar el token. Lo mismo vale para `KEYCLOAK_ADMIN_CLIENT_SECRET` y el cliente
`exchange-admin-api`.

Luego:

```bash
docker compose up -d --build
```

O, más simple, `./scripts/deploy.sh prod`: si no existe `.env` lo crea y, en una
instalación nueva, genera solo `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`,
`KEYCLOAK_DB_PASSWORD` y `KEYCLOAK_ADMIN_PASSWORD`. En producción se niega a desplegar
si alguno de esos cuatro sigue con el valor de ejemplo (ver `DESPLIEGUE.md`). Los
secretos de los clientes de Keycloak no los puede generar: tienen que coincidir con
Keycloak.

---

## 4. Los secretos, uno por uno

### `DJANGO_SECRET_KEY`

**Qué protege.** Django la usa para firmar criptográficamente las sesiones, las
cookies, los tokens CSRF y los enlaces de un solo uso.

**Si se filtra.** Se pueden falsificar sesiones y suplantar a cualquier usuario de la
aplicación.

**Recomendación.** Valor aleatorio largo, distinto en cada entorno. Nunca reutilizar
el de desarrollo en otro lado.

### `POSTGRES_PASSWORD`

**Qué protege.** El usuario de PostgreSQL de la base de la aplicación (`exchange_db`):
divisas, cotizaciones, billeteras, tarjetas y movimientos.

**Si se filtra.** Cualquiera con acceso de red al contenedor de base de datos puede
leer y modificar todos los datos del negocio.

**Recomendación.** Contraseña fuerte y única.

### `KEYCLOAK_DB_PASSWORD`

**Qué protege.** La base de datos de Keycloak (`keycloak_db`), donde viven los
usuarios, los realms, los clientes y las credenciales.

**Si se filtra.** Es peor que la anterior: se compromete la identidad de todo el
sistema, no sólo los datos de negocio.

**Recomendación.** Contraseña fuerte, **distinta** de `POSTGRES_PASSWORD`. Son dos
instancias de PostgreSQL separadas justamente para que un problema en una no arrastre
a la otra.

### `KEYCLOAK_ADMIN_PASSWORD`

**Qué protege.** La cuenta de administrador de la consola de Keycloak
(`http://localhost/admin/`), usuario `admin`.

**Si se filtra.** Control total del proveedor de identidad: crear usuarios, cambiar
roles, leer la configuración de los clientes. Es el secreto más sensible del
proyecto.

**Recomendación.** La contraseña más fuerte de todas. En un entorno real, con
autenticación de dos factores.

### `KEYCLOAK_CLIENT_SECRET`

**Qué protege.** Es la credencial con la que Django se identifica ante Keycloak como
cliente OIDC confidencial (cliente `exchange-web`).

**Si se filtra.** Un atacante puede hacerse pasar por la aplicación ante Keycloak y
completar intercambios de tokens en su nombre.

**Recomendación.** Mantenerlo privado y sincronizado entre `.env` y Keycloak.

### `KEYCLOAK_ADMIN_CLIENT_SECRET`

**Qué protege.** La cuenta de servicio `exchange-admin-api`, que es la que usa la
pestaña *Usuarios* para crear, editar y eliminar cuentas a través del Admin API de
Keycloak. Esa cuenta tiene el rol `realm-management: realm-admin`.

**Si se filtra.** Permite administrar usuarios y roles del realm sin pasar por la
consola.

**Recomendación.** Tratarlo con el mismo cuidado que `KEYCLOAK_ADMIN_PASSWORD`, ya
que concede permisos equivalentes sobre los usuarios.

### `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY` y `STRIPE_WEBHOOK_SECRET`

**Qué protegen.** La clave secreta (`sk_test_…`) permite a la aplicación crear
sesiones de pago y reembolsos en la cuenta de Stripe del equipo. El secreto del
webhook (`whsec_…`) es el que prueba que un aviso de pago viene de Stripe: sin una
firma válida el webhook responde 400 y no acredita nada. La clave publicable no es
secreta.

**Si se filtran.** Con la clave secreta se puede operar sobre la cuenta de Stripe
(en modo de prueba no mueve dinero real, pero igual hay que rotarla). Con el secreto
del webhook se podrían fabricar avisos de pago y acreditar divisas sin cobrar.

**Cómo se obtienen.** Las claves en el *Dashboard* de Stripe → *Developers* → *API
keys* (siempre las de **modo de prueba**). El secreto del webhook lo imprime `stripe
listen --forward-to localhost/stripe/webhook/` en desarrollo, o el endpoint creado
en el *Dashboard* en un servidor. Ver `STRIPE.md`.

**Sin `STRIPE_SECRET_KEY`** el medio de pago *Tarjeta (Stripe)* simplemente no se
ofrece.

### `FACTURA_SEGURA_TOKEN` (y el usuario ESI)

**Qué protege.** Es el *Authentication-Token* del usuario ESI de Factura Segura: con
él la aplicación genera, consulta y cancela documentos electrónicos **en nombre del
emisor** habilitado (RUC 2595733 en TEST). Vale hasta que se cambie la contraseña del
usuario ESI.

**Si se filtra.** Cualquiera podría emitir o cancelar documentos a nombre del emisor
y gastar el rango de numeración del equipo (12 facturas en TEST). Se revoca cambiando
la contraseña en el portal de Factura Segura.

**Cómo se obtiene.**

```bash
docker compose exec web python manage.py factura_segura_token --email <correo ESI>
```

Pide la contraseña sin mostrarla (o la toma de `FACTURA_SEGURA_PASSWORD`, que conviene
**no** dejar en `.env` después) e imprime la línea para `.env`.

**Sin token** las facturas se emiten en modo simulado: no salen de la aplicación. El
resto de las variables `FACTURA_*` (RUC, establecimiento, punto de expedición, rango,
timbrado) no son secretas; están en `FACTURACION_ELECTRONICA.md`.

---

## 5. Contraseñas de las cuentas de demostración

No son secretos de configuración, pero son credenciales y conviene tenerlas
documentadas en un solo lugar.

| Dónde | Usuario | Contraseña | Para qué |
|-------|---------|-----------|----------|
| El sitio | `demo.admin` | `ChangeMe123!` | Cuenta de administrador. |
| El sitio | `demo.manager` | `ChangeMe123!` | Cuenta de gestor. |
| El sitio | `demo.customer` | `ChangeMe123!` | Cuenta de usuario. |
| El sitio | `demo.cajero` | `ChangeMe123!` | Cuenta de cajero (pestaña *Caja*). |
| Consola de Keycloak | `admin` | `change-me` | Administración de identidad. |

Las cuatro cuentas del sitio se crean al importar `keycloak/realm-export.json`. Para
cambiarlas: consola de Keycloak → realm `exchange-learning` → *Users* → el usuario →
*Credentials*. Para que el cambio quede en el repositorio hay que editar también el
realm export.

**Antes de cualquier demostración fuera de la máquina local, cambiar las cuatro.**

---

## 6. Variables que no son secretas pero tienen que coincidir

Si estos valores no corresponden al stack que está corriendo, el login y las
redirecciones del navegador fallan aunque todos los secretos estén bien.

| Variable | Valor por defecto | Para qué sirve |
|----------|------------------|----------------|
| `DJANGO_DEBUG` | `1` | Modo desarrollo. **En cualquier entorno compartido debe ser `0`**: con `1`, un error muestra la traza completa y parte de la configuración. |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,192.168.100.13,web,nginx,keycloak` | Hosts desde los que Django acepta peticiones. Agregar la IP de la máquina si se accede desde la red local. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `http://localhost,http://127.0.0.1,http://192.168.100.13` | Orígenes permitidos para enviar formularios. Si falta uno, los formularios devuelven 403. |
| `DJANGO_USE_X_FORWARDED_HOST` | `1` | Necesario porque Django está detrás de Nginx. |
| `DJANGO_SECURE_SSL_REDIRECT` | `0` | Forzar HTTPS. En `1` sólo si hay certificado. |
| `DJANGO_SESSION_COOKIE_SECURE` | `0` | Cookies de sesión sólo por HTTPS. Poner en `1` cuando haya HTTPS. |
| `DJANGO_CSRF_COOKIE_SECURE` | `0` | Igual, para la cookie CSRF. |
| `SITE_URL` | `http://192.168.100.13` | URL base del sitio. El middleware la ajusta sola en cada petición. |
| `LOGIN_REDIRECT_URL` | `/dashboard/` | Dónde cae el usuario después de iniciar sesión. |
| `LOGOUT_REDIRECT_URL` | `/` | Dónde cae después de cerrar sesión. |
| `POSTGRES_DB` / `POSTGRES_USER` | `exchange_db` / `exchange_user` | Base y usuario de la aplicación. |
| `POSTGRES_HOST` / `POSTGRES_PORT` | `db` / `5432` | `db` es el nombre del servicio en Docker Compose, no un host de red. |
| `KEYCLOAK_DB` / `KEYCLOAK_DB_USER` | `keycloak_db` / `keycloak_user` | Base y usuario de Keycloak. |
| `KEYCLOAK_ADMIN` | `admin` | Usuario administrador de la consola de Keycloak. |
| `KEYCLOAK_REALM` | `exchange-learning` | Nombre del realm. Tiene que coincidir con el del realm export. |
| `KEYCLOAK_CLIENT_ID` | `exchange-web` | Cliente OIDC de la aplicación. |
| `KEYCLOAK_ADMIN_CLIENT_ID` | `exchange-admin-api` | Cliente de la cuenta de servicio del Admin API. |
| `KEYCLOAK_PUBLIC_URL` | `http://192.168.100.13` | URL de Keycloak para el **navegador**. |
| `KEYCLOAK_INTERNAL_URL` | `http://keycloak:8080` | URL de Keycloak para las llamadas **de Django**. |
| `KEYCLOAK_HOSTNAME` | `192.168.100.13` | Host que Keycloak considera propio. |
| `STRIPE_CURRENCY` | `pyg` | Moneda de los cobros de Stripe. El guaraní no tiene decimales: el monto va en unidades enteras. |
| `FACTURA_SEGURA_URL` | `https://apitest.facturasegura.com.py` | API de Factura Segura. Producción: `https://api.facturasegura.com.py`. |
| `FACTURA_RUC` / `FACTURA_RUC_DV` | `2595733` / `3` | Emisor habilitado para el equipo en TEST. |
| `FACTURA_ESTABLECIMIENTO` / `FACTURA_PUNTO_EXPEDICION` | `001` / `003` | Parte fija del número de factura. |
| `FACTURA_NUMERO_DESDE` / `FACTURA_NUMERO_HASTA` | `314` / `325` | Rango de numeración autorizado. Al agotarse, la emisión se rechaza. |
| `FACTURA_TIMBRADO` / `FACTURA_TIMBRADO_INICIO` | vacíos | Sólo si Factura Segura los exige. |

### Por qué hay dos URLs de Keycloak

No es un descuido. `KEYCLOAK_PUBLIC_URL` es la que se le da al navegador para los
redirects de login y logout. `KEYCLOAK_INTERNAL_URL` es la que usa Django para
pedirle el token y los datos del usuario, servidor a servidor: desde dentro del
contenedor `web`, `localhost` apunta al propio contenedor, así que una sola URL no
puede servir para ambas cosas. El detalle completo está en la sección 2 de
`DOCUMENTACION_TECNICA.md`.

---

## 7. Archivos involucrados

| Archivo | Contiene | ¿Se versiona? |
|---------|----------|---------------|
| `.env` | Los valores reales de esta máquina. | **No** (está en `.gitignore`). |
| `.env.example` | La plantilla, con marcadores de posición. | Sí. |
| `docker-compose.yml` | Referencias `${VARIABLE:-valor-por-defecto}`, nunca secretos literales. | Sí. |
| `backend/config/settings.py` | Lecturas con `os.getenv()`, nunca secretos literales. | Sí. |
| `keycloak/realm-export.json` | Realm, clientes, roles y usuarios de demo, con secretos de ejemplo. | Sí. |
| `backend/Dockerfile` | Sólo el entorno de ejecución. Ningún secreto queda dentro de la imagen. | Sí. |
| `backend/.dockerignore` | Evita que `.env` y `.git` se copien a la imagen. | Sí. |
| `docker-compose.dev.yml` / `docker-compose.prod.yml` | Ajustes de cada ambiente; ningún secreto. | Sí. |
| `Factura Segura Credenciales.txt` (raíz) | El correo de habilitación de Factura Segura. | **No** (`.gitignore` de la raíz). |

---

## 8. Lista de verificación antes de exponer el proyecto

Si el proyecto va a dejar de correr sólo en `localhost`:

- [ ] Los cinco secretos de la sección 3 cambiados por valores aleatorios.
- [ ] `KEYCLOAK_ADMIN_CLIENT_SECRET` cambiado también.
- [ ] Las contraseñas de `demo.admin`, `demo.manager`, `demo.customer`, `demo.cajero` y del `admin`
      de Keycloak cambiadas.
- [ ] `DJANGO_DEBUG=0`.
- [ ] `DJANGO_ALLOWED_HOSTS` y `DJANGO_CSRF_TRUSTED_ORIGINS` con los hosts reales y
      sin comodines.
- [ ] HTTPS configurado y `DJANGO_SECURE_SSL_REDIRECT`,
      `DJANGO_SESSION_COOKIE_SECURE` y `DJANGO_CSRF_COOKIE_SECURE` en `1`.
- [ ] `.env` fuera del repositorio (`git status` no debe mencionarlo).
- [ ] Claves de Stripe y token de Factura Segura sólo en `.env`; `FACTURA_SEGURA_PASSWORD`
      vacío una vez obtenido el token.
- [ ] `./scripts/deploy.sh prod` corre sin `--insecure`.
- [ ] Los puertos de PostgreSQL no publicados al exterior (hoy no lo están: sólo
      Nginx expone el puerto 80).

---

## 9. Problemas frecuentes relacionados con la configuración

| Síntoma | Qué revisar |
|---------|-------------|
| `Invalid token issuer` al iniciar sesión | El host usado para entrar no está en `DJANGO_ALLOWED_HOSTS`. Agregarlo y recrear los contenedores. |
| 403 CSRF al enviar un formulario | Falta el origen en `DJANGO_CSRF_TRUSTED_ORIGINS`. |
| `invalid_client` en el login | `KEYCLOAK_CLIENT_SECRET` no coincide con el secreto del cliente `exchange-web` en Keycloak. |
| La pestaña *Usuarios* da error de Keycloak | `KEYCLOAK_ADMIN_CLIENT_SECRET` desincronizado, o la cuenta de servicio perdió el rol `realm-admin`. |
| El contenedor `web` se reinicia en bucle | Casi siempre las variables `POSTGRES_*`. Revisar con `docker compose logs web`. |
| Cambié `.env` y no pasó nada | Faltó `docker compose up -d` para recrear los contenedores. |
| El webhook de Stripe responde 400 | `STRIPE_WEBHOOK_SECRET` no corresponde al `stripe listen` (o al endpoint) que está enviando los avisos. |
| El webhook de Stripe responde 503 | Falta `STRIPE_SECRET_KEY` o `STRIPE_WEBHOOK_SECRET`. |
| Las facturas salen "simuladas" | Falta `FACTURA_SEGURA_TOKEN`, o se cambió `.env` sin recrear el contenedor. |
| "Factura Segura rechazó el token" | El token se invalidó (se cambió la contraseña del usuario ESI). Volver a correr `factura_segura_token`. |
