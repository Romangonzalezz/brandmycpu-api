from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('spots', '0002_logo_filefield')]

    operations = [
        migrations.AddField(
            model_name='spot',
            name='website',
            field=models.URLField(blank=True, max_length=300),
        ),
    ]
