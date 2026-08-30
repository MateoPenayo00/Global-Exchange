from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"

ALLOWED_HOSTS = [host.strip() for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
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
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "exchange_password"),
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

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SITE_URL = os.getenv("SITE_URL", "http://localhost")
LOGIN_REDIRECT_URL = os.getenv("LOGIN_REDIRECT_URL", "/dashboard/")
LOGOUT_REDIRECT_URL = os.getenv("LOGOUT_REDIRECT_URL", "/")

OIDC_RP_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "exchange-web")
OIDC_RP_CLIENT_SECRET = os.getenv("KEYCLOAK_CLIENT_SECRET", "change-me")
OIDC_OP_SERVER_URL = os.getenv("KEYCLOAK_SERVER_URL", "http://keycloak:8080/").rstrip("/")
OIDC_REALM = os.getenv("KEYCLOAK_REALM", "exchange-learning")
OIDC_OP_AUTHORIZATION_ENDPOINT = f"{OIDC_OP_SERVER_URL}/realms/{OIDC_REALM}/protocol/openid-connect/auth"
OIDC_OP_TOKEN_ENDPOINT = f"{OIDC_OP_SERVER_URL}/realms/{OIDC_REALM}/protocol/openid-connect/token"
OIDC_OP_USER_ENDPOINT = f"{OIDC_OP_SERVER_URL}/realms/{OIDC_REALM}/protocol/openid-connect/userinfo"
OIDC_OP_JWKS_ENDPOINT = f"{OIDC_OP_SERVER_URL}/realms/{OIDC_REALM}/protocol/openid-connect/certs"
OIDC_OP_LOGOUT_ENDPOINT = f"{OIDC_OP_SERVER_URL}/realms/{OIDC_REALM}/protocol/openid-connect/logout"
OIDC_RP_SIGN_ALGO = "RS256"
OIDC_CREATE_USER = True
OIDC_AUTH_REQUEST_EXTRA_PARAMS = {"scope": "openid email profile"}

AUTHENTICATION_BACKENDS = [
    "mozilla_django_oidc.auth.OIDCAuthenticationBackend",
    "django.contrib.auth.backends.ModelBackend",
]

LOGIN_URL = "/oidc/authenticate/"
