from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('spots', '0003_spot_website')]

    operations = [
        migrations.AddField(
            model_name='spot',
            name='x_handle',
            field=models.CharField(blank=True, max_length=15),
        ),
    ]
