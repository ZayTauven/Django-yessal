from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.conf import settings
from .models import Message, MessageReaction, ChatInvitation, Notification
from .services import diffuser_message, trigger_pusher, send_fcm_push

@receiver(post_save, sender=Message)
def broadcast_message(sender, instance, created, **kwargs):
    if created:
        if instance.message_type == Message.MessageType.SYSTEM:
            # Une note de service s'affiche dans le fil ouvert, mais ne pousse
            # rien sur les téléphones et ne fait pas remonter la liste.
            trigger_pusher(f'private-chat.{instance.chat_id}', 'new-message', _charge(instance))
            return
        # La charge est celle de l'API : citation, nom de fichier, vignette.
        # Un objet recomposé à la main ici divergeait du `GET` au premier
        # champ ajouté — le fil temps réel n'aurait jamais montré la citation.
        trigger_pusher(f'private-chat.{instance.chat_id}', 'new-message', _charge(instance))
        diffuser_message(instance)
    else:
        if instance.is_deleted:
            trigger_pusher(
                f'private-chat.{instance.chat_id}',
                'message-deleted',
                {
                    'message_id': instance.id
                }
            )
        elif instance.edited_at:
            trigger_pusher(f'private-chat.{instance.chat_id}', 'message-edited', _charge(instance))


def _charge(message):
    from .serializers import MessageSerializer
    data = dict(MessageSerializer(message, context={}).data)
    data['reactions'] = []
    return data

@receiver(post_save, sender=MessageReaction)
def broadcast_reaction_add(sender, instance, created, **kwargs):
    if created:
        trigger_pusher(
            f'private-chat.{instance.message.chat_id}',
            'message-reaction',
            {
                'message_id': instance.message_id,
                'emoji': instance.emoji,
                'user_id': instance.user_id,
                'action': 'add'
            }
        )

@receiver(post_delete, sender=MessageReaction)
def broadcast_reaction_remove(sender, instance, **kwargs):
    trigger_pusher(
        f'private-chat.{instance.message.chat_id}',
        'message-reaction',
        {
            'message_id': instance.message_id,
            'emoji': instance.emoji,
            'user_id': instance.user_id,
            'action': 'remove'
        }
    )

@receiver(post_save, sender=ChatInvitation)
def broadcast_invitation(sender, instance, created, **kwargs):
    if created:
        sender_avatar = None
        if hasattr(instance.sender, 'avatar') and instance.sender.avatar:
            sender_avatar = instance.sender.avatar.url
        elif hasattr(instance.sender, 'avatar_url') and instance.sender.avatar_url:
            sender_avatar = instance.sender.avatar_url

        trigger_pusher(
            f'private-user.{instance.recipient_id}',
            'new-invitation',
            {
                'invitation_id': instance.id,
                'sender_name': instance.sender.get_full_name() or instance.sender.email or str(instance.sender.id),
                'sender_avatar': sender_avatar,
                'chat_name': instance.chat.name if instance.chat else None,
            }
        )

@receiver(post_save, sender=Notification)
def push_notification_to_fcm(sender, instance, created, **kwargs):
    if created:
        send_fcm_push(
            user_id=instance.user_id,
            title=instance.title,
            body=instance.message,
            data={'url': '/dashboard/notifications'},
        )


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_messaging_preferences(sender, instance, created, **kwargs):
    if created:
        from .models import UserMessagingPreferences
        from .canaux import abonner
        UserMessagingPreferences.objects.get_or_create(user=instance)
        # Les canaux du Kourel sont ouverts à tous, d'office.
        abonner(instance)
