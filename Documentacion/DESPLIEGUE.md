# Despliegue automático

`currency-exchange-sprint1/scripts/deploy.sh` despliega el proyecto completo en el
ambiente de desarrollo o en el de producción con un solo comando: código, base de
datos (esquema y datos iniciales) y la población de datos de demostración.

```bash
cd currency-exchange-sprint1
./scripts/deploy.sh dev           # desarrollo
./scripts/deploy.sh prod          # producción
```

En Windows se corre desde **Git Bash** o **WSL**, con Docker Desktop abierto.

---

## 1. Los dos ambientes

Los dos usan el mismo `docker-compose.yml` (los cinco servicios: `db`, `keycloak-db`,
`keycloak`, `web`, `nginx`) más un archivo que ajusta lo propio de cada ambiente:

| | Desarrollo (`docker-compose.dev.yml`) | Producción (`docker-compose.prod.yml`) |
|---|---|---|
| Código | **Montado** desde `backend/`: gunicorn se recarga solo al guardar un archivo. | **Dentro de la imagen**: lo que se despliega es lo que se construyó. |
| Django | `DEBUG=1` | `DEBUG=0` |
| Gunicorn | 1 worker con `--reload` | 3 workers, `--timeout 60`, log de accesos |
| Keycloak | `start-dev` | `start` (modo producción), HTTP detrás de nginx |
| Reinicio | `unless-stopped` | `always` (vuelve solo si se reinicia la máquina) |
| Datos de demostración | Sí, por defecto | Sólo con `--seed` |
| Contraseñas de ejemplo | Se aceptan | **Se rechazan**, salvo `--insecure` |

Sin el script se puede levantar cada ambiente a mano:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

`docker compose up` a secas sigue funcionando como siempre (equivale a desarrollo
sin el código montado).

## 2. Qué hace el script

| Paso | Qué hace |
|------|----------|
| 1. Requisitos | Verifica Docker (y que responda), Docker Compose v2 y `curl`. |
| 2. Código | Con `--pull`, `git pull --ff-only`. Si no, usa el código del disco y muestra rama y commit. |
| 3. Configuración | Con `--reset`, borra contenedores **y volúmenes**. Si falta `.env` lo crea desde `.env.example`; en producción con una instalación nueva genera además `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`, `KEYCLOAK_DB_PASSWORD` y `KEYCLOAK_ADMIN_PASSWORD` aleatorias. En producción se niega a seguir si alguno de esos cuatro valores es el de ejemplo. Avisa si faltan el token de Factura Segura o la clave de Stripe. |
| 4. Imágenes | `docker compose build web`. |
| 5. Servicios | `docker compose up -d --remove-orphans`. |
| 6. Espera | Hasta que `http://localhost/` responda (4 min como máximo) y hasta que Keycloak publique la configuración OpenID del realm (5 min). Si algo no responde, muestra sus últimos logs y termina con error. |
| 7. Base de datos | Corre `migrate` (el contenedor ya migra al arrancar; repetirlo es inocuo), comprueba con `migrate --check` que no quede nada pendiente y, si corresponde, `poblar_demo`. |
| 8. Pruebas | Con `--test`, la suite completa dentro del contenedor. |

Termina con el estado de los servicios y las direcciones de la aplicación.

### Opciones

| Opción | Efecto |
|--------|--------|
| `--pull` | Actualiza el código antes de desplegar. |
| `--reset` | Borra **todos los datos** (pide confirmación) y despliega desde cero. |
| `--seed` / `--no-seed` | Fuerza o evita los datos de demostración. |
| `--test` | Corre las pruebas al final. |
| `--insecure` | Permite producción con contraseñas de ejemplo. Sólo para una demo en la propia máquina. |
| `-y`, `--yes` | No pide confirmación (para automatizar). |

La variable `DEPLOY_URL` cambia la dirección que se usa para verificar que el sitio
responda (por defecto `http://localhost`).

## 3. Población de la base de datos

La base se puebla en dos capas:

1. **Datos base — las migraciones.** `0004_seed_guarani_and_history` siembra el
   guaraní como divisa base, USD, EUR, BRL y ARS, 30 días de historial y el reloj
   simulado. Se aplican siempre, en los dos ambientes.
2. **Datos de demostración — `python manage.py poblar_demo`.** Crea los usuarios de
   demostración de Keycloak (`demo.admin`, `demo.manager`, `demo.customer`,
   `demo.cajero`) con su rol, simula 7 días de cotizaciones (con los avisos a los
   clientes) y ejecuta una **simulación completa de transacciones**: compras con cada
   medio de pago (incluido Stripe, confirmado por el manejador del webhook), una
   compra cancelada, otra vencida, una venta, una sesión de caja de punta a punta y
   las facturas (simuladas, para no gastar el rango real de Factura Segura).

`poblar_demo` es idempotente: si ya hay datos de demostración no hace nada, así que el
script lo puede correr en cada despliegue. Para otra ronda: `poblar_demo --force`.

Los usuarios de Keycloak y sus contraseñas vienen de `keycloak/realm-export.json`, que
Keycloak importa la primera vez que arranca con la base vacía.

## 4. Casos habituales

```bash
# Primera vez en una máquina nueva (desarrollo)
./scripts/deploy.sh dev

# Traer lo último de la rama y redesplegar, con pruebas
./scripts/deploy.sh dev --pull --test

# Empezar de cero (borra todo)
./scripts/deploy.sh dev --reset

# Producción con datos de demostración para la presentación
./scripts/deploy.sh prod --seed
```

## 5. Antes de exponer producción

El script exige contraseñas propias para las bases, Django y la consola de Keycloak,
pero hay dos cosas que no puede hacer solo:

- **Los secretos de los clientes de Keycloak** (`KEYCLOAK_CLIENT_SECRET`,
  `KEYCLOAK_ADMIN_CLIENT_SECRET`) tienen que coincidir con los de
  `realm-export.json`. Para cambiarlos hay que rotarlos en la consola de Keycloak y en
  `.env` a la vez (ver `SECRETOS_Y_CONFIGURACION.md`). El script avisa si siguen siendo
  los de ejemplo.
- **El host público**: agregarlo a `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`
  y a las *Redirect URIs* del cliente `exchange-web`.

Con HTTPS delante (un proxy o un balanceador), poner además
`DJANGO_SECURE_SSL_REDIRECT=1`, `DJANGO_SESSION_COOKIE_SECURE=1` y
`DJANGO_CSRF_COOKIE_SECURE=1`.

## 6. Problemas frecuentes

| Síntoma | Qué hacer |
|---------|-----------|
| `Docker no responde` | Iniciar Docker Desktop, o agregar el usuario al grupo `docker` en Linux. |
| `Producción con valores de ejemplo en: …` | Cambiar esos valores en `.env`. Si los volúmenes ya existen, las contraseñas de las bases no se pueden cambiar sólo en `.env`: o se cambian dentro de PostgreSQL, o se usa `--reset`. |
| `La aplicación no respondió` | El script muestra los logs de `web`. Lo más común: `.env` con datos de la base que no coinciden con los del volumen. |
| `Keycloak no respondió` | La primera vez en modo producción Keycloak compila su configuración y tarda más. Volver a correr el script. |
| El puerto 80 está ocupado | Otro servidor web en la máquina. Detenerlo, o cambiar el puerto de `nginx` en `docker-compose.yml` y usar `DEPLOY_URL=http://localhost:8080`. |
