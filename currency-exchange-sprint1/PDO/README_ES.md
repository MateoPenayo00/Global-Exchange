# Currency Exchange Sprint 1

Este proyecto es un entorno de aprendizaje local para un negocio ficticio de cambio de divisas.

## Qué incluye
- Aplicación Django
- Proveedor de identidad Keycloak
- Bases de datos PostgreSQL
- Proxy inverso Nginx para acceso por localhost e IP de LAN

## Acceso desde el navegador (local)
- Aplicación principal: http://localhost
- Aplicación principal por IP de LAN: http://192.168.100.13
- Consola de administración de Django: http://localhost/django-admin/
- Consola de administración de Keycloak: http://localhost/admin/

## Comportamiento de host
- La aplicación Django acepta automáticamente localhost y la IP de LAN.
- La aplicación también deriva el host actual a partir de la petición, de modo que se adapta sin configuración adicional.
- Keycloak está configurado para permitir las mismas variaciones de host locales a través del mismo proxy.

## Idioma
- La interfaz de Django está configurada en español.
- Keycloak está configurado para usar español como idioma por defecto.

## Arranque
1. Revisa `.env` si quieres personalizar secretos o nombres de host.
2. Compila e inicia los contenedores:

```bash
docker compose up -d --build
```

## Flujos principales de usuario
- Crear cuenta a través de Keycloak
- Iniciar sesión a través de Keycloak
- Cerrar sesión
- Eliminar la cuenta desde la aplicación

## Notas
- La aplicación Django corre detrás de nginx.
- Keycloak se expone a través de nginx en la misma máquina.
- La aplicación y Keycloak están pensados para permanecer únicamente en local.
- Usa `docker compose logs --tail=200 web` si la aplicación se reinicia.
- Si necesitas reiniciar las bases de datos, detén el stack y elimina los volúmenes de Docker.

---

**Nota (Sprint 2):** desde este README original en inglés, el proyecto sumó roles
(`admin`, `manager`, `user`), un tablero de gestión de usuarios para administradores,
un CRUD de divisas y una billetera con compra/retiro de divisas. El detalle de estas
funciones está en `PDO/core-README_ES.md`, y el resumen de la sesión de trabajo está
en `CHIA.txt` (raíz del proyecto).
