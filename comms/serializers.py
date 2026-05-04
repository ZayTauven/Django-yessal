from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Message, Chat, ChatMembership, Announcement, Notification

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


class MessageSerializer(serializers.ModelSerializer):
    sender_email = serializers.EmailField(source='sender.email', read_only=True)
    sender_name = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ['id', 'chat', 'sender', 'sender_email', 'sender_name', 'content', 'sent_at']

    def get_sender_name(self, obj):
        u = obj.sender
        name = u.get_full_name()
        return name.strip() if name else u.email


class ChatSerializer(serializers.ModelSerializer):
    campaign_name = serializers.CharField(source='campaign.name', read_only=True)

    class Meta:
        model = Chat
        fields = ['id', 'name', 'daara', 'campaign', 'campaign_name', 'created_by', 'created_at']


class CreateChatSerializer(serializers.Serializer):
    """Payload pour créer un salon (admin ou chef de Daara uniquement, côté vue)."""

    name = serializers.CharField(max_length=200, trim_whitespace=True)
    daara_id = serializers.IntegerField(required=False, allow_null=True)
    invite_mode = serializers.ChoiceField(
        choices=[
            ('manual', 'manual'),
            ('daara_all', 'daara_all'),
            ('daara_members', 'daara_members'),
            ('daara_collectors', 'daara_collectors'),
            ('daara_chefs', 'daara_chefs'),
            ('global_chefs', 'global_chefs'),
            ('global_collectors', 'global_collectors'),
        ],
        default='manual',
        required=False,
    )
    preset_daara_id = serializers.IntegerField(required=False, allow_null=True)
    manual_user_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        default=list,
    )
    campaign_id = serializers.IntegerField(required=False, allow_null=True)
