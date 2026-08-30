from django.contrib.auth import get_user_model
from mozilla_django_oidc.auth import OIDCAuthenticationBackend


class KeycloakOIDCAuthenticationBackend(OIDCAuthenticationBackend):
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
        user.save()
        return user
