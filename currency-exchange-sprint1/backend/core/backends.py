import requests
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from mozilla_django_oidc.auth import OIDCAuthenticationBackend
from requests.auth import HTTPBasicAuth

from core.roles import ADMIN, ALL_ROLES, roles_from_claims
from core.services.keycloak_admin import KeycloakAdminClient, KeycloakAdminError


class KeycloakOIDCAuthenticationBackend(OIDCAuthenticationBackend):
    def _issuer_host_header(self):
        """Force the Host header on server-to-server calls to Keycloak to match
        the host the browser used for the /auth redirect (self.request.get_host()).

        We call Keycloak directly on its internal Docker address
        (KEYCLOAK_INTERNAL_URL) for speed and to avoid depending on nginx, but
        Keycloak computes each token's "iss" claim from the Host header of the
        request that reached it — the /token and /userinfo calls must present
        the same Host the /auth step did, or Keycloak rejects the token with
        "Invalid token issuer" even though it's the same session.
        """
        request = getattr(self, "request", None)
        if request is None:
            return {}
        return {"Host": request.get_host()}

    def get_token(self, payload):
        auth = None
        if self.get_settings("OIDC_TOKEN_USE_BASIC_AUTH", False):
            user = payload.get("client_id")
            pw = payload.get("client_secret")
            auth = HTTPBasicAuth(user, pw)
            del payload["client_secret"]

        response = requests.post(
            self.OIDC_OP_TOKEN_ENDPOINT,
            data=payload,
            auth=auth,
            headers=self._issuer_host_header(),
            verify=self.get_settings("OIDC_VERIFY_SSL", True),
            timeout=self.get_settings("OIDC_TIMEOUT", None),
            proxies=self.get_settings("OIDC_PROXY", None),
        )
        self.raise_token_response_error(response)
        return response.json()

    def get_userinfo(self, access_token, id_token, payload):
        headers = self._issuer_host_header()
        headers["Authorization"] = f"Bearer {access_token}"

        user_response = requests.get(
            self.OIDC_OP_USER_ENDPOINT,
            headers=headers,
            verify=self.get_settings("OIDC_VERIFY_SSL", True),
            timeout=self.get_settings("OIDC_TIMEOUT", None),
            proxies=self.get_settings("OIDC_PROXY", None),
        )
        user_response.raise_for_status()
        return user_response.json()

    def get_username(self, claims):
        return (
            claims.get("preferred_username")
            or claims.get("email")
            or claims.get("sub")
            or super().get_username(claims)
        )

    def filter_users_by_claims(self, claims):
        User = get_user_model()
        username = self.get_username(claims)
        email = claims.get("email")

        users = User.objects.none()
        if username:
            users = User.objects.filter(username=username)
        if not users.exists() and email:
            users = User.objects.filter(email__iexact=email)
        return users

    def create_user(self, claims):
        user = super().create_user(claims)
        return self.update_user(user, claims)

    def update_user(self, user, claims):
        user.username = self.get_username(claims)
        user.email = claims.get("email", "") or ""
        user.first_name = claims.get("given_name", "") or ""
        user.last_name = claims.get("family_name", "") or ""

        roles = roles_from_claims(claims)

        # A self-registered account arrives without any Exchange Pro role. Give it
        # the default "user" role in Keycloak so it can trade, then reflect it here.
        if not roles:
            try:
                client = KeycloakAdminClient()
                client.ensure_default_role(username=user.username, email=user.email)
                roles = client.get_user_roles(
                    client.find_user(username=user.username, email=user.email)["id"]
                )
            except (KeycloakAdminError, KeyError, TypeError):
                roles = set()

        user.is_staff = ADMIN in roles
        user.is_superuser = ADMIN in roles
        user.save()
        self._sync_groups(user, roles)
        return user

    @staticmethod
    def _sync_groups(user, roles):
        for name in ALL_ROLES:
            group, _ = Group.objects.get_or_create(name=name)
            if name in roles:
                user.groups.add(group)
            else:
                user.groups.remove(group)
