"""
Ramène les numéros déjà en base à leur forme E.164.

À partir d'ici, `User.save()` et les sérialiseurs ne laissent plus passer autre
chose (voir `core.phone`). Restent les lignes écrites AVANT : « +221 77 000 00
00 » avec ses espaces, ou « 77 000 00 00 » sans indicatif. Elles resteraient
introuvables depuis l'écran de connexion, qui émet du E.164.

═══════════════════════════════════════════════════════════════════════════
Ce qui ne peut PAS être fait automatiquement
═══════════════════════════════════════════════════════════════════════════
`phone` est unique. Deux lignes distinctes peuvent se normaliser vers la MÊME
valeur — « +221 77 000 00 00 » et « +221770000000 » sont deux comptes, un seul
numéro. Fusionner ou supprimer relève d'une décision humaine : ces personnes
ont un historique de dons, des adhésions à des Daaras, des conversations.

La migration laisse donc la ligne en conflit INCHANGÉE et écrit un
avertissement nommant le numéro et les identifiants concernés. Elle ne supprime
jamais un compte, et n'échoue jamais : un déploiement ne doit pas s'arrêter là.
Ces comptes continuent d'ailleurs de fonctionner — `LoginSerializer` cherche
aussi sur la saisie brute, précisément pour eux.

Même traitement pour un numéro que la normalisation refuse (« à demander à sa
femme », vu dans ce genre de colonne) : signalé, conservé.

═══════════════════════════════════════════════════════════════════════════
Sens inverse
═══════════════════════════════════════════════════════════════════════════
`RunPython.noop` : une normalisation ne se défait pas. « +221770000000 » ne dit
plus si on avait écrit « 77 000 00 00 » ou « +221 77-000-00-00 », et il n'y a
rien à restaurer. Revenir en arrière laisse les numéros normalisés — ce qui est
sans danger, l'ancien code acceptait déjà cette forme.

Aucun `AlterField` ici : E.164 tient en seize caractères, le champ en accepte
vingt. Le schéma n'a pas à bouger.
"""

import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def normaliser_les_numeros(apps, schema_editor):
    # Import local et non en tête de fichier : le graphe des migrations est
    # chargé à chaque commande `manage.py`, alors que ce module ne sert que
    # pendant les quelques secondes où cette migration s'applique.
    from core.phone import normalize_phone_quietly

    User = apps.get_model('accounts', 'User')

    normalises = 0
    conflits = 0
    illisibles = 0

    lignes = (
        User.objects.exclude(phone=None)
        .exclude(phone='')
        .order_by('pk')
        .values_list('pk', 'phone')
    )

    for pk, ancien in lignes:
        nouveau = normalize_phone_quietly(ancien)

        if nouveau is None:
            illisibles += 1
            logger.warning(
                "ACTION REQUISE — numéro illisible conservé tel quel : "
                "compte #%s, valeur %r. À corriger à la main.", pk, ancien,
            )
            continue

        if nouveau == ancien:
            continue

        # L'occupant se lit en base et non dans un cache local : les lignes
        # déjà traitées par cette même boucle y sont, et une ligne encore
        # intacte qui porte DÉJÀ la forme canonique aussi.
        occupant = (
            User.objects.filter(phone=nouveau)
            .exclude(pk=pk)
            .values_list('pk', flat=True)
            .first()
        )
        if occupant is not None:
            conflits += 1
            logger.warning(
                "ACTION REQUISE — collision sur %s : le compte #%s (valeur "
                "%r) désigne le même numéro que le compte #%s. Le compte #%s "
                "est laissé tel quel ; c'est à un humain de dire lequel des "
                "deux garde ce numéro.",
                nouveau, pk, ancien, occupant, pk,
            )
            continue

        # `update()` et non `save()` : le modèle historique d'une migration n'a
        # ni le `save()` du vrai modèle ni ses effets de bord (redimensionnement
        # d'avatar, alignement de `is_staff`). On veut écrire une colonne, rien
        # d'autre.
        User.objects.filter(pk=pk).update(phone=nouveau)
        normalises += 1

    if normalises or conflits or illisibles:
        logger.warning(
            "Normalisation des téléphones : %s ligne(s) mise(s) en forme, "
            "%s collision(s), %s numéro(s) illisible(s).",
            normalises, conflits, illisibles,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_user_token_version'),
    ]

    operations = [
        migrations.RunPython(normaliser_les_numeros, migrations.RunPython.noop),
    ]
