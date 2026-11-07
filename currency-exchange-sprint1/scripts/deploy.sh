#!/usr/bin/env bash
# Despliegue automático de Exchange Pro: código, base de datos y población de datos.
#
#   ./scripts/deploy.sh dev  [opciones]   ambiente de desarrollo (código montado, recarga sola)
#   ./scripts/deploy.sh prod [opciones]   ambiente de producción (código en la imagen, sin DEBUG)
#
# Opciones:
#   --pull       actualiza el código (git pull --ff-only) antes de desplegar
#   --reset      borra los contenedores y los volúmenes (¡TODOS los datos!) y empieza de cero
#   --seed       carga datos de demostración (por defecto sólo en dev)
#   --no-seed    no carga datos de demostración
#   --test       corre las pruebas unitarias al final
#   --insecure   permite desplegar prod con las contraseñas de ejemplo (sólo para una demo local)
#   -y, --yes    no pide confirmación
#   -h, --help   muestra esta ayuda
#
# Pasos: verificar requisitos → preparar .env → construir imágenes → levantar los
# servicios → esperar a la app y a Keycloak → migrar la base y verificar que no
# quede nada pendiente → poblar datos → (pruebas) → resumen.
#
# En Windows se corre desde Git Bash o WSL, con Docker Desktop abierto.

set -euo pipefail

# --- Utilidades -----------------------------------------------------------

if [ -t 1 ]; then
  BOLD=$'\e[1m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; RED=$'\e[31m'; RESET=$'\e[0m'
else
  BOLD=""; GREEN=""; YELLOW=""; RED=""; RESET=""
fi
step() { printf '\n%s==> %s%s\n' "$BOLD" "$*" "$RESET"; }
ok()   { printf '%s  ✓ %s%s\n' "$GREEN" "$*" "$RESET"; }
warn() { printf '%s  ! %s%s\n' "$YELLOW" "$*" "$RESET"; }
die()  { printf '%s  ✗ %s%s\n' "$RED" "$*" "$RESET" >&2; exit 1; }
usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

# --- Argumentos -----------------------------------------------------------

ENVIRONMENT=""
PULL=0; RESET_DATA=0; SEED=""; RUN_TESTS=0; INSECURE=0; ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    dev|prod)    ENVIRONMENT="$arg" ;;
    --pull)      PULL=1 ;;
    --reset)     RESET_DATA=1 ;;
    --seed)      SEED=1 ;;
    --no-seed)   SEED=0 ;;
    --test)      RUN_TESTS=1 ;;
    --insecure)  INSECURE=1 ;;
    -y|--yes)    ASSUME_YES=1 ;;
    -h|--help)   usage 0 ;;
    *)           printf 'Opción desconocida: %s\n\n' "$arg" >&2; usage 1 ;;
  esac
done
[ -n "$ENVIRONMENT" ] || { printf 'Indicá el ambiente: dev o prod.\n\n' >&2; usage 1; }
# Datos de demostración: siempre en dev, en prod sólo si se piden.
[ -n "$SEED" ] || { [ "$ENVIRONMENT" = dev ] && SEED=1 || SEED=0; }

# Todo se ejecuta desde la carpeta del proyecto, se llame desde donde se llame.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
COMPOSE=(docker compose -f docker-compose.yml -f "docker-compose.$ENVIRONMENT.yml")
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(basename "$PROJECT_DIR" | tr '[:upper:]' '[:lower:]')}"
BASE_URL="${DEPLOY_URL:-http://localhost}"

confirm() {
  [ "$ASSUME_YES" = 1 ] && return 0
  read -r -p "$1 [s/N] " answer
  [[ "$answer" =~ ^[sSyY]$ ]]
}

env_value() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true; }

set_env_value() {
  # Reemplaza (o agrega) KEY=valor en .env sin imprimir el valor.
  local key="$1" value="$2"
  if grep -qE "^$key=" .env; then
    local escaped; escaped="$(printf '%s' "$value" | sed 's/[&|\\]/\\&/g')"
    sed -i.bak "s|^$key=.*|$key=$escaped|" .env && rm -f .env.bak
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

random_secret() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32
  else LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 64; fi
}

