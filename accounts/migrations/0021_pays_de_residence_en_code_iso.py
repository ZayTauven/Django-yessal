"""Le pays de résidence passe d'un texte libre à un code ISO (`core/pays.py`).

Chaque saisie existante est ramenée à son code : « Sénégal », « Senegal » et
« SN » deviennent « SN ». Une saisie que la table ne reconnaît pas ne tient
pas dans deux caractères ; elle est vidée — le membre la refera depuis la
liste — et ANNONCÉE ici, pour qu'aucune ne disparaisse sans trace.
"""

from django.db import migrations, models

import core.pays


def vers_codes(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    inconnus = []
    for user in User.objects.exclude(residence_country=None).only('id', 'residence_country'):
        code = core.pays.code_pays(user.residence_country)
        if code is None and user.residence_country.strip():
            inconnus.append((user.pk, user.residence_country))
        if code != user.residence_country:
            User.objects.filter(pk=user.pk).update(residence_country=code)
    for pk, valeur in inconnus:
        print(f"\n  pays de résidence non reconnu, vidé : membre {pk} « {valeur} »", end='')


def vers_noms(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    for code, nom in core.pays.PAYS:
        User.objects.filter(residence_country=code).update(residence_country=nom)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0020_pieces_identite_nom_aleatoire'),
    ]

    operations = [
        migrations.RunPython(vers_codes, vers_noms),
        migrations.AlterField(
            model_name='user',
            name='residence_country',
            field=models.CharField(blank=True, choices=core.pays.CHOIX, max_length=2, null=True),
        ),
    ]
