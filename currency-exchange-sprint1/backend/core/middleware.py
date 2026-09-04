from django.conf import settings


class LocalhostAutoDetectMiddleware:
    """Derive the public host from the current request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        base_url = f"{request.scheme}://{request.get_host()}"
        realm = settings.KEYCLOAK_REALM

        # Only the endpoints the browser is redirected to should follow the
        # host the request came in on. The token/userinfo/jwks endpoints are
        # called server-to-server by Django itself, so they must keep using
        # KEYCLOAK_INTERNAL_URL (e.g. http://keycloak:8080) — pointing them at
        # base_url (e.g. http://localhost) breaks them, since "localhost"
        # from inside the web container refers to the container itself.
        settings.SITE_URL = base_url
        settings.KEYCLOAK_PUBLIC_URL = base_url
        settings.OIDC_OP_AUTHORIZATION_ENDPOINT = f"{base_url}/realms/{realm}/protocol/openid-connect/auth"
        settings.OIDC_OP_LOGOUT_ENDPOINT = f"{base_url}/realms/{realm}/protocol/openid-connect/logout"

        return self.get_response(request)