wait_for() {
  # wait_for <descripción> <segundos> <comando...>
  local what="$1" timeout="$2"; shift 2
  local start=$SECONDS
  printf '  … esperando %s' "$what"
  until "$@" >/dev/null 2>&1; do
    if (( SECONDS - start >= timeout )); then
      printf '\n'; return 1
    fi
    printf '.'; sleep 3
  done
  printf ' (%ss)\n' "$(( SECONDS - start ))"
}

http_ok() { curl -fsS -o /dev/null --max-time 5 "$1"; }

# --- 1. Requisitos --------------------------------------------------------

step "Ambiente: $ENVIRONMENT  ·  proyecto: $PROJECT_NAME  ·  $PROJECT_DIR"
step "1/8 Verificando requisitos"
command -v docker >/dev/null 2>&1 || die "Falta Docker. Instalá Docker Engine o Docker Desktop."
docker info >/dev/null 2>&1 || die "Docker no responde. ¿Está iniciado (o tu usuario está en el grupo docker)?"
docker compose version >/dev/null 2>&1 || die "Falta el plugin Docker Compose v2 ('docker compose')."
command -v curl >/dev/null 2>&1 || die "Falta curl (se usa para verificar que el sitio responda)."
[ -f "docker-compose.$ENVIRONMENT.yml" ] || die "No existe docker-compose.$ENVIRONMENT.yml."
ok "Docker $(docker version --format '{{.Server.Version}}'), $(docker compose version --short 2>/dev/null || echo 'Compose v2')"

# --- 2. Código ------------------------------------------------------------

if [ "$PULL" = 1 ]; then
  step "2/8 Actualizando el código"
  git -C "$PROJECT_DIR" pull --ff-only || die "git pull falló (¿cambios locales sin commitear?)."
  ok "Código en $(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD) @ $(git -C "$PROJECT_DIR" rev-parse --short HEAD)"
else
  step "2/8 Código: se usa el que está en el disco (--pull para actualizarlo)"
  git -C "$PROJECT_DIR" rev-parse --short HEAD >/dev/null 2>&1 \
    && ok "$(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD) @ $(git -C "$PROJECT_DIR" rev-parse --short HEAD)"
fi

# --- 3. Configuración (.env) ----------------------------------------------

step "3/8 Preparando la configuración"
if [ "$RESET_DATA" = 1 ]; then
  confirm "Se van a BORRAR todos los datos (bases de la app y de Keycloak). ¿Seguir?" || die "Cancelado."
  "${COMPOSE[@]}" down -v --remove-orphans
  ok "Contenedores y volúmenes eliminados"
fi

fresh_install=1
docker volume inspect "${PROJECT_NAME}_postgres_data" >/dev/null 2>&1 && fresh_install=0

if [ ! -f .env ]; then
  cp .env.example .env
  ok "Se creó .env a partir de .env.example"
  if [ "$ENVIRONMENT" = prod ] && [ "$fresh_install" = 1 ]; then
    # Las contraseñas de las bases sólo se pueden elegir antes de crear sus volúmenes.
    for key in DJANGO_SECRET_KEY POSTGRES_PASSWORD KEYCLOAK_DB_PASSWORD KEYCLOAK_ADMIN_PASSWORD; do
      set_env_value "$key" "$(random_secret)"
    done
    ok "Se generaron DJANGO_SECRET_KEY, POSTGRES_PASSWORD, KEYCLOAK_DB_PASSWORD y KEYCLOAK_ADMIN_PASSWORD"
    warn "La contraseña de la consola de Keycloak quedó en .env (KEYCLOAK_ADMIN_PASSWORD)."
  fi
else
  ok "Se usa el .env existente"
fi

