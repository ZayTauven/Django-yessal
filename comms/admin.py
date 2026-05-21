from django.contrib import admin
from .models import (
    Chat,
    ChatMembership,
    Message,
    MessageReaction,
    MessageReadReceipt,
    ChatInvitation,
    UserMessagingPreferences,
    MessagingPilotageConfig,
    Notification,
    Announcement,
)

@admin.register(Chat)
class ChatAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'chat_type', 'daara', 'campaign', 'created_by', 'created_at']
    list_filter = ['chat_type', 'created_at']
    search_fields = ['name']

@admin.register(ChatMembership)
class ChatMembershipAdmin(admin.ModelAdmin):
    list_display = ['chat', 'user', 'added_by', 'joined_at', 'is_muted']
    list_filter = ['joined_at', 'is_muted']
    search_fields = ['user__email', 'user__phone']

@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'chat', 'sender', 'message_type', 'sent_at', 'is_deleted']
    list_filter = ['message_type', 'sent_at', 'is_deleted']
    search_fields = ['content', 'sender__email', 'sender__phone']

@admin.register(MessageReaction)
class MessageReactionAdmin(admin.ModelAdmin):
    list_display = ['message', 'user', 'emoji', 'created_at']

@admin.register(MessageReadReceipt)
class MessageReadReceiptAdmin(admin.ModelAdmin):
    list_display = ['message', 'user', 'read_at']

@admin.register(ChatInvitation)
class ChatInvitationAdmin(admin.ModelAdmin):
    list_display = ['sender', 'recipient', 'chat', 'status', 'created_at', 'expires_at']
    list_filter = ['status', 'created_at']

@admin.register(UserMessagingPreferences)
class UserMessagingPreferencesAdmin(admin.ModelAdmin):
    list_display = ['user', 'visibility', 'allow_direct_invites', 'allow_group_invites', 'show_online_status']
    list_filter = ['visibility', 'allow_direct_invites', 'allow_group_invites']

@admin.register(MessagingPilotageConfig)
class MessagingPilotageConfigAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'allow_cross_daara_search', 'allow_member_invite', 'allow_group_creation', 'allow_invite_accept_decline']
    list_editable = ['allow_cross_daara_search', 'allow_member_invite', 'allow_group_creation', 'allow_invite_accept_decline']
    readonly_fields = ['updated_by', 'updated_at']

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['user', 'title', 'is_read', 'created_at']
    list_filter = ['is_read', 'created_at']

@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ['title', 'target', 'urgency', 'target_role', 'daara', 'is_published']
    list_filter = ['target', 'urgency', 'target_role', 'is_published']
