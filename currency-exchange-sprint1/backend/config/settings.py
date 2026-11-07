from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "*").split(",")
    if host.strip()
]
USE_X_FORWARDED_HOST = os.getenv("DJANGO_USE_X_FORWARDED_HOST", "1") == "1"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = os.getenv("DJANGO_SECURE_SSL_REDIRECT", "0") == "1"
SESSION_COOKIE_SECURE = os.getenv("DJANGO_SESSION_COOKIE_SECURE", "0") == "1"
CSRF_COOKIE_SECURE = os.getenv("DJANGO_CSRF_COOKIE_SECURE", "0") == "1"
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "DJANGO_CSRF_TRUSTED_ORIGINS",
        "http://localhost,http://127.0.0.1,http://192.168.100.13",
    ).split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "core.apps.CoreConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "core.middleware.LocalhostAutoDetectMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.roles",
                "core.context_processors.unread_notifications",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "exchange_db"),
        "USER": os.getenv("POSTGRES_USER", "exchange_user"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "change-me"),
        "HOST": os.getenv("POSTGRES_HOST", "db"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es"
LANGUAGES = [("es", "Español")]
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
LOCALE_PATHS = [BASE_DIR / "locale"]

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
# El ambiente de desarrollo monta el código en /app y deja los estáticos fuera de él.
STATIC_ROOT = Path(os.getenv("DJANGO_STATIC_ROOT", BASE_DIR / "staticfiles"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SITE_URL = os.getenv("SITE_URL", "http://localhost").rstrip("/")
LOGIN_REDIRECT_URL = os.getenv("LOGIN_REDIRECT_URL", "/dashboard/")
LOGOUT_REDIRECT_URL = os.getenv("LOGOUT_REDIRECT_URL", "/")

KEYCLOAK_PUBLIC_URL = os.getenv("KEYCLOAK_PUBLIC_URL", "http://localhost").rstrip("/")
KEYCLOAK_INTERNAL_URL = os.getenv("KEYCLOAK_INTERNAL_URL", "http://keycloak:8080").rstrip("/")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "exchange-learning")
KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "exchange-web")
KEYCLOAK_CLIENT_SECRET = os.getenv("KEYCLOAK_CLIENT_SECRET", "change-me-web-secret")
KEYCLOAK_ADMIN_CLIENT_ID = os.getenv("KEYCLOAK_ADMIN_CLIENT_ID", "exchange-admin-api")
KEYCLOAK_ADMIN_CLIENT_SECRET = os.getenv("KEYCLOAK_ADMIN_CLIENT_SECRET", "change-me-admin-secret")

# Stripe (modo de prueba en desarrollo). Sin STRIPE_SECRET_KEY el medio de pago
# "Tarjeta (Stripe)" no se ofrece en la pantalla de pago.
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
# El guaraní es una moneda sin decimales en Stripe: el monto se envía en unidades enteras.
STRIPE_CURRENCY = os.getenv("STRIPE_CURRENCY", "pyg").lower()

# Facturación electrónica con Factura Segura (ambiente TEST por defecto). Sin
# FACTURA_SEGURA_TOKEN las facturas se emiten en modo simulado: se numeran y se
# muestran, pero no se envían a Factura Segura ni al SIFEN.
FACTURA_SEGURA_URL = os.getenv("FACTURA_SEGURA_URL", "https://apitest.facturasegura.com.py").rstrip("/")
FACTURA_SEGURA_TOKEN = os.getenv("FACTURA_SEGURA_TOKEN", "")
# Sólo los usa el comando ``factura_segura_token`` para obtener el token.
FACTURA_SEGURA_EMAIL = os.getenv("FACTURA_SEGURA_EMAIL", "")
FACTURA_SEGURA_PASSWORD = os.getenv("FACTURA_SEGURA_PASSWORD", "")
FACTURA_SEGURA_TIMEOUT = int(os.getenv("FACTURA_SEGURA_TIMEOUT", "30"))

# Datos del emisor y rango de numeración autorizado. Los valores por defecto son
# los que Factura Segura habilitó para el equipo 10 en TEST.
INVOICE_EMITTER = {
    "ruc": os.getenv("FACTURA_RUC", "2595733"),
    "dv": os.getenv("FACTURA_RUC_DV", "3"),
    "taxpayer_type": os.getenv("FACTURA_TIPO_CONTRIBUYENTE", "1"),  # 1 persona física, 2 jurídica
    "name": os.getenv("FACTURA_RAZON_SOCIAL", "Exchange Pro - Equipo 10 IS2"),
    "address": os.getenv("FACTURA_DIRECCION", "Campus Universitario, San Lorenzo"),
    "house_number": os.getenv("FACTURA_NUMERO_CASA", "0"),
    "department_code": os.getenv("FACTURA_DEPARTAMENTO", "1"),
    "department": os.getenv("FACTURA_DEPARTAMENTO_DESC", "CAPITAL"),
    "city_code": os.getenv("FACTURA_CIUDAD", "1"),
    "city": os.getenv("FACTURA_CIUDAD_DESC", "ASUNCION (DISTRITO)"),
    "phone": os.getenv("FACTURA_TELEFONO", "(021)000000"),
    "email": os.getenv("FACTURA_EMAIL", "facturacion@exchangepro.local"),
    "stamp": os.getenv("FACTURA_TIMBRADO", ""),
    "stamp_start": os.getenv("FACTURA_TIMBRADO_INICIO", ""),
    "establishment": os.getenv("FACTURA_ESTABLECIMIENTO", "001"),
    "expedition_point": os.getenv("FACTURA_PUNTO_EXPEDICION", "003"),
    "first_number": int(os.getenv("FACTURA_NUMERO_DESDE", "314")),
    "last_number": int(os.getenv("FACTURA_NUMERO_HASTA", "325")),
    "activities": [
        {"cActEco": "62010", "dDesActEco": "Actividades de programación informática"},
        {"cActEco": "74909", "dDesActEco": "Otras actividades profesionales, científicas y técnicas n.c.p."},
    ],
}

OIDC_RP_CLIENT_ID = KEYCLOAK_CLIENT_ID
OIDC_RP_CLIENT_SECRET = KEYCLOAK_CLIENT_SECRET
OIDC_RP_SIGN_ALGO = "RS256"
OIDC_RP_SCOPES = "openid email profile"
OIDC_CREATE_USER = True
OIDC_STORE_ID_TOKEN = True

# Endpoints the browser is redirected to must use the public URL.
OIDC_OP_AUTHORIZATION_ENDPOINT = f"{KEYCLOAK_PUBLIC_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/auth"
OIDC_OP_LOGOUT_ENDPOINT = f"{KEYCLOAK_PUBLIC_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/logout"

# Endpoints Django calls server-to-server must use the internal Docker-network URL,
# since KEYCLOAK_PUBLIC_URL (e.g. http://localhost) resolves to the web container itself.
OIDC_OP_TOKEN_ENDPOINT = f"{KEYCLOAK_INTERNAL_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"
OIDC_OP_USER_ENDPOINT = f"{KEYCLOAK_INTERNAL_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/userinfo"
OIDC_OP_JWKS_ENDPOINT = f"{KEYCLOAK_INTERNAL_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"

AUTHENTICATION_BACKENDS = [
    "core.backends.KeycloakOIDCAuthenticationBackend",
    "django.contrib.auth.backends.ModelBackend",
]

LOGIN_URL = "/oidc/authenticate/"