if [ "$ENVIRONMENT" = prod ]; then
  weak=()
  for key in DJANGO_SECRET_KEY POSTGRES_PASSWORD KEYCLOAK_DB_PASSWORD KEYCLOAK_ADMIN_PASSWORD; do
    value="$(env_value "$key")"
    { [ -z "$value" ] || [[ "$value" == change-me* ]]; } && weak+=("$key")
  done
  if [ "${#weak[@]}" -gt 0 ]; then
    if [ "$INSECURE" = 1 ]; then
      warn "Producción con valores de ejemplo en: ${weak[*]} (--insecure). Sólo para una demo local."
    else
      die "Producción con valores de ejemplo en: ${weak[*]}. Cambialos en .env (ver Documentacion/SECRETOS_Y_CONFIGURACION.md) o usá --insecure para una demo local."
    fi
  fi
  [[ "$(env_value KEYCLOAK_CLIENT_SECRET)" == change-me* ]] \
    && warn "KEYCLOAK_CLIENT_SECRET es el de ejemplo: rotalo en Keycloak y en .env antes de exponer el sitio."
fi

if [ -z "$(env_value FACTURA_SEGURA_TOKEN)" ]; then
  warn "Sin FACTURA_SEGURA_TOKEN: las facturas se emiten en modo simulado."
fi
if [ -z "$(env_value STRIPE_SECRET_KEY)" ]; then
  warn "Sin STRIPE_SECRET_KEY: el pago con Stripe no se ofrece."
fi

# --- 4. Imágenes ----------------------------------------------------------

step "4/8 Construyendo las imágenes"
"${COMPOSE[@]}" build web
ok "Imagen de la aplicación lista"

# --- 5. Servicios ---------------------------------------------------------

step "5/8 Levantando los servicios"
"${COMPOSE[@]}" up -d --remove-orphans
ok "Servicios iniciados"

# --- 6. Esperar a que todo responda -----------------------------------------

step "6/8 Esperando a que la aplicación y Keycloak respondan"
wait_for "a la aplicación en $BASE_URL" 240 http_ok "$BASE_URL/" \
  || { "${COMPOSE[@]}" logs --tail=40 web; die "La aplicación no respondió. Logs de web arriba."; }
ok "Aplicación en línea"
REALM="$(env_value KEYCLOAK_REALM)"; REALM="${REALM:-exchange-learning}"
wait_for "a Keycloak (realm $REALM)" 300 http_ok "$BASE_URL/realms/$REALM/.well-known/openid-configuration" \
  || { "${COMPOSE[@]}" logs --tail=40 keycloak; die "Keycloak no respondió. Logs de keycloak arriba."; }
ok "Keycloak en línea"

# --- 7. Base de datos -----------------------------------------------------

step "7/8 Base de datos: migraciones y datos"
# El contenedor web ya migra al arrancar; se repite (es idempotente) para verificarlo acá.
"${COMPOSE[@]}" exec -T web python manage.py migrate --noinput
"${COMPOSE[@]}" exec -T web python manage.py migrate --check >/dev/null \
  || die "Quedaron migraciones sin aplicar."
ok "Esquema al día ($( "${COMPOSE[@]}" exec -T web python manage.py showmigrations core | grep -c '\[X\]') migraciones de core aplicadas)"
ok "Datos base: guaraní, divisas de demostración e historial (los siembran las migraciones)"
if [ "$SEED" = 1 ]; then
  "${COMPOSE[@]}" exec -T web python manage.py poblar_demo
  ok "Datos de demostración cargados"
else
  ok "Sin datos de demostración (--seed para cargarlos)"
fi

# --- 8. Pruebas y resumen ---------------------------------------------------

if [ "$RUN_TESTS" = 1 ]; then
  step "8/8 Pruebas unitarias"
  "${COMPOSE[@]}" exec -T web python manage.py test core || die "Hay pruebas que fallan."
  ok "Pruebas en verde"
else
  step "8/8 Pruebas: omitidas (--test para correrlas)"
fi

step "Listo: Exchange Pro desplegado en $ENVIRONMENT"
"${COMPOSE[@]}" ps --format 'table {{.Service}}\t{{.Status}}'
cat <<EOF

  Aplicación           $BASE_URL
  Consola de Keycloak  $BASE_URL/admin/
  Admin de Django      $BASE_URL/django-admin/
  Usuarios de prueba   demo.admin, demo.manager, demo.customer, demo.cajero (ver MANUAL_DE_USUARIO.md)

  Logs:     ${COMPOSE[*]} logs -f web
  Detener:  ${COMPOSE[*]} down
EOF
