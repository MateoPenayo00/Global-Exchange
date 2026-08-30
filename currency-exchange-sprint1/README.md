# Currency Exchange Sprint 1

This folder contains the scaffold for the Sprint 1 learning project.

## Start the stack

1. Copy `.env.example` to `.env`.
2. Review the secrets.
3. Start the containers:

```bash
docker compose up -d --build
```

## Services

- Django app: proxied through Nginx on port 80
- Keycloak: http://localhost:8080
- PostgreSQL: internal service, exposed on 5432 for local debugging

## Notes

- `backend/` contains the Django app.
- `nginx/default.conf` contains the reverse proxy rules.
- `keycloak/realm-export.json` seeds the realm and client.
- `postgres/init/01-create-databases.sql` is a reference init script for database setup.
