# Currency Exchange Sprint 1

This project is a learning stack for a fictional currency exchange business.

## What it includes
- Django app
- Keycloak identity provider
- PostgreSQL databases
- Nginx reverse proxy
- Docker Compose orchestration

## Browser access
- Main app: http://192.168.100.13
- Keycloak: http://192.168.100.13:8080

## Basic startup
1. Review `.env`
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
- `exchange-web` is the Django application container.
- `exchange-admin-api` is a Keycloak confidential client used for account deletion.
- Use `docker compose logs --tail=200 web` if the app restarts.
