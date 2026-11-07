"""Consulta en el SIFEN el estado de las facturas que siguen en proceso.

La pantalla de una factura ya se actualiza sola al abrirla; este comando sirve
para ponerlas al día todas juntas (por ejemplo, desde un cron)::

    python manage.py actualizar_facturas
"""

from django.core.management.base import BaseCommand

from core.models import Invoice
from core.services import invoicing


class Command(BaseCommand):
    help = "Actualiza el estado en el SIFEN de las facturas en proceso."

    def handle(self, *args, **options):
        pending = Invoice.objects.filter(status=Invoice.PROCESSING, is_simulated=False).exclude(cdc="")
        updated = failed = 0
        for invoice in pending:
            try:
                invoicing.refresh_status(invoice)
            except invoicing.InvoicingError as exc:
                failed += 1
                self.stderr.write(f"{invoice.full_number}: {exc}")
            else:
                updated += 1
                self.stdout.write(f"{invoice.full_number}: {invoice.sifen_status}")
        self.stdout.write(self.style.SUCCESS(f"Consultadas {updated} facturas; {failed} con error."))
