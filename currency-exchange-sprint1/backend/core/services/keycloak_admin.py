from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings

from core.roles import ALL_ROLES


class KeycloakAdminError(RuntimeError):
    pass


@dataclass(frozen=True)
class KeycloakUserRecord:
    id: str
    username: str
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    enabled: bool = True
    roles: tuple[str, ...] = ()


class KeycloakAdminClient:
    def __init__(self) -> None:
        self.base_url = settings.KEYCLOAK_INTERNAL_URL.rstrip("/")
        self.realm = settings.KEYCLOAK_REALM
        self.client_id = settings.KEYCLOAK_ADMIN_CLIENT_ID
        self.client_secret = settings.KEYCLOAK_ADMIN_CLIENT_SECRET
        self.session = requests.Session()
        self._token: str | None = None

    # -- low level helpers -------------------------------------------------

    def _token_url(self) -> str:
        return f"{self.base_url}/realms/{self.realm}/protocol/openid-connect/token"

    def _admin_url(self, path: str = "") -> str:
        return f"{self.base_url}/admin/realms/{self.realm}{path}"

    def _users_url(self) -> str:
        return self._admin_url("/users")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._admin_access_token()}"}

    def _admin_access_token(self) -> str:
        if self._token:
            return self._token
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
        token = response.json().get("access_token")
        if not token:
            raise KeycloakAdminError("Keycloak admin token response did not contain an access token.")
        self._token = token
        return token

    def _request(self, method: str, url: str, *, ok: tuple[int, ...] = (200,), **kwargs: Any) -> requests.Response:
        response = self.session.request(method, url, headers=self._headers(), timeout=10, **kwargs)
        if response.status_code not in ok:
            raise KeycloakAdminError(
                f"Keycloak {method} {url} failed: {response.status_code} {response.text.strip()}"
            )
        return response

    # -- searching / reading users --------------------------------------

    def _search_users(self, **query: str) -> list[dict[str, Any]]:
        params = {k: v for k, v in query.items() if v}
        params["exact"] = "true"
        data = self._request("GET", self._users_url(), params=params).json()
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

    def get_user(self, user_id: str) -> dict[str, Any]:
        return self._request("GET", f"{self._users_url()}/{user_id}").json()

    def list_users(self, search: str | None = None) -> list[KeycloakUserRecord]:
        params: dict[str, str] = {"max": "200"}
        if search:
            params["search"] = search
        raw = self._request("GET", self._users_url(), params=params).json()
        records = []
        for entry in raw:
            roles = tuple(
                r["name"]
                for r in self._user_realm_roles(entry["id"])
                if r["name"] in ALL_ROLES
            )
            records.append(
                KeycloakUserRecord(
                    id=entry["id"],
                    username=entry.get("username", ""),
                    email=entry.get("email"),
                    first_name=entry.get("firstName"),
                    last_name=entry.get("lastName"),
                    enabled=entry.get("enabled", True),
                    roles=roles,
                )
            )
        records.sort(key=lambda r: r.username.lower())
        return records

    # -- realm roles ----------------------------------------------------

    def _realm_role(self, name: str) -> dict[str, Any]:
        return self._request("GET", self._admin_url(f"/roles/{name}")).json()

    def _user_realm_roles(self, user_id: str) -> list[dict[str, Any]]:
        return self._request(
            "GET", f"{self._users_url()}/{user_id}/role-mappings/realm"
        ).json()

    def get_user_roles(self, user_id: str) -> set[str]:
        return {r["name"] for r in self._user_realm_roles(user_id) if r["name"] in ALL_ROLES}

    def set_user_roles(self, user_id: str, roles: set[str]) -> None:
        """Make the user's Exchange Pro realm roles exactly ``roles``."""
        wanted = {r for r in roles if r in ALL_ROLES}
        current = self.get_user_roles(user_id)

        to_add = [self._realm_role(name) for name in wanted - current]
        to_remove = [self._realm_role(name) for name in current - wanted]

        url = f"{self._users_url()}/{user_id}/role-mappings/realm"
        if to_remove:
            self._request("DELETE", url, json=to_remove, ok=(204,))
        if to_add:
            self._request("POST", url, json=to_add, ok=(204,))

    # -- creating / deleting users ------------------------------------

    def create_user(
        self,
        *,
        username: str,
        email: str,
        password: str,
        first_name: str = "",
        last_name: str = "",
        roles: set[str] | None = None,
        temporary_password: bool = False,
    ) -> str:
        payload = {
            "username": username,
            "email": email,
            "firstName": first_name,
            "lastName": last_name,
            "enabled": True,
            "emailVerified": True,
            "credentials": [
                {"type": "password", "value": password, "temporary": temporary_password}
            ],
        }
        response = self._request("POST", self._users_url(), json=payload, ok=(201,))
        location = response.headers.get("Location", "")
        user_id = location.rstrip("/").rsplit("/", 1)[-1]
        if not user_id:
            created = self.find_user(username=username, email=email)
            if not created:
                raise KeycloakAdminError("Keycloak did not report the id of the new user.")
            user_id = created["id"]

        self.set_user_roles(user_id, roles or set())
        return user_id

    def set_password(self, user_id: str, password: str, *, temporary: bool = False) -> None:
        self._request(
            "PUT",
            f"{self._users_url()}/{user_id}/reset-password",
            json={"type": "password", "value": password, "temporary": temporary},
            ok=(204,),
        )

    def delete_user_by_id(self, user_id: str) -> None:
        response = self.session.delete(
            f"{self._users_url()}/{user_id}", headers=self._headers(), timeout=10
        )
        if response.status_code not in (204, 404):
            raise KeycloakAdminError(
                f"Could not delete Keycloak user: {response.status_code} {response.text.strip()}"
            )

    def ensure_default_role(self, *, username: str | None = None, email: str | None = None) -> None:
        """Give a freshly self-registered account the default ``user`` role if it has none."""
        from core.roles import DEFAULT_ROLE

        record = self.find_user(username=username, email=email)
        if not record:
            return
        if not self.get_user_roles(record["id"]):
            self.set_user_roles(record["id"], {DEFAULT_ROLE})

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
