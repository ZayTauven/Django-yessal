from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import (
    Message,
    Chat,
    ChatMembership,
    Announcement,
    Notification,
    MessageReaction,
    ChatInvitation,
    UserMessagingPreferences,
    MessagingPilotageConfig,
)

User = get_user_model()

class AnnouncementSerializer(serializers.ModelSerializer):
    daara_name = serializers.CharField(source='daara.name', read_only=True)

    class Meta:
        model = Announcement
        fields = ['id', 'title', 'content', 'target', 'daara', 'daara_name', 'urgency', 'target_role', 'is_published', 'created_at', 'expires_at']


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ['id', 'title', 'message', 'is_read', 'created_at']


class UserBriefSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    avatar = serializers.SerializerMethodField()
    daara_name = serializers.CharField(source='daara.name', read_only=True)

    class Meta:
        model = User
        fields = ['id', 'name', 'avatar', 'daara_name', 'role']

    def get_name(self, obj):
        name = obj.get_full_name()
        return name.strip() if name else (obj.email or obj.phone or f"User {obj.id}")

    def get_avatar(self, obj):
        request = self.context.get('request')
        if obj.avatar:
            url = obj.avatar.url
            return request.build_absolute_uri(url) if request else url
        return obj.avatar_url or None


class MessageReactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageReaction
        fields = ['id', 'emoji', 'user']


class MessageSerializer(serializers.ModelSerializer):
    sender = UserBriefSerializer(read_only=True)
    reactions = serializers.SerializerMethodField()
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ['id', 'chat', 'sender', 'message_type', 'content', 'file', 'file_url', 'reply_to', 'reactions', 'is_deleted', 'sent_at']
        read_only_fields = ['sender', 'sent_at']

    def get_file_url(self, obj):
        return obj.file.url if obj.file else None

    def get_reactions(self, obj):
        # Regrouper les réactions par emoji
        user = self.context.get('request').user if self.context.get('request') else None
        reactions_qs = obj.reactions.all()
        aggregated = {}
        for r in reactions_qs:
            emoji = r.emoji
            if emoji not in aggregated:
                aggregated[emoji] = {
                    'emoji': emoji,
                    'count': 0,
                    'reacted_by_me': False
                }
            aggregated[emoji]['count'] += 1
            if user and r.user_id == user.id:
                aggregated[emoji]['reacted_by_me'] = True
        return list(aggregated.values())


class ChatSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()
    avatar = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    members_count = serializers.SerializerMethodField()

    class Meta:
        model = Chat
        fields = ['id', 'chat_type', 'display_name', 'avatar', 'last_message', 'unread_count', 'members_count', 'daara', 'campaign', 'created_at']

    def _get_other_member(self, obj):
        request = self.context.get('request')
        if not request or not request.user:
            return None
        # Cache the member lookup
        if not hasattr(self, '_other_member_cache'):
            self._other_member_cache = {}
        if obj.id not in self._other_member_cache:
            prefetched_memberships = getattr(obj, 'memberships', None)
            if hasattr(prefetched_memberships, 'all'):
                membership = next(
                    (m for m in prefetched_memberships.all() if m.user_id != request.user.id),
                    None
                )
            else:
                membership = obj.memberships.exclude(user=request.user).select_related('user').first()
            self._other_member_cache[obj.id] = membership.user if membership else None
        return self._other_member_cache[obj.id]

    def get_display_name(self, obj):
        if obj.chat_type == Chat.ChatType.DIRECT:
            other = self._get_other_member(obj)
            if other:
                return other.get_full_name() or other.email or other.phone or f"Membres {other.id}"
            return "Contact inconnu"
        return obj.name or f"Salon #{obj.id}"

    def get_avatar(self, obj):
        request = self.context.get('request')
        if obj.chat_type == Chat.ChatType.DIRECT:
            other = self._get_other_member(obj)
            if other:
                if other.avatar:
                    url = other.avatar.url
                    return request.build_absolute_uri(url) if request else url
                return other.avatar_url or None
        return None

    def get_last_message(self, obj):
        if getattr(obj, 'last_message_id', None) is None:
            return None

        sender_full_name = (
            f"{(obj.last_message_sender_first_name or '').strip()} {(obj.last_message_sender_last_name or '').strip()}"
        ).strip()
        sender_name = sender_full_name or obj.last_message_sender_email or "Utilisateur"
        content = "Message supprime" if obj.last_message_deleted else (obj.last_message_content or "")
        if obj.last_message_type == Message.MessageType.FILE:
            content = "Fichier partage" if not obj.last_message_deleted else content

        return {
            'content': content,
            'sent_at': obj.last_message_sent_at.isoformat() if obj.last_message_sent_at else None,
            'sender_name': sender_name
        }

    def get_unread_count(self, obj):
        request = self.context.get('request')
        if not request or not request.user:
            return 0
        if hasattr(obj, 'unread_count_annotated'):
            return obj.unread_count_annotated
        membership = obj.memberships.filter(user=request.user).first()
        if not membership:
            return 0
        if not membership.last_read_at:
            return obj.messages.count()
        return obj.messages.filter(sent_at__gt=membership.last_read_at).count()

    def get_members_count(self, obj):
        if hasattr(obj, 'members_count_annotated'):
            return obj.members_count_annotated
        return obj.memberships.count()


