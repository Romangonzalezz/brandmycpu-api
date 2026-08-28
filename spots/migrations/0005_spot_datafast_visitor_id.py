from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('spots', '0004_spot_x_handle')]

    operations = [
        migrations.AddField(
            model_name='spot',
            name='datafast_visitor_id',
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
