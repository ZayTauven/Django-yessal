from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from core.validators import downscale_image, validate_upload_size


class Fete(models.Model):
    class Recurrence(models.TextChoices):
        ANNUAL = 'annual', 'Annuelle'
        QUARTERLY = 'quarterly', 'Trimestrielle'
        WEEKLY = 'weekly', 'Hebdomadaire'
        NONE = 'none', 'Non récurrente'

    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    date = models.DateField(null=True, blank=True)
    recurrence = models.CharField(max_length=20, choices=Recurrence.choices, default=Recurrence.ANNUAL)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='created_fetes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        # Le client dit « Événement » ; le code garde `Fete` (modèle, table,
        # route `/api/events/fetes/`). Seul le texte affiché change.
        verbose_name = 'Événement'
        verbose_name_plural = 'Événements'

    def __str__(self):
        return f"{self.name} ({self.get_recurrence_display()})"


class Campaign(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', _('Pending')
        ACTIVE = 'active', _('Active')
        COMPLETED = 'completed', _('Completed')
        INACTIVE = 'inactive', _('Inactive')

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    illustrative_photo = models.ImageField(upload_to='campaigns/photos/', blank=True, null=True, validators=[validate_upload_size])
    goal_amount = models.DecimalField(max_digits=15, decimal_places=2, blank=True, null=True)
    objective = models.TextField(blank=True, null=True)
    deadline = models.DateField()
    # Premier jour de collecte, heure de Dakar. Sans lui, « À venir » était un
    # statut posé à la main, et rien ne basculait le Ndiguel en « En cours » le
    # jour venu. Facultatif : un Ndiguel sans date d'ouverture garde le statut
    # qu'on lui donne, comme avant. Avec elle, ce sont les DATES qui décident
    # (voir `get_effective_status`), calculées par le serveur et jamais par
    # l'appareil du membre.
    opens_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    fete = models.ForeignKey('events.Fete', on_delete=models.SET_NULL, null=True, blank=True, related_name='ndiguels')

    daara = models.ForeignKey('accounts.Daara', on_delete=models.SET_NULL, null=True, blank=True, related_name='campaigns')
    organizer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='organized_campaigns')
    organizer_assigned_at = models.DateTimeField(blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='campaigns_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Ndiguel'
        verbose_name_plural = 'Ndiguels'

    def __str__(self):
        return self.name

    def is_management_open(self):
        today = timezone.localdate()
        if self.status in [self.Status.COMPLETED, self.Status.INACTIVE]:
            return False
        return self.deadline >= today

    def get_effective_status(self, today=None):
        """Le statut qui fait foi, pour le jour de Dakar.

        Clôturé ou suspendu à la main : c'est la main qui l'emporte. Sinon, les
        dates : avant `opens_on` le Ndiguel est à venir, après `deadline` il
        est terminé. La fenêtre est inclusive aux deux bouts — ouvert dès le
        premier jour à 00:00, encore ouvert le dernier jour jusqu'à 23:59.
        """
        if self.status in [self.Status.COMPLETED, self.Status.INACTIVE]:
            return self.status
        today = today or timezone.localdate()
        if self.deadline < today:
            return self.Status.COMPLETED
        if self.opens_on:
            return self.Status.PENDING if today < self.opens_on else self.Status.ACTIVE
        return self.status

    def can_be_managed_by(self, user):
        if not user or not user.is_authenticated:
            return False
        if user.role in ['admin', 'chef_daara']:
            return True
        return self.organizer_id == user.id and self.is_management_open()

    def save(self, *args, **kwargs):
        # Réduction à la source : le plafond de 15 Mo dit ce qu'on accepte,
        # pas ce qu'on doit réservir à chaque visiteur. Voir
        # core.validators.downscale_image — sans effet si l'image tient déjà
        # dans les bornes, et silencieuse en cas d'échec.
        downscale_image(self.illustrative_photo)
        super().save(*args, **kwargs)


class CampaignTodo(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name='todos')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.title} - {self.campaign.name}"


class ExecutionPlanifiee(models.Model):
    """Dernier passage de chaque tâche du planificateur (`planificateur`).

    C'est la base, et non la mémoire du processus, qui dit si la tâche du jour
    a tourné : un conteneur redémarré à 9 h ne relance pas le rappel de 8 h,
    et deux planificateurs lancés par erreur ne l'envoient pas deux fois (la
    ligne est verrouillée pendant la décision).
    """
    nom = models.CharField(max_length=80, unique=True)
    derniere_execution = models.DateTimeField(null=True, blank=True)
    dernier_resultat = models.CharField(max_length=20, blank=True)
    dernier_message = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Tâche planifiée'
        verbose_name_plural = 'Tâches planifiées'

    def __str__(self):
        return f"{self.nom} ({self.derniere_execution or 'jamais'})"
