"""Les deux canaux globaux du Kourel, et l'abonnement de tous les membres.

Décisions du 2026-10-29 : canaux GLOBAUX (pas un par Daara), abonnement
d'office. Les membres inscrits plus tard sont abonnés par le signal de
création de compte (`comms/signals.py`).
"""
from django.db import migrations

CANAUX = [
    ('guide', 'Annonces de Sangue Bi', 'Annonces et enseignements du guide.'),
    ('jefs', 'Les Jëfs du Kourel', 'Chaque Jëf confirmé, sans montant. On y réagit, on n\'y répond pas.'),
]


def creer(apps, schema_editor):
    Chat = apps.get_model('comms', 'Chat')
    ChatMembership = apps.get_model('comms', 'ChatMembership')
    User = apps.get_model('accounts', 'User')
    # Tous les comptes, actifs ou non : un compte en attente de validation
    # trouvera ses canaux le jour où il pourra se connecter.
    membres = list(User.objects.exclude(email='systeme@yessal.local').values_list('id', flat=True))
    for kind, name, description in CANAUX:
        chat, _ = Chat.objects.get_or_create(
            channel_kind=kind,
            defaults={'chat_type': 'channel', 'name': name, 'description': description},
        )
        deja = set(ChatMembership.objects.filter(chat=chat).values_list('user_id', flat=True))
        ChatMembership.objects.bulk_create(
            [ChatMembership(chat=chat, user_id=uid) for uid in membres if uid not in deja],
            batch_size=500,
        )


def supprimer(apps, schema_editor):
    Chat = apps.get_model('comms', 'Chat')
    Chat.objects.filter(channel_kind__in=[k for k, _, _ in CANAUX]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('comms', '0010_canaux_et_cartes'),
        ('accounts', '0018_zones_par_defaut_collecteurs'),
    ]
    operations = [migrations.RunPython(creer, supprimer)]
