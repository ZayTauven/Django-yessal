"""« Afficher les noms » dans « Les Jëfs du Kourel » (2026-10-31).

Trois choses :

  · le réglage `Chat.show_names` ;
  · le canal existant le reçoit ACTIVÉ : jusqu'ici, un donateur qui avait
    accepté d'être nommé l'était toujours. Rien ne change donc à l'écran au
    déploiement ; l'administration peut l'éteindre ;
  · les cartes déjà publiées sont recomposées en leurs deux versions
    (discrète, nommée) que le sérialiseur choisit désormais. Leur illustration
    ne bouge pas.
"""

from django.db import migrations, models


def activer_les_noms(apps, schema_editor):
    Chat = apps.get_model('comms', 'Chat')
    Chat.objects.filter(channel_kind='jefs').update(show_names=True)


def recomposer_les_cartes(apps, schema_editor):
    # Les modèles RÉELS : la composition vit dans `canaux.composer_carte`, et
    # la réécrire ici en ferait deux copies qui divergeraient.
    from comms.canaux import composer_carte
    from comms.models import Message
    from contributions.models import Donation

    for message in Message.objects.filter(message_type='card', payload__kind='jef'):
        ancien = message.payload or {}
        if 'text_discreet' in ancien:
            continue
        don = (
            Donation.objects.select_related('donor', 'donor__daara', 'donor__title', 'campaign')
            .filter(pk=ancien.get('donation_id')).first()
        )
        if don is None:
            continue
        texte, neuf = composer_carte(don, rang=ancien.get('milestone') or 0)
        illustration = {k: v for k, v in ancien.items() if k not in ('donor', 'amount')}
        message.payload = {**illustration, **{
            k: v for k, v in neuf.items() if k in ('text_discreet', 'text_named', 'donor', 'amount')
        }}
        message.content = texte
        message.save(update_fields=['payload', 'content'])


class Migration(migrations.Migration):

    dependencies = [
        ('comms', '0014_moderateur_de_salon'),
        ('contributions', '0013_donation_public_in_channel'),
        ('accounts', '0020_pieces_identite_nom_aleatoire'),
        ('events', '0014_series_et_editions'),
    ]

    operations = [
        migrations.AddField(
            model_name='chat',
            name='show_names',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(activer_les_noms, migrations.RunPython.noop),
        migrations.RunPython(recomposer_les_cartes, migrations.RunPython.noop),
    ]
