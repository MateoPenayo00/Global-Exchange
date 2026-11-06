import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_stripe_payments'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Notification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('previous_buy', models.DecimalField(decimal_places=6, max_digits=18)),
                ('previous_sell', models.DecimalField(decimal_places=6, max_digits=18)),
                ('new_buy', models.DecimalField(decimal_places=6, max_digits=18)),
                ('new_sell', models.DecimalField(decimal_places=6, max_digits=18)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('read_at', models.DateTimeField(blank=True, null=True)),
                (
                    'currency',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='notifications',
                        to='core.currency',
                    ),
                ),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='notifications',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'verbose_name': 'notificación',
                'verbose_name_plural': 'notificaciones',
                'ordering': ['-created_at', '-id'],
            },
        ),
    ]
