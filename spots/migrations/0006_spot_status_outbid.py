from django.db import migrations, models


class Migration(migrations.Migration):
    """Agrega el estado `outbid` a Spot.status.

    Sólo cambia `choices`, así que en Postgres no toca la columna: es un
    CharField sin constraint. Va igual para que `makemigrations --check` no
    quede desalineado con el modelo.
    """

    dependencies = [('spots', '0005_spot_datafast_visitor_id')]

    operations = [
        migrations.AlterField(
            model_name='spot',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('confirmed', 'Confirmed'),
                    ('placed', 'Placed'),
                    ('outbid', 'Outbid'),
                ],
                default='pending',
                max_length=15,
            ),
        ),
    ]
