from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_cash_card_details'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseorder',
            name='stripe_session_id',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='purchaseorder',
            name='stripe_payment_intent',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AlterField(
            model_name='purchaseorder',
            name='payment_kind',
            field=models.CharField(
                blank=True,
                choices=[
                    ('balance', 'Saldo en guaraníes'),
                    ('currency', 'Otra divisa de la billetera'),
                    ('card', 'Tarjeta de crédito'),
                    ('qr', 'Código QR'),
                    ('stripe', 'Tarjeta (Stripe)'),
                ],
                max_length=10,
            ),
        ),
        migrations.CreateModel(
            name='StripeEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_id', models.CharField(max_length=120, unique=True)),
                ('event_type', models.CharField(max_length=80)),
                ('outcome', models.CharField(blank=True, max_length=40)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'order',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='stripe_events',
                        to='core.purchaseorder',
                    ),
                ),
            ],
            options={
                'verbose_name': 'evento de Stripe',
                'verbose_name_plural': 'eventos de Stripe',
                'ordering': ['-created_at'],
            },
        ),
    ]
