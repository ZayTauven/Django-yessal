from django.db import migrations, models
from django.db.models import F


def createurs_moderateurs(apps, schema_editor):
    """Chaque salon existant reçoit son créateur pour modérateur.

    Les salons créés par l'application n'enregistraient pas leur créateur
    (`created_by` vide : constaté sur les 5 salons de la démo le 2026-10-30).
    Il se retrouve : c'est la seule appartenance que son titulaire s'est
    ajoutée lui-même (`added_by == user`), la création l'inscrivant ainsi.
    """
    Chat = apps.get_model('comms', 'Chat')
    ChatMembership = apps.get_model('comms', 'ChatMembership')

    for chat in Chat.objects.filter(chat_type='group', created_by__isnull=True):
        createur = (
            ChatMembership.objects.filter(chat=chat, user_id=F('added_by_id'))
            .order_by('joined_at')
            .values_list('user_id', flat=True)
            .first()
        )
        if createur:
            chat.created_by_id = createur
            chat.save(update_fields=['created_by'])

    for chat in Chat.objects.filter(chat_type='group', created_by__isnull=False):
        ChatMembership.objects.filter(chat=chat, user_id=chat.created_by_id).update(is_moderator=True)


class Migration(migrations.Migration):

    dependencies = [
        ('comms', '0013_gestion_des_canaux'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatmembership',
            name='is_moderator',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(createurs_moderateurs, migrations.RunPython.noop),
    ]