class CreateGroupChatSerializer(serializers.ModelSerializer):
    member_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False
    )
    manual_user_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False
    )
    invite_mode = serializers.ChoiceField(
        choices=[
            'manual',
            'daara_all',
            'daara_members',
            'daara_collectors',
            'daara_chefs',
            'global_chefs',
            'global_collectors',
        ],
        write_only=True,
        required=False,
        default='manual'
    )
    daara_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    preset_daara_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    campaign_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = Chat
        fields = [
            'id',
            'chat_type',
            'name',
            'member_ids',
            'manual_user_ids',
            'invite_mode',
            'daara_id',
            'preset_daara_id',
            'campaign_id',
        ]

    def create(self, validated_data):
        request = self.context.get('request')
        invite_mode = validated_data.pop('invite_mode', 'manual')
        member_ids = set(validated_data.pop('member_ids', []))
        member_ids.update(validated_data.pop('manual_user_ids', []))
        daara_id = validated_data.pop('daara_id', None)
        preset_daara_id = validated_data.pop('preset_daara_id', None)
        campaign_id = validated_data.pop('campaign_id', None)
        validated_data['chat_type'] = Chat.ChatType.GROUP
        if daara_id:
            validated_data['daara_id'] = daara_id
        if campaign_id:
            validated_data['campaign_id'] = campaign_id
        chat = Chat.objects.create(**validated_data)

        target_daara_id = preset_daara_id or daara_id or getattr(getattr(request, 'user', None), 'daara_id', None)
        scoped_users = User.objects.filter(is_active=True).exclude(role=User.Role.ADMIN)

        if invite_mode in {'daara_all', 'daara_members', 'daara_collectors', 'daara_chefs'} and target_daara_id:
            scoped_users = scoped_users.filter(daara_id=target_daara_id)
            role_by_mode = {
                'daara_members': User.Role.MEMBER,
                'daara_collectors': User.Role.COLLECTOR,
                'daara_chefs': User.Role.CHEF_DAARA,
            }.get(invite_mode)
            if role_by_mode:
                scoped_users = scoped_users.filter(role=role_by_mode)
            member_ids.update(scoped_users.values_list('id', flat=True))
        elif invite_mode == 'global_chefs':
            member_ids.update(scoped_users.filter(role=User.Role.CHEF_DAARA).values_list('id', flat=True))
        elif invite_mode == 'global_collectors':
            member_ids.update(scoped_users.filter(role=User.Role.COLLECTOR).values_list('id', flat=True))
        
        # Le créateur est toujours membre
        if request and request.user:
            ChatMembership.objects.create(chat=chat, user=request.user, added_by=request.user)

        # Ajouter les autres membres
        for uid in member_ids:
            if request and request.user and int(uid) == request.user.id:
                continue
            try:
                user = User.objects.get(pk=uid)
                ChatMembership.objects.get_or_create(chat=chat, user=user, defaults={'added_by': request.user if request else None})
            except User.DoesNotExist:
                pass
        return chat


class ChatInvitationSerializer(serializers.ModelSerializer):
    sender = UserBriefSerializer(read_only=True)
    recipient = UserBriefSerializer(read_only=True)
    chat_name = serializers.CharField(source='chat.name', read_only=True)

    class Meta:
        model = ChatInvitation
        fields = ['id', 'sender', 'recipient', 'chat', 'chat_name', 'status', 'created_at', 'expires_at']
        read_only_fields = ['sender', 'status', 'created_at', 'expires_at']


class UserMessagingPreferencesSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserMessagingPreferences
        fields = [
            'visibility',
            'allow_direct_invites',
            'allow_group_invites',
            'show_online_status',
            'notifications_enabled',
            'notify_on_mention_only',
        ]


class MessagingPilotageConfigSerializer(serializers.ModelSerializer):
    daara_name = serializers.CharField(source='daara.name', read_only=True)

    class Meta:
        model = MessagingPilotageConfig
        fields = [
            'id',
            'daara',
            'daara_name',
            'allow_cross_daara_search',
            'allow_member_invite',
            'allow_group_creation',
            'allow_invite_accept_decline',
            'allow_member_visibility_setting',
            'allow_file_sharing',
            'updated_at',
        ]

