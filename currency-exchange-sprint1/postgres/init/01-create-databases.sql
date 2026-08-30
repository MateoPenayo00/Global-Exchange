CREATE DATABASE keycloak_db;
CREATE USER keycloak_user WITH PASSWORD 'keycloak_password';
GRANT ALL PRIVILEGES ON DATABASE keycloak_db TO keycloak_user;
