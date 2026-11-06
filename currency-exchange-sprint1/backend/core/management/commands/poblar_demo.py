"""Puebla la base con datos de demostración simulando transacciones completas.

Las migraciones ya siembran el guaraní, las divisas de demostración y 30 días de
historial. Este comando agrega lo que sólo se obtiene operando:

1. los usuarios de demostración de Keycloak (``demo.admin``, ``demo.manager``,
   ``demo.customer``, ``demo.cajero``) con su rol, para que tengan datos antes
   de su primer inicio de sesión;
2. ``--dias`` días de simulación de cotizaciones (con sus avisos a los clientes);
3. una ronda completa de ``services.demo.simulate_transactions()``: compras con
   cada medio de pago, cancelación, vencimiento, venta, caja y facturas simuladas.

Es idempotente: si ya hay datos de demostración no hace nada, salvo con
``--force``. Así el script de despliegue lo puede correr siempre::

    python manage.py poblar_demo            # sólo si la base está vacía
    python manage.py poblar_demo --force    # otra ronda más
"""

import random

from django.core.management.base import BaseCommand, CommandError

from core.models import SimulationState
from core.services import checkout, cashier
from core.services.demo import ensure_demo_users, has_demo_activity, simulate_transactions
from core.services.notifications import notify_rate_change
from core.services.simulation import advance_days

SEED = 2026


class Command(BaseCommand):
    help = "Puebla la base con usuarios y transacciones de demostración."

    def add_arguments(self, parser):
        parser.add_argument("--dias", type=int, default=7, help="Días de cotizaciones a simular (0 para ninguno).")
        parser.add_argument("--force", action="store_true", help="Simular otra ronda aunque ya haya datos.")

    def handle(self, *args, **options):
        if has_demo_activity() and not options["force"]:
            self.stdout.write("Ya hay datos de demostración; no se agrega nada (usá --force para otra ronda).")
            return

        users = ensure_demo_users()
        self.stdout.write(f"Usuarios de demostración: {', '.join(users)}")

        days = options["dias"]
        if days > 0 and (options["force"] or SimulationState.load().days_advanced == 0):
            state, changes = advance_days(days)
            for change in changes:
                notify_rate_change(change.currency, change.previous_buy, change.previous_sell)
            self.stdout.write(f"Cotizaciones: {days} días simulados, hasta el {state.current_date:%d/%m/%Y}.")

        rng = random.Random(None if options["force"] else SEED)
        try:
            report = simulate_transactions(rng=rng)
        except (checkout.CheckoutError, cashier.CashierError) as exc:
            raise CommandError(f"No se pudo simular: {exc}") from exc

        for step in report.steps:
            mark = self.style.SUCCESS("✓") if step.ok else self.style.ERROR("✗")
            self.stdout.write(f"  {mark} [{step.area}] {step.action}: {step.outcome}")
        summary = f"{len(report.steps)} pasos, {report.invoices} facturas simuladas, {report.failures} con error."
        self.stdout.write(self.style.SUCCESS(summary) if not report.failures else self.style.WARNING(summary))
