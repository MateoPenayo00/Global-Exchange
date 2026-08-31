from django.conf import settings


class LocalhostAutoDetectMiddleware:
    """Derive the public host from the current request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        base_url = f"{request.scheme}://{request.get_host()}"
        realm = settings.KEYCLOAK_REALM

        settings.SITE_URL = base_url
        settings.KEYCLOAK_PUBLIC_URL = base_url
        settings.OIDC_OP_AUTHORIZATION_ENDPOINT = f"{base_url}/realms/{realm}/protocol/openid-connect/auth"
        settings.OIDC_OP_TOKEN_ENDPOINT = f"{base_url}/realms/{realm}/protocol/openid-connect/token"
        settings.OIDC_OP_USER_ENDPOINT = f"{base_url}/realms/{realm}/protocol/openid-connect/userinfo"
        settings.OIDC_OP_JWKS_ENDPOINT = f"{base_url}/realms/{realm}/protocol/openid-connect/certs"
        settings.OIDC_OP_LOGOUT_ENDPOINT = f"{base_url}/realms/{realm}/protocol/openid-connect/logout"

        return self.get_response(request)
