# Currency Exchange Sprint 1

This project is a local learning stack for a fictional currency exchange business.

## What it includes
- Django app
- Keycloak identity provider
- PostgreSQL databases
- Nginx reverse proxy for localhost access

## Local browser access
- Main app: http://localhost
- Django admin console: http://localhost/django-admin/
- Keycloak admin console: http://localhost/admin/
- Keycloak realm endpoints: http://localhost/realms/

## Language
- The Django UI is configured in Spanish.
- Keycloak is configured to use Spanish as the default locale.

## Startup
1. Review `.env` if you want to customize secrets or hostnames.
2. Build and start containers:

```bash
docker compose up -d --build
```

## Main user flows
- Create account via Keycloak
- Log in via Keycloak
- Log out
- Delete account from the app

## Notes
- The Django app runs behind nginx.
- Keycloak is proxied through nginx on the same localhost host.
- The app and Keycloak are intended to stay local only.
- Use `docker compose logs --tail=200 web` if the app restarts.
- If you need to reset the databases, stop the stack and remove the Docker volumes.
