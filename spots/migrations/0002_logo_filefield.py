import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('spots', '0001_initial')]

    operations = [
        migrations.AlterField(
            model_name='spot',
            name='logo',
            field=models.FileField(
                blank=True,
                null=True,
                upload_to='logos/',
                validators=[
                    django.core.validators.FileExtensionValidator(
                        ['png', 'jpg', 'jpeg', 'webp', 'svg']
                    )
                ],
            ),
        ),
    ]
