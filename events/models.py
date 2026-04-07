from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _

class Event(models.Model):
    class Recurrence(models.TextChoices):
        ANNUAL = 'annual', _('Annual')
        QUARTERLY = 'quarterly', _('Quarterly')
        WEEKLY = 'weekly', _('Weekly')
        NONE = 'none', _('None')

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    event_date = models.DateField(blank=True, null=True)
    recurrence = models.CharField(
        max_length=20, 
        choices=Recurrence.choices, 
        default=Recurrence.NONE
    )
    is_date_fixed = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        related_name='events_created'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

class EventMedia(models.Model):
    class MediaType(models.TextChoices):
        PHOTO = 'photo', _('Photo')
        VIDEO = 'video', _('Video')
        TEXT = 'text', _('Text')
        LINK = 'link', _('Link')

    event = models.ForeignKey(
        Event, 
        on_delete=models.CASCADE, 
        related_name='media'
    )
    media_type = models.CharField(
        max_length=20, 
        choices=MediaType.choices, 
        default=MediaType.PHOTO
    )
    url = models.URLField(max_length=500, blank=True, null=True)
    content = models.TextField(blank=True, null=True) # For type "text"
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.media_type} for {self.event.name}"
