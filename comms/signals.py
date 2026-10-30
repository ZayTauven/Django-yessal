from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from django.conf import settings
from .models import Message, MessageReaction, ChatInvitation, Notification
from .services import diffuser_message, publier, send_fcm_push

@receiver(post_save, sender=Message)
def broadcast_message(sender, instance, created, **kwargs):
    if created:
        if instance.message_type == Message.MessageType.SYSTEM:
            # Une note de service s'affiche dans le fil ouvert, mais ne pousse
            # rien sur les téléphones et ne fait pas remonter la liste.
            publier(f'private-chat.{instance.chat_id}', 'new-message', _charge(instance))
            return
        # La charge est celle de l'API : citation, nom de fichier, vignette.
        # Un objet recomposé à la main ici divergeait du `GET` au premier
        # champ ajouté — le fil temps réel n'aurait jamais montré la citation.
        publier(f'private-chat.{instance.chat_id}', 'new-message', _charge(instance))
        diffuser_message(instance)
    else:
        if instance.is_deleted:
            publier(
                f'private-chat.{instance.chat_id}',
                'message-deleted',
                {
                    'message_id': instance.id
                }
            )
        elif instance.edited_at:
            publier(f'private-chat.{instance.chat_id}', 'message-edited', _charge(instance))


def _charge(message):
    from .serializers import MessageSerializer
    data = dict(MessageSerializer(message, context={}).data)
    data['reactions'] = []
    # L'identifiant que l'expéditeur a donné à sa bulle « en cours » : il la
    # remplace par celle-ci sans deviner (deux photos sans légende envoyées
    # d'affilée avaient le même contenu vide, et se confondaient).
    data['client_id'] = getattr(message, '_client_id', None)
    return data


def _reactions(message_id):
    """L'état COMPLET des réactions d'un message, avec qui a réagi.

    Le client remplaçait des compteurs par +1/−1 : un événement perdu ou reçu
    deux fois faussait le compte pour de bon, et ses propres réactions faites
    depuis le téléphone n'apparaissaient pas sur l'ordinateur. Avec l'état
    entier, chaque appareil se recale au premier événement.
    """
    agregat = {}
    for emoji, user_id in MessageReaction.objects.filter(message_id=message_id).values_list('emoji', 'user_id'):
        entree = agregat.setdefault(emoji, {'emoji': emoji, 'count': 0, 'user_ids': []})
        entree['count'] += 1
        entree['user_ids'].append(user_id)
    return list(agregat.values())

@receiver(post_save, sender=MessageReaction)
def broadcast_reaction_add(sender, instance, created, **kwargs):
    if created:
        publier(
            f'private-chat.{instance.message.chat_id}',
            'message-reaction',
            {
                'message_id': instance.message_id,
                'emoji': instance.emoji,
                'user_id': instance.user_id,
                'action': 'add',
                'reactions': _reactions(instance.message_id),
            }
        )

@receiver(post_delete, sender=MessageReaction)
def broadcast_reaction_remove(sender, instance, **kwargs):
    publier(
        f'private-chat.{instance.message.chat_id}',
        'message-reaction',
        {
            'message_id': instance.message_id,
            'emoji': instance.emoji,
            'user_id': instance.user_id,
            'action': 'remove',
            'reactions': _reactions(instance.message_id),
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

        publier(
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


@receiver(pre_save, sender=settings.AUTH_USER_MODEL)
def retenir_le_role(sender, instance, update_fields=None, **kwargs):
    """Le rôle d'AVANT l'écriture : seul moyen de savoir qu'il change."""
    instance._role_avant = None
    if instance.pk and (update_fields is None or 'role' in update_fields):
        instance._role_avant = (
            sender.objects.filter(pk=instance.pk).values_list('role', flat=True).first()
        )


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def auteur_du_canal_du_guide(sender, instance, created, update_fields=None, **kwargs):
    """La guide publie d'office dans son canal ; elle n'y publie plus si elle
    quitte ce rôle.

    Seul un PASSAGE de rôle touche au droit : un auteur désigné à la main par
    un administrateur (`ChatViewSet.set_author`) le garde, quel que soit son
    rôle, et une connexion (`last_login`) ne réécrit rien.
    """
    if update_fields is not None and 'role' not in update_fields:
        return
    avant = getattr(instance, '_role_avant', None)
    guide = sender.Role.GUIDE
    if instance.role == guide and (created or avant != guide):
        droit = True
    elif avant == guide and instance.role != guide:
        droit = False
    else:
        return
    from .canaux import canal
    from .models import Chat, ChatMembership
    chat = canal(Chat.ChannelKind.GUIDE)
    if not chat:
        return
    membership, _ = ChatMembership.objects.get_or_create(chat=chat, user=instance)
    if membership.can_post != droit:
        membership.can_post = droit
        membership.save(update_fields=['can_post'])
