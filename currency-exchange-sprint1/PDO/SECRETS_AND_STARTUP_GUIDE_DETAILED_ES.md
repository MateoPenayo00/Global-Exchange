# Currency Exchange Sprint 1
## Secretos, configuración, explicación archivo por archivo y guía de arranque

Este documento combina dos necesidades:

1. Una explicación detallada de cómo se plantillan y configuran los secretos.
2. Una explicación archivo por archivo de la estructura del proyecto, con pasos prácticos de arranque y operación.

El objetivo es ayudar a desarrolladores junior a entender no solo **qué** cambiar, sino también **por qué** existe cada archivo, **cómo** se conectan los valores entre sí y **qué sucede** cuando arranca el stack.

---

# 1. Propósito del proyecto y modelo mental

Este proyecto es un entorno de aprendizaje para un negocio ficticio de cambio de divisas. El objetivo del Sprint 1 no es construir el producto comercial final. El objetivo es crear una plataforma pequeña pero realista que enseñe cómo encajan las piezas principales de una aplicación web moderna:

- **Django** maneja la aplicación web y las páginas renderizadas en el servidor.
- **Keycloak** maneja el login, el registro, el logout y la identidad de la cuenta.
- **PostgreSQL** almacena los datos de la aplicación y de identidad.
- **Nginx** actúa como el proxy inverso con el que habla primero el navegador.
- **Docker Compose** ejecuta todos los servicios juntos de forma repetible.

El proyecto está intencionalmente dividido en contenedores separados porque eso hace que cada responsabilidad sea más fácil de entender y depurar.

Un desarrollador junior debería pensar en el stack así:

- el navegador envía peticiones a Nginx,
- Nginx las reenvía a Django o a Keycloak,
- Django renderiza las páginas de cara al usuario,
- Keycloak gestiona la identidad,
- PostgreSQL persiste los datos,
- Docker mantiene el entorno reproducible.

Esa separación es un patrón muy común en sistemas del mundo real.

---

# 2. Por qué los secretos deben plantillarse

Un secreto es cualquier valor que no debería estar codificado directamente en el código fuente ni compartirse públicamente. En este proyecto, los valores secretos más importantes son:

- la clave secreta de Django
- las contraseñas de PostgreSQL
- la contraseña de administrador de Keycloak
- el secreto del cliente de Keycloak

Los secretos se plantillan para que el mismo código base pueda ser reutilizado por diferentes desarrolladores y entornos sin reescribir el código de la aplicación.

En lugar de escribir los secretos directamente en archivos Python o JSON, el proyecto usa variables de entorno. Eso significa que:

- el código lee los valores en tiempo de ejecución,
- los valores pueden cambiar sin cambiar el código,
- cada desarrollador puede tener su propio archivo `.env` local,
- el repositorio puede incluir un archivo `.env.example` seguro como guía.

Este es un patrón mucho más seguro y limpio que codificar credenciales directamente.

---

# 3. Los archivos de configuración principales y qué hace cada uno

## 3.1 `.env.example`

Este es el archivo plantilla. Muestra los nombres de las variables que espera el proyecto.

### Por qué existe
Un desarrollador nuevo puede copiar este archivo a `.env` y completar los valores. Funciona como una lista de verificación.

### Qué debe contener
- nombres de variables
- valores de ejemplo seguros
- comentarios breves describiendo las variables

### Qué **no** debe contener
- contraseñas reales
- secretos reales de producción
- claves de API privadas

### Idea de ejemplo
```text
DJANGO_SECRET_KEY=change-me
POSTGRES_PASSWORD=change-me
KEYCLOAK_ADMIN_PASSWORD=change-me
```

Estos son marcadores de posición (placeholders). Le indican al desarrollador exactamente qué debe reemplazar.

---

## 3.2 `.env`

Este es el archivo real de tiempo de ejecución usado por Docker Compose y Django.

### Por qué existe
Docker Compose lee `.env` automáticamente al arrancar. Eso facilita inyectar valores reales en los contenedores.

### Qué debe contener
- los valores secretos reales para esa máquina
- los nombres de realm/cliente elegidos
- las URLs y puertos locales

### Qué debería pasar con él
- mantenerlo local
- no subirlo al repositorio en un proyecto real
- no compartirlo casualmente

### Por qué importa
Si alguien cambia el archivo, la aplicación debería seguir funcionando porque el código lee las variables de entorno en lugar de valores codificados.

---

## 3.3 `docker-compose.yml`

Este archivo define los servicios del stack.

### Por qué existe
Permite al equipo arrancar todos los servicios con un solo comando en lugar de arrancar cada contenedor manualmente.

