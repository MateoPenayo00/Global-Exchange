from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings


class KeycloakAdminError(RuntimeError):
    pass


@dataclass(frozen=True)
class KeycloakUserRecord:
    id: str
    username: str
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None


class KeycloakAdminClient:
    def __init__(self) -> None:
        self.base_url = settings.KEYCLOAK_INTERNAL_URL.rstrip("/")
        self.realm = settings.KEYCLOAK_REALM
        self.client_id = settings.KEYCLOAK_ADMIN_CLIENT_ID
        self.client_secret = settings.KEYCLOAK_ADMIN_CLIENT_SECRET
        self.session = requests.Session()

    def _token_url(self) -> str:
        return f"{self.base_url}/realms/{self.realm}/protocol/openid-connect/token"

    def _users_url(self) -> str:
        return f"{self.base_url}/admin/realms/{self.realm}/users"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._admin_access_token()}"}

    def _admin_access_token(self) -> str:
        response = self.session.post(
            self._token_url(),
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=10,
        )
        if response.status_code >= 400:
            raise KeycloakAdminError(
                f"Could not get a Keycloak admin token: {response.status_code} {response.text.strip()}"
            )
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise KeycloakAdminError("Keycloak admin token response did not contain an access token.")
        return token

    def _search_users(self, **query: str) -> list[dict[str, Any]]:
        params = {k: v for k, v in query.items() if v}
        params["exact"] = "true"
        response = self.session.get(self._users_url(), params=params, headers=self._headers(), timeout=10)
        if response.status_code >= 400:
            raise KeycloakAdminError(
                f"Could not search Keycloak users: {response.status_code} {response.text.strip()}"
            )
        data = response.json()
        if not isinstance(data, list):
            raise KeycloakAdminError("Unexpected Keycloak search response.")
        return data

    def find_user(self, username: str | None = None, email: str | None = None) -> dict[str, Any] | None:
        for query in (dict(username=username), dict(email=email)):
            if not any(query.values()):
                continue
            results = self._search_users(**query)
            if results:
                return results[0]
        return None

    def delete_user_by_id(self, user_id: str) -> None:
        response = self.session.delete(
            f"{self._users_url()}/{user_id}",
            headers=self._headers(),
            timeout=10,
        )
        if response.status_code not in (204, 404):
            raise KeycloakAdminError(
                f"Could not delete Keycloak user: {response.status_code} {response.text.strip()}"
            )

    def delete_django_user_account(self, django_user) -> dict[str, Any]:
        candidates = []
        if getattr(django_user, "email", None):
            candidates.append(django_user.email)
        if getattr(django_user, "username", None):
            candidates.append(django_user.username)
        if hasattr(django_user, "get_username"):
            candidates.append(django_user.get_username())

        seen: set[str] = set()
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            record = self.find_user(username=candidate, email=candidate)
            if record:
                self.delete_user_by_id(record["id"])
                return record

        raise KeycloakAdminError(
            "The corresponding Keycloak account could not be found. Check the username and email mapping in Keycloak."
        )
