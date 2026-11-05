"""Avisos a los clientes cuando cambia la cotización de una divisa.

* ``notify_rate_change()`` crea un aviso por cada cliente (rol ``user``) cuando la
  cotización de una divisa cambió de verdad. La llaman las dos vistas que mueven
  cotizaciones: la edición manual de una divisa y la simulación de días.
* ``mark_read()`` marca avisos como leídos, sólo los del usuario indicado.
"""

from django.contrib.auth import get_user_model
from django.utils import timezone

from .. import roles as role_lib
from ..models import Currency, Notification


def notify_rate_change(currency: Currency, previous_buy, previous_sell) -> int:
    """Avisa a los clientes del cambio de ``currency``. Devuelve cuántos avisos creó.

    ``currency`` trae las cotizaciones **nuevas**; ``previous_buy`` y
    ``previous_sell`` son las de antes. No avisa si la divisa es la base, está
    inactiva o si las cotizaciones quedaron igual.
    """
    if currency.is_base or not currency.is_active:
        return 0
    if (currency.buy_rate, currency.sell_rate) == (previous_buy, previous_sell):
        return 0

    clients = get_user_model().objects.filter(groups__name=role_lib.USER).distinct()
    created = Notification.objects.bulk_create(
        Notification(
            user=client,
            currency=currency,
            previous_buy=previous_buy,
            previous_sell=previous_sell,
            new_buy=currency.buy_rate,
            new_sell=currency.sell_rate,
        )
        for client in clients
    )
    return len(created)


def mark_read(user, notification_id=None) -> int:
    """Marca como leídos los avisos de ``user``: uno si se indica ``notification_id``, o todos."""
    unread = Notification.objects.filter(user=user, read_at__isnull=True)
    if notification_id:
        if not str(notification_id).isdigit():
            return 0  # un id que no es número no corresponde a ningún aviso
        unread = unread.filter(pk=notification_id)
    return unread.update(read_at=timezone.now())
