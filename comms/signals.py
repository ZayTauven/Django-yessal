from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.conf import settings
from .models import Message, MessageReaction, ChatInvitation, Notification
from .services import trigger_pusher, send_fcm_push

@receiver(post_save, sender=Message)
def broadcast_message(sender, instance, created, **kwargs):
    if created:
        if instance.message_type == 'system':
            return
        trigger_pusher(
            f'private-chat.{instance.chat_id}',
            'new-message',
            {
                'id': instance.id,
                'message_type': instance.message_type,
                'content': instance.content,
                'file_url': instance.file.url if instance.file else None,
                'sender_id': instance.sender_id,
                'sender_name': instance.sender.get_full_name() or instance.sender.email or str(instance.sender.id),
                'reply_to_id': instance.reply_to_id,
                'sent_at': instance.sent_at.isoformat(),
            }
        )
    else:
        if instance.is_deleted:
            trigger_pusher(
                f'private-chat.{instance.chat_id}',
                'message-deleted',
                {
                    'message_id': instance.id
                }
            )

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
        UserMessagingPreferences.objects.get_or_create(user=instance)
