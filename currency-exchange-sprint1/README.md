# Currency Exchange Sprint 1

This project is a learning stack for a fictional currency exchange business.

## What it includes
- Django app
- Keycloak identity provider
- PostgreSQL databases
- Docker Compose orchestration

## Browser access via NPM
- Main app: https://global-exchange.mateopenayo.dev
- Keycloak: https://auth.global-exchange.mateopenayo.dev

## Backend ports for NPM forwarding
- Django app: 192.168.100.13:8000
- Keycloak: 192.168.100.13:8080

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
- The project is intended to sit behind Nginx Proxy Manager later, not a local nginx container.
- Use `docker compose logs --tail=200 web` if the app restarts.
