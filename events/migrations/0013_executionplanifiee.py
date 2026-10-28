from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0012_campaign_opens_on'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExecutionPlanifiee',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nom', models.CharField(max_length=80, unique=True)),
                ('derniere_execution', models.DateTimeField(blank=True, null=True)),
                ('dernier_resultat', models.CharField(blank=True, max_length=20)),
                ('dernier_message', models.TextField(blank=True)),
            ],
            options={
                'verbose_name': 'Tâche planifiée',
                'verbose_name_plural': 'Tâches planifiées',
            },
        ),
    ]
