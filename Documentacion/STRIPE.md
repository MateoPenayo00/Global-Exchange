# Pagos con Stripe

Medio de pago "Tarjeta (Stripe)" de la pantalla de pago de una compra. Usa Stripe
Checkout en modo de prueba.

## Flujo

1. El usuario abre una compra y elige **Tarjeta (Stripe)**. `purchase_confirm` crea una
   sesión de Checkout (`stripe_payments.create_checkout_session`) por el monto en
   guaraníes de la orden y lo redirige a la página de Stripe.
2. El usuario paga en Stripe y vuelve a la pantalla de pago. **Volver no confirma nada.**
3. Stripe llama a `POST /stripe/webhook/` con el evento `checkout.session.completed`.
   Se verifica la firma y `checkout.settle_stripe_payment` acredita la divisa.

## Garantías

- **Firma:** sin una firma `Stripe-Signature` válida el webhook responde 400.
- **Idempotencia:** cada `event.id` se guarda en `StripeEvent`; un reenvío no acredita dos veces.
- **Pagos tardíos:** si la orden venció, se canceló o ya está pagada, el cobro se reembolsa.
- **Sin clave:** si falta `STRIPE_SECRET_KEY`, la opción no se muestra.

## Configuración (`.env`)

| Variable | Valor |
|----------|-------|
| `STRIPE_SECRET_KEY` | `sk_test_...` |
| `STRIPE_PUBLISHABLE_KEY` | `pk_test_...` |
| `STRIPE_WEBHOOK_SECRET` | `whsec_...` (lo imprime `stripe listen`) |
| `STRIPE_CURRENCY` | `pyg` (moneda sin decimales) |

Después de cambiar `.env`: `docker compose up -d`.

## Probar en local

```bash
stripe login
stripe listen --forward-to localhost/stripe/webhook/
```

Tarjeta de prueba: `4242 4242 4242 4242`, cualquier fecha futura y cualquier CVC.
Pago rechazado: `4000 0000 0000 0002`.

## Pruebas

```bash
docker compose exec web python manage.py test core.tests_stripe
```
