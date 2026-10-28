"""Donne aux collecteurs déjà nommés la zone qu'une nomination leur donnerait.

Depuis 0017, un collecteur ne voit que les demandes de ses zones. Les
collecteurs nommés AVANT n'en ont aucune : sans cette migration, ils ne
verraient plus une seule demande au lendemain du déploiement. Ils reçoivent la
LDD de leur Daara — la règle de `User.save` pour une nomination. Ceux qui n'ont
pas de Daara restent sans zone : à affecter depuis Pilotage › Collecteurs.
"""

from django.db import migrations


def zones_par_defaut(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    for c in User.objects.filter(role='collector', daara__isnull=False).select_related('daara'):
        if not c.collecte_zones.exists():
            c.collecte_zones.add(c.daara.ldd_id)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0017_pilotagesettings_collecte_modifiee_le_and_more'),
    ]

    operations = [
        migrations.RunPython(zones_par_defaut, migrations.RunPython.noop),
    ]