### Servicios típicamente definidos
- `db` para la base de datos PostgreSQL de Django
- `keycloak-db` para la base de datos PostgreSQL de Keycloak
- `keycloak` para el proveedor de identidad
- `web` para Django
- `nginx` para el enrutamiento del proxy inverso

### Por qué usa variables de entorno
El archivo de Compose no debería guardar secretos directamente. En su lugar, usa valores como:

```yaml
POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
KEYCLOAK_ADMIN_PASSWORD: ${KEYCLOAK_ADMIN_PASSWORD}
```

Esto significa que Docker Compose sustituye los valores desde `.env` en tiempo de ejecución.

### Por qué es útil
- el archivo de Compose se mantiene legible
- los secretos quedan fuera del código
- el mismo archivo de Compose puede reutilizarse en distintos entornos

---

## 3.4 `backend/Dockerfile`

Este archivo construye la imagen del contenedor de Django.

### Por qué existe
Docker necesita una receta para construir la imagen de la aplicación. El Dockerfile es esa receta.

### Qué hace
- parte de una imagen base de Python
- instala los paquetes de sistema necesarios para Python y PostgreSQL
- instala los requisitos de Python
- copia el código de Django al contenedor
- arranca Gunicorn

### Por qué no debe contener secretos
La imagen debería ser reutilizable. Si los secretos estuvieran incrustados en la imagen, cada reconstrucción arriesgaría exponerlos y la imagen quedaría atada a un único entorno.

### Qué recordar
El Dockerfile define el **entorno**, no los **valores secretos**.

---

## 3.5 `backend/requirements.txt`

Aquí se listan los paquetes de Python que necesita la aplicación.

### Por qué existe
Las dependencias de Python deben registrarse en un solo lugar para que cada desarrollador instale las mismas versiones.

### Paquetes típicos aquí
- Django
- Gunicorn
- psycopg2-binary
- python-dotenv
- mozilla-django-oidc

### Por qué importa
Si cada desarrollador usa versiones de paquetes ligeramente distintas, la aplicación puede comportarse de forma diferente en cada máquina.

---

## 3.6 `backend/config/settings.py`

Este es el archivo principal de configuración (settings) de Django.

### Por qué existe
Django lee este archivo para saber cómo configurarse a sí mismo.

