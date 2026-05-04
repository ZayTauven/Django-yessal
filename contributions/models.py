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

    class PaymentStatus(models.TextChoices):
        PENDING = 'pending', _('Pending')
        PENDING_WIRE = 'pending_wire', _('Virement en attente')
        CONFIRMED = 'confirmed', _('Confirmed')
        FAILED = 'failed', _('Failed')

    campaign = models.ForeignKey('events.Campaign', on_delete=models.CASCADE, related_name='donations')
    donor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='donations_made')
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
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.MANUAL)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    wire_reference = models.CharField(max_length=100, blank=True, help_text='Référence de virement saisie par le membre')
    archive_id = models.ForeignKey('DonationArchive', on_delete=models.SET_NULL, null=True, blank=True, related_name='donations')

    collector = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='collected_donations')
    validated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='validated_donations')
    validated_at = models.DateTimeField(null=True, blank=True)

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
