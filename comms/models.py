from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _

class Chat(models.Model):
    class ChatType(models.TextChoices):
        DIRECT = 'direct', _('Direct')
        GROUP = 'group', _('Group')

    chat_type = models.CharField(
        max_length=10,
        choices=ChatType.choices,
        default=ChatType.GROUP
    )
    name = models.CharField(max_length=200, blank=True, null=True)
    daara = models.ForeignKey(
        'accounts.Daara',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='chats'
    )
    campaign = models.ForeignKey(
        'events.Campaign',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='chats'
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='chats_created'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        if self.chat_type == self.ChatType.DIRECT:
            return f"Direct Chat {self.id}"
        return self.name or f"Chat {self.id} for {self.daara.name if self.daara else (self.campaign.name if self.campaign else 'Internal')}"

class ChatMembership(models.Model):
    chat = models.ForeignKey(
        Chat,
        on_delete=models.CASCADE,
        related_name='memberships'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='chat_memberships'
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='members_added'
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    is_muted = models.BooleanField(default=False)
    last_read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('chat', 'user')
        indexes = [
            models.Index(fields=['user', 'chat']),
        ]

class Message(models.Model):
    class MessageType(models.TextChoices):
        TEXT = 'text', _('Text')
        FILE = 'file', _('File')
        SYSTEM = 'system', _('System')

    chat = models.ForeignKey(
        Chat,
        on_delete=models.CASCADE,
        related_name='messages'
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='messages_sent'
    )
    message_type = models.CharField(
        max_length=10,
        choices=MessageType.choices,
        default=MessageType.TEXT
    )
    content = models.TextField()
    file = models.FileField(upload_to='chat_files/', null=True, blank=True)
    reply_to = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='replies'
    )
    is_deleted = models.BooleanField(default=False)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sent_at']
        indexes = [
            models.Index(fields=['chat', 'sent_at']),
            models.Index(fields=['sender', 'sent_at']),
        ]

    def __str__(self):
        return f"Message from {self.sender.email or self.sender.phone} in Chat {self.chat.id}"

class MessageReaction(models.Model):
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name='reactions'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='message_reactions'
    )
    emoji = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('message', 'user', 'emoji')

    def __str__(self):
        return f"{self.user} reacted {self.emoji} to message {self.message.id}"

class MessageReadReceipt(models.Model):
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name='read_receipts'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='message_read_receipts'
    )
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('message', 'user')

    def __str__(self):
        return f"{self.user} read message {self.message.id} at {self.read_at}"

class ChatInvitation(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', _('Pending')
        ACCEPTED = 'accepted', _('Accepted')
        DECLINED = 'declined', _('Declined')
        EXPIRED = 'expired', _('Expired')

    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sent_invitations'
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='received_invitations'
    )
    chat = models.ForeignKey(
        Chat,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='invitations'
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['recipient', 'status']),
            models.Index(fields=['sender', 'status']),
        ]

    def save(self, *args, **kwargs):
        if not self.expires_at:
            from django.utils import timezone
            self.expires_at = timezone.now() + timezone.timedelta(days=7)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Invitation from {self.sender} to {self.recipient} ({self.status})"

class UserMessagingPreferences(models.Model):
    class Visibility(models.TextChoices):
        ALL = 'all', _('Everyone')
        DAARA_ONLY = 'daara_only', _('My Daara')
        NOBODY = 'nobody', _('Nobody')

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='messaging_preferences'
    )
    visibility = models.CharField(
        max_length=20,
        choices=Visibility.choices,
        default=Visibility.ALL
    )
    allow_direct_invites = models.BooleanField(default=True)
    allow_group_invites = models.BooleanField(default=True)
    show_online_status = models.BooleanField(default=True)
    notifications_enabled = models.BooleanField(default=True)
    notify_on_mention_only = models.BooleanField(default=False)

    def __str__(self):
        return f"Preferences for {self.user}"

class MessagingPilotageConfig(models.Model):
    daara = models.OneToOneField(
        'accounts.Daara',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='messaging_pilotage_config'
    )
    allow_cross_daara_search = models.BooleanField(default=True)
    allow_member_invite = models.BooleanField(default=True)
    allow_group_creation = models.BooleanField(default=True)
    allow_invite_accept_decline = models.BooleanField(default=True)
    allow_member_visibility_setting = models.BooleanField(default=True)
    allow_file_sharing = models.BooleanField(default=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='updated_pilotage_configs'
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Pilotage: {self.daara.name if self.daara else 'GLOBAL'}"

class Notification(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications'
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Notification for {self.user.email} - {self.title}"


class FCMToken(models.Model):
    class DeviceType(models.TextChoices):
        WEB = 'web', _('Web')
        ANDROID = 'android', _('Android')
        IOS = 'ios', _('iOS')

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='fcm_tokens',
    )
    token = models.TextField(unique=True)
    device_type = models.CharField(max_length=10, choices=DeviceType.choices, default=DeviceType.WEB)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'FCM Token'

    def __str__(self):
        return f"FCMToken({self.device_type}) – {self.user_id}"

class Announcement(models.Model):
    class Target(models.TextChoices):
        GLOBAL = 'global', _('Global')
        DAARA_ONLY = 'daara_only', _('Specific Daara')

    class Urgency(models.TextChoices):
        INFO = 'info', _('Information')
        WARNING = 'warning', _('Warning')
        CRITICAL = 'critical', _('Critical')

    class TargetRole(models.TextChoices):
        ALL = 'all', _('All Roles')
        ADMIN = 'admin', _('Admin Only')
        CHEF_DAARA = 'chef_daara', _('Chef Daara Only')
        COLLECTOR = 'collector', _('Collector Only')
        MEMBER = 'member', _('Member Only')

    title = models.CharField(max_length=255)
    content = models.TextField()
    target = models.CharField(max_length=20, choices=Target.choices, default=Target.GLOBAL)
    urgency = models.CharField(max_length=20, choices=Urgency.choices, default=Urgency.INFO)
    target_role = models.CharField(max_length=20, choices=TargetRole.choices, default=TargetRole.ALL)
    daara = models.ForeignKey(
        'accounts.Daara', 
        on_delete=models.CASCADE, 
        null=True, 
        blank=True, 
        related_name='announcements'
    )
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return self.title
