from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class DonationArchive(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    total_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.total_count} dons · {self.total_amount} FCFA)"


class CashDeposit(models.Model):
    """Bordereau de remise : les espèces qu'un collecteur apporte à l'administration.

    C'est la page du cahier que le Kourel tenait à la main, horodatée et
    signée. Un administrateur compte l'argent, saisit le total compté, et la
    remise n'est enregistrée que si ce total correspond au centime près aux
    Jëfs qu'elle couvre. Ces Jëfs passent alors seulement à « confirmé ».

    Celui qui a encaissé ne confirme jamais lui-même : la remise est le seul
    chemin d'un Jëf en espèces vers le total d'un Ndiguel.
    """

    collector = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='cash_deposits',
    )
    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='validated_cash_deposits',
    )
    total_amount = models.DecimalField(max_digits=15, decimal_places=2)
    total_count = models.PositiveIntegerField()
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Remise en espèces'
        verbose_name_plural = 'Remises en espèces'

    def __str__(self):
        return f"Remise n°{self.pk} ({self.total_count} Jëfs · {self.total_amount} FCFA)"


class Donation(models.Model):
    class PaymentMethod(models.TextChoices):
        ORANGE_MONEY = 'orange_money', _('Orange Money')
        WAVE = 'wave', _('Wave')
        BICTORYS = 'bictorys', _('Bictorys (carte)')
        VIREMENT = 'virement', _('Virement bancaire')
        MANUAL = 'manual', _('Espèces (collecteur)')

        # Legacy values kept for backward compatibility.
        PAYPAL = 'paypal', _('PayPal')
        COLLECTOR = 'collector', _('Collector')
        VISA = 'visa', _('Visa')
        MASTERCARD = 'mastercard', _('Mastercard')

    # Les libellés partent tels quels dans le courriel `jef_enregistre`
    # (« Statut : Pending ») : ils sont en français.
    #
    # Circuit d'un Jëf en espèces :
    #   pending   → le membre a demandé une collecte
    #   collected → un collecteur a l'argent en main
    #   confirmed → l'administration l'a reçu, via un `CashDeposit`
    # `cancelled` n'est posé que par un administrateur, avec un motif.
    class PaymentStatus(models.TextChoices):
        PENDING = 'pending', _('En attente')
        PENDING_WIRE = 'pending_wire', _('Virement en attente')
        COLLECTED = 'collected', _('Encaissé par le collecteur')
        CONFIRMED = 'confirmed', _('Confirmé')
        FAILED = 'failed', _('Échoué')
        CANCELLED = 'cancelled', _('Annulé')

    # Le plancher d'un Jëf, volontairement bas : sur une plateforme
    # confrérique, refuser une offrande modeste serait mal reçu. Le web
    # (`lib/jef.ts`) et le mobile (`donate.tsx`) affichent la même valeur.
    MIN_AMOUNT = 100

    # Ce qu'on peut ÉMETTRE aujourd'hui. Les valeurs héritées restent
    # lisibles sur les anciens Jëfs, jamais acceptées sur un nouveau.
    CURRENT_METHODS = (
        PaymentMethod.ORANGE_MONEY,
        PaymentMethod.WAVE,
        PaymentMethod.BICTORYS,
        PaymentMethod.VIREMENT,
        PaymentMethod.MANUAL,
    )

    # PROTECT, et non CASCADE : supprimer un Ndiguel ou un membre effaçait EN
    # SILENCE tous ses Jëfs — confirmés, remis, comptés dans un bordereau. Un
    # Ndiguel se clôture, un compte se bloque ; l'argent reste au registre.
    campaign = models.ForeignKey('events.Campaign', on_delete=models.PROTECT, related_name='donations')
    donor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='donations_made')
    beneficiary = models.ForeignKey('accounts.Tutelle', on_delete=models.SET_NULL, null=True, blank=True, related_name='donations_received')
    target_daara = models.ForeignKey(
        'accounts.Daara',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='received_donations',
        help_text='Daara du membre bénéficiaire',
    )
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    is_anonymous = models.BooleanField(default=False)
    # Consentement EXPLICITE à voir son nom dans le canal « Les Jëfs du
    # Kourel ». Distinct d'`is_anonymous` (classements internes, `False` par
    # défaut) : réutiliser celui-là aurait publié le nom de tous les donateurs
    # passés, qui n'avaient rien accepté de tel. Jamais de montant, dans aucun cas.
    public_in_channel = models.BooleanField(default=False)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.MANUAL)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    wire_reference = models.CharField(max_length=100, blank=True, help_text='Référence de virement saisie par le membre')
    archive_id = models.ForeignKey('DonationArchive', on_delete=models.SET_NULL, null=True, blank=True, related_name='donations')

    collector = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='collected_donations')
    validated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='validated_donations')
    validated_at = models.DateTimeField(null=True, blank=True)

    # « Je m'en charge » : un collecteur annonce qu'il est en route. La demande
    # reste `pending`, mais porte déjà son `collector` — les autres collecteurs
    # de la zone la voient prise et ne se déplacent pas pour rien.
    claimed_at = models.DateTimeField(null=True, blank=True)
    collected_at = models.DateTimeField(null=True, blank=True)
    cash_deposit = models.ForeignKey(
        'CashDeposit', on_delete=models.PROTECT, null=True, blank=True, related_name='donations',
    )
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='cancelled_donations')
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)

    external_ref = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    bictorys_ref = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    checkout_url = models.URLField(max_length=500, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Jëf'
        verbose_name_plural = 'Jëfs'
        indexes = [
            models.Index(fields=['external_ref']),
            models.Index(fields=['bictorys_ref']),
            models.Index(fields=['archive_id']),
            models.Index(fields=['payment_status']),
        ]

    def __str__(self):
        campaign_name = self.campaign.name if self.campaign_id else 'campagne'
        return f"Donation {self.id} for {campaign_name}"
