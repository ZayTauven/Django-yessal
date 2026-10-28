"""Remet dans le circuit les Jëfs en espèces restés hors de lui.

Avant le circuit demandé → encaissé → remise (0009), un Jëf en espèces portant
un COLLECTEUR pouvait rester `pending`. `seed_demo` en fabriquait. Le nouveau
circuit ne connaît pas cette combinaison : une demande ouverte n'a pas de
collecteur, et un Jëf qui en a un est `collected`. Ces Jëfs n'apparaissaient
donc ni dans « Demandes de collecte » ni dans « Ma caisse », ne pouvaient pas
être remis, et gonflaient les résumés (rapport de test du 2026-10-28, B6).

Pour un Jëf en espèces, un collecteur désigné veut dire « argent en main » : ils
passent `collected`, datés de leur création. Ils entrent ainsi dans la caisse de
leur collecteur, où l'administration peut les remettre ou les annuler avec un
motif.

⚠ Écartés volontairement : les Jëfs où donateur et collecteur sont la MÊME
personne. C'est la trace de l'ancien bug du mode collecte mobile (`member_id`
ignoré), et le vrai membre ne se devine pas : ils relèvent de l'audit
« Intégrité argent », pas d'une règle automatique.

Pas de retour arrière : rien ne distingue après coup un Jëf passé `collected`
ici d'un Jëf encaissé normalement.
"""

from django.db import migrations
from django.db.models import F


def vers_encaisse(apps, schema_editor):
    Donation = apps.get_model('contributions', 'Donation')
    (
        Donation.objects
        .filter(payment_method='manual', payment_status='pending', collector__isnull=False)
        .exclude(donor_id=F('collector_id'))
        .update(payment_status='collected', collected_at=F('created_at'))
    )


class Migration(migrations.Migration):

    dependencies = [
        ('contributions', '0009_circuit_especes_et_remise'),
    ]

    operations = [
        migrations.RunPython(vers_encaisse, migrations.RunPython.noop),
    ]
