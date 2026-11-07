"""Obtiene el token de autenticación del usuario ESI de Factura Segura.

Uso::

    python manage.py factura_segura_token --email usuario@fpuna.edu.py
    # pide la contraseña sin mostrarla; o la toma de FACTURA_SEGURA_PASSWORD

El token vale hasta que se cambie la contraseña. Hay que copiarlo a
``FACTURA_SEGURA_TOKEN`` en ``.env`` y reiniciar el contenedor ``web``.
"""

import getpass

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.services.factura_segura import FacturaSeguraError, login


class Command(BaseCommand):
    help = "Pide a Factura Segura el token de autenticación del usuario ESI."

    def add_arguments(self, parser):
        parser.add_argument("--email", default=settings.FACTURA_SEGURA_EMAIL, help="Correo del usuario ESI.")
        parser.add_argument("--url", default=settings.FACTURA_SEGURA_URL, help="URL base del API (TEST por defecto).")

    def handle(self, *args, **options):
        email = options["email"] or input("Correo del usuario ESI: ").strip()
        password = settings.FACTURA_SEGURA_PASSWORD or getpass.getpass("Contraseña de Factura Segura: ")
        if not email or not password:
            raise CommandError("Hacen falta el correo y la contraseña del usuario ESI.")
        try:
            token = login(email, password, base_url=options["url"])
        except FacturaSeguraError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("Token obtenido. Agregá esta línea a .env y reiniciá el contenedor web:"))
        self.stdout.write(f"FACTURA_SEGURA_TOKEN={token}")
