from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _

class Chat(models.Model):
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

    class Meta:
        unique_together = ('chat', 'user')

class Message(models.Model):
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
    content = models.TextField()
    sent_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Message from {self.sender.email} in {self.chat.id}"

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
