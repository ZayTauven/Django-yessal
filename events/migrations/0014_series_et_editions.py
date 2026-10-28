from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0013_executionplanifiee'),
    ]

    operations = [
        migrations.AlterField(
            model_name='fete',
            name='recurrence',
            field=models.CharField(
                choices=[
                    ('annual', 'Annuelle'), ('annual_hijri', 'Annuelle (calendrier hégirien)'),
                    ('quarterly', 'Trimestrielle'), ('weekly', 'Hebdomadaire'), ('none', 'Non récurrente'),
                ],
                default='annual', max_length=20,
            ),
        ),
        migrations.AddField(model_name='fete', name='hijri_mois',
                            field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='fete', name='hijri_jour',
                            field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='fete', name='auto_editions',
                            field=models.BooleanField(default=False)),
        migrations.AddField(model_name='fete', name='ouvre_jours_avant',
                            field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name='fete', name='ferme_jours_apres',
                            field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name='fete', name='objectif_par_defaut',
                            field=models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True)),
        migrations.AddField(model_name='campaign', name='occurrence_date',
                            field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name='campaign', name='a_confirmer',
                            field=models.BooleanField(default=False)),
        migrations.AddField(model_name='campaign', name='ouverture_annoncee_le',
                            field=models.DateTimeField(blank=True, null=True)),
        migrations.AddConstraint(
            model_name='campaign',
            constraint=models.UniqueConstraint(
                condition=models.Q(fete__isnull=False, occurrence_date__isnull=False),
                fields=('fete', 'occurrence_date'),
                name='une_edition_par_serie_et_par_date',
            ),
        ),
    ]
