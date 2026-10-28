from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0011_fete_libelle_evenement'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='opens_on',
            field=models.DateField(blank=True, null=True),
        ),
    ]