### Qué debería leer del entorno
- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG`
- `DJANGO_ALLOWED_HOSTS`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `KEYCLOAK_SERVER_URL`
- `KEYCLOAK_REALM`
- `KEYCLOAK_CLIENT_ID`
- `KEYCLOAK_CLIENT_SECRET`
- URLs de redirección y de sitio

### Por qué importa cada valor
- **clave secreta**: firma cookies y protege los mecanismos internos de Django
- **debug**: habilita el modo de desarrollo; no debe quedar activo en producción
- **hosts permitidos**: evita ataques de cabecera Host y errores de configuración
- **configuración de la base de datos**: permite que Django se conecte a PostgreSQL
- **configuración de Keycloak**: le indica a Django dónde están los endpoints de login y de tokens
- **URLs de redirección**: aseguran que el usuario vuelva a la página correcta tras el login o el logout

### Por qué es importante este archivo
Este archivo es el puente central entre el código y el entorno.

---

## 3.7 `backend/config/urls.py`

Este archivo mapea las rutas de URL a las vistas.

### Por qué existe
Cuando el navegador pide `/dashboard/`, Django necesita saber qué código debe responder.

### Qué conecta
- página de inicio
- panel (dashboard)
- página de cuenta
- página de login
- página de logout
- página de registro
- página de eliminación de cuenta
- rutas de OIDC para la integración con Keycloak

### Por qué importa para el aprendizaje
Aquí es donde los junior empiezan a ver cómo las rutas se convierten en páginas reales.

---

## 3.8 `backend/core/views.py`

Este archivo contiene la lógica de la aplicación para las páginas.

### Por qué existe
Las vistas son el código que decide qué ve el navegador.

### Responsabilidades actuales
- mostrar la página de inicio
- mostrar el panel
- mostrar la página de cuenta
- redirigir el flujo de login a OIDC
- redirigir el flujo de registro al registro de Keycloak
- cerrar la sesión del usuario
- mostrar la página de confirmación de eliminación de cuenta

### Por qué se mantiene simple en el Sprint 1
El objetivo es probar primero el flujo de login e identidad. Lógica de negocio más avanzada puede llegar después.

---

## 3.9 `backend/core/templates/core/*.html`

Estas son las plantillas HTML de la interfaz de usuario.

### Por qué existen
Las plantillas de Django permiten renderizar páginas en el servidor sin necesidad de un framework de frontend aparte.

### Archivos incluidos
- `base.html`
- `home.html`
- `dashboard.html`
- `account.html`
- `delete_account.html`

### Qué hace cada una
- **base.html**: layout y estilos compartidos
- **home.html**: página pública de inicio
- **dashboard.html**: área de aterrizaje tras iniciar sesión
- **account.html**: detalles de cuenta y opción de eliminarla
- **delete_account.html**: página de confirmación del flujo de eliminación

### Por qué las plantillas de Django son una buena elección aquí
Son más fáciles de entender para los junior que una arquitectura de SPA separada.

---

## 3.10 `nginx/default.conf`

Este archivo configura el proxy inverso.

### Por qué existe
El navegador debería hablar con Nginx, no directamente con cada contenedor.

### Qué enruta
- peticiones generales a Django
- rutas relacionadas con OIDC a Django
- rutas de Keycloak a Keycloak

### Por qué importa
Oculta la disposición interna de los contenedores al usuario y hace más limpia la experiencia en el navegador.

---

## 3.11 `keycloak/realm-export.json`

Este archivo inicializa (siembra) el realm de Keycloak.

### Por qué existe
Le da al equipo una configuración de identidad repetible.

### Qué puede contener
- nombre del realm
- definición del cliente
- URIs de redirección
- definiciones de roles
- usuarios de ejemplo
- configuración de registro

### Por qué es útil
En lugar de crear todo manualmente desde cero cada vez, el proyecto puede partir de una base conocida.

### Nota importante
Usa marcadores de posición seguros para el aprendizaje. No guardes secretos reales en este archivo.

---

## 3.12 `postgres/init/01-create-databases.sql`

Este archivo es un script SQL de ejemplo para el arranque inicial.

### Por qué existe
Muestra cómo podrían crearse manualmente una base de datos y un usuario.

### Qué enseña
- cómo se crean las bases de datos
- cómo se otorgan privilegios a los usuarios
- cómo pueden separarse los datos de identidad y los datos de la aplicación

### Por qué puede ser una referencia más que configuración activa
La configuración de Compose ya puede manejar las bases de datos a través de variables de entorno del contenedor. Aun así, el archivo SQL es un artefacto de aprendizaje útil.

---

## 3.13 `README.md`

Este archivo explica cómo arrancar el proyecto y qué contiene el repositorio.

### Por qué existe
Ayuda a un desarrollador a empezar sin tener que adivinar la estructura.

---

## 3.14 `.gitignore`

Este archivo le indica a Git qué ignorar.

### Por qué existe
Mantiene los secretos locales y los archivos generados fuera del repositorio.

### Exclusiones típicas
- `.env`
- bytecode de Python
- cachés
- archivos locales del editor
- artefactos de compilación

---

## 3.15 `backend/.dockerignore`

Este archivo le indica a Docker qué no enviar a la construcción (build) de la imagen.

### Por qué existe
Hace que las construcciones de Docker sean más rápidas y evita copiar archivos locales innecesarios a la imagen.

### Exclusiones típicas
- `.env`
- `.git`
- cachés
- archivos generados

---

# 4. Qué secretos deben configurarse y por qué

Estos valores deben revisarse y cambiarse respecto a los marcadores de posición antes de un uso serio.

## 4.1 `DJANGO_SECRET_KEY`

### Por qué importa
Django lo usa para la firma criptográfica de sesiones y cookies.

### Qué pasa si es débil o queda expuesto
- se debilita la seguridad de las sesiones
- la aplicación se vuelve más fácil de manipular

### Recomendación
Usa un valor aleatorio largo.

---

## 4.2 `POSTGRES_PASSWORD`

### Por qué importa
Esto protege al usuario de PostgreSQL de la base de datos de Django.

### Qué pasa si es débil
Cualquiera con acceso de red al contenedor de la base de datos podría potencialmente autenticarse con más facilidad.

### Recomendación
Usa una contraseña fuerte y única.

---

## 4.3 `KEYCLOAK_DB_PASSWORD`

### Por qué importa
Esto protege la base de datos detrás de Keycloak.

### Por qué Keycloak depende de ella
Keycloak almacena ahí usuarios, clientes, realms y datos relacionados con la identidad.

### Recomendación
Usa una contraseña única, distinta de la contraseña de la base de datos de Django.

---

## 4.4 `KEYCLOAK_ADMIN_PASSWORD`

### Por qué importa
Esta es la contraseña del usuario administrador de Keycloak.

### Qué pasa si se filtra
Alguien podría administrar todo el realm y su configuración de autenticación.

### Recomendación
Usa una contraseña fuerte y trátala como altamente sensible.

---

## 4.5 `KEYCLOAK_CLIENT_SECRET`

### Por qué importa
Django lo usa para autenticarse a sí mismo como cliente OIDC confidencial.

### Qué pasa si se filtra
La relación de confianza entre Django y Keycloak puede verse comprometida.

### Recomendación
Mantenlo privado y sincronizado entre Keycloak y Django.

---

## 4.6 Valores no secretos que igual deben coincidir

Estos valores no son secretos, pero deben ser correctos:

- `KEYCLOAK_REALM`
- `KEYCLOAK_CLIENT_ID`
- `DJANGO_ALLOWED_HOSTS`
- `SITE_URL`
- `LOGIN_REDIRECT_URL`
- `LOGOUT_REDIRECT_URL`

Si estos valores no coinciden con el stack en ejecución, los flujos de login y las redirecciones del navegador pueden fallar.

---

# 5. Pasos previos

Antes de arrancar el stack, realiza estos pasos en orden.

## Paso 1: instalar prerrequisitos
Asegúrate de que la máquina tenga:
- Docker Engine
- plugin de Docker Compose
- Git
- VSCode u otro editor de código

### Por qué importa
Sin estas herramientas, el desarrollador junior perderá tiempo depurando comandos faltantes en lugar de aprender el stack.

---

## Paso 2: copiar `.env.example` a `.env`
Crea un archivo de entorno local real.

### Por qué importa
El archivo de ejemplo es solo una plantilla. Los valores reales de tiempo de ejecución pertenecen a `.env`.

---

## Paso 3: establecer los valores secretos
Actualiza lo siguiente en `.env`:
- `DJANGO_SECRET_KEY`
- `POSTGRES_PASSWORD`
- `KEYCLOAK_DB_PASSWORD`
- `KEYCLOAK_ADMIN_PASSWORD`
- `KEYCLOAK_CLIENT_SECRET`

### Por qué importa
Si estos valores se dejan como marcadores de posición, el stack será inseguro o no funcionará correctamente.

---

## Paso 4: confirmar que los puertos están libres
El stack usa:
- puerto 80 para Nginx
- puerto 8080 para Keycloak
- puerto 5432 para PostgreSQL si se expone localmente

### Por qué importa
Si otro proceso ya usa uno de estos puertos, los contenedores pueden arrancar pero quedar inalcanzables.

---

## Paso 5: revisar la estructura de carpetas del proyecto
Las carpetas principales son:
- `backend/`
- `nginx/`
- `keycloak/`
- `postgres/`

### Por qué importa
Saber dónde vive cada archivo facilita mucho la depuración.

---

## Paso 6: asegurarse de que los secretos no se suban al repositorio
Verifica que `.gitignore` incluya `.env`.

### Por qué importa
Esto evita la exposición accidental de contraseñas y claves secretas.

---

# 6. Guía de arranque paso a paso

## Paso 1: abrir la carpeta del proyecto
Trabaja desde:

```bash
C:\Users\mpenayo\Documents\Agile Learning Project\currency-exchange-sprint1
```

## Paso 2: construir y arrancar el stack

```bash
docker compose up -d --build
```

### Qué hace esto
- construye la imagen de Django
- arranca las bases de datos
- arranca Keycloak
- arranca Django
- arranca Nginx

---

## Paso 3: confirmar que los servicios están corriendo

```bash
docker compose ps
```

### Qué buscar
- todos los servicios en estado saludable (healthy) o en ejecución
- sin reinicios inmediatos

---

## Paso 4: inspeccionar los logs si es necesario

```bash
docker compose logs -f web
docker compose logs -f keycloak
docker compose logs -f nginx
```

### Por qué importan los logs
Los logs muestran el mensaje de error exacto en lugar de tener que adivinar por qué falló un servicio.

---

## Paso 5: abrir el navegador
Visita:

- `http://localhost`
- `http://localhost:8080`

### Qué significan
- `http://localhost` pasa por Nginx hacia la aplicación
- `http://localhost:8080` abre Keycloak directamente

---

# 7. Cómo configurar todo una vez que el stack está corriendo

## 7.1 PostgreSQL

### Qué revisar
- el contenedor de la base de datos está saludable
- Django puede conectarse
- las migraciones se ejecutan

### Si algo falla
Revisa los valores en `.env`:
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_HOST`
- `POSTGRES_PORT`

### Por qué importa
Django depende de la base de datos antes de poder cargar las páginas normales de la aplicación.

---

## 7.2 Keycloak

Abre:

```text
http://localhost:8080
```

Inicia sesión usando:
- `KEYCLOAK_ADMIN`
- `KEYCLOAK_ADMIN_PASSWORD`

### Luego verifica
- el nombre del realm es `exchange-learning`
- el ID de cliente es `exchange-web`
- las URIs de redirección son correctas
- el registro está habilitado si se requiere para el Sprint 1
- los roles existen si el JSON los importó

### Por qué importa
Si la configuración del realm o del cliente no coincide con la de Django, el login fallará aunque Keycloak esté corriendo.

---

## 7.3 Django

### Qué revisar
- la página de inicio carga
- el login redirige a Keycloak
- el logout vuelve a la página de inicio
- el panel (dashboard) está protegido

### Comando útil de migración manual

```bash
docker compose run --rm web python manage.py migrate
```

### Por qué importa
Django no funcionará correctamente si sus tablas de base de datos no están inicializadas.

---

## 7.4 Nginx

### Qué revisar
- el navegador puede acceder a la aplicación a través del puerto 80
- Nginx puede llegar a Django
- Nginx puede enrutar correctamente las peticiones relacionadas con autenticación

### Por qué importa
Nginx es la puerta exterior de la aplicación para el navegador.

---

## 7.5 Páginas del frontend

Verifica que estas páginas funcionen:
- `/`
- `/login/`
- `/register/`
- `/dashboard/`
- `/account/`
- `/delete-account/`

### Por qué importa
El recorrido del usuario debe ser visible desde la página pública de inicio hasta la gestión de cuenta autenticada.

---

# 8. Lista de verificación recomendada después del arranque

Una vez que el stack está corriendo, haz lo siguiente:

1. abre la página de inicio
2. verifica que aparece la marca del negocio de cambio de divisas
3. abre la consola de administración de Keycloak
4. verifica que el realm se importó correctamente
5. revisa la configuración del cliente
6. prueba el login desde el navegador
7. prueba el logout
8. prueba la página de cuenta después de iniciar sesión
9. prueba la página de eliminación de cuenta
10. revisa los logs en busca de errores
11. confirma que los datos persisten en los volúmenes de Docker

### Por qué importa esta lista de verificación
Le da al equipo una secuencia de validación repetible y evita pruebas al azar.

---

# 9. Flujo de trabajo de desarrollo después del arranque

Un buen flujo de trabajo para los junior es:

1. cambiar un archivo
2. reiniciar el contenedor correspondiente si hace falta
3. inspeccionar los logs
4. probar un flujo específico
5. confirmar (commit) en una rama de funcionalidad
6. abrir un pull request

### Por qué funciona este enfoque
Mantiene la depuración simple y ayuda al equipo a ver qué cambio causó qué efecto.

---

# 10. Errores comunes y cómo evitarlos

## Error 1: codificar secretos directamente en archivos Python
Evítalo leyendo los valores desde el entorno.

## Error 2: subir `.env` al repositorio
Evítalo manteniendo `.env` fuera de Git.

## Error 3: cambiar demasiados archivos a la vez
Evítalo haciendo cambios pequeños y focalizados.

## Error 4: olvidar alinear la configuración de Keycloak y Django
Evítalo verificando los nombres de realm, los IDs de cliente y las URIs de redirección.

## Error 5: ignorar los logs
Evítalo leyendo los logs antes de cambiar código a ciegas.

---

# 11. Breve explicación de la arquitectura, repetida con contexto

Este stack de aprendizaje está construido de la forma en que se construyen muchos sistemas reales:

- Django es la capa de aplicación,
- Keycloak es la capa de identidad,
- PostgreSQL es la capa de persistencia,
- Nginx es el punto de entrada del tráfico,
- Docker Compose conecta los servicios entre sí.

Por eso los archivos están separados de esta manera. Cada archivo existe para hacer que una parte del sistema sea comprensible y mantenible.

---

# 12. Orden final recomendado para el equipo

Si los junior quieren la secuencia menos confusa, deberían hacer el trabajo en este orden:

1. preparar `.env`
2. arrancar PostgreSQL
3. arrancar Keycloak
4. arrancar Django
5. añadir el enrutamiento de Nginx
6. probar login/logout
7. mejorar el frontend
8. luego ampliar el comportamiento de gestión de cuentas

### Por qué este orden es el mejor
Sigue el orden de dependencias y reduce la cantidad de depuración simultánea.

---

# 13. Recordatorio final

La regla más importante para este sprint es:

**Mantén los secretos fuera del código, mantén la configuración en variables de entorno, y haz que cada archivo sea responsable de una sola tarea.**

Ese único hábito hará que el proyecto sea mucho más fácil de entender y mucho más seguro de mantener.
