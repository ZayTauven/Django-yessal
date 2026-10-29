from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .models import Donation
from comms.notify import notify_many, nom_de

User = get_user_model()

@receiver(post_save, sender=Donation)
def publier_dans_le_canal(sender, instance, **kwargs):
    """Un Jëf confirmé apparaît dans « Les Jëfs du Kourel ».

    Après validation de la transaction : un paiement annulé par un rollback
    ne doit pas laisser de carte derrière lui. `publier_jef` est idempotent —
    un Jëf re-sauvegardé ne publie pas deux fois.
    """
    if instance.payment_status == Donation.PaymentStatus.CONFIRMED:
        from django.db import transaction
        from comms.canaux import publier_jef
        transaction.on_commit(lambda: publier_jef(instance))


@receiver(post_save, sender=Donation)
def notify_for_collector_payment(sender, instance, created, **kwargs):
    # Une DEMANDE de collecte seulement. Le signal partait aussi pour des
    # espèces déjà dans la main du collecteur, et annonçait « Collecte
    # physique à effectuer » aux autres pour un argent déjà ramassé.
    if (
        created
        and instance.payment_method == Donation.PaymentMethod.MANUAL
        and instance.payment_status == Donation.PaymentStatus.PENDING
    ):
        donor = instance.donor
        campaign = instance.campaign
        daara = donor.daara

        from .services.collecte import collecteurs_de_zone, zone_de

        # Les collecteurs de la ZONE du membre, plus le coordinateur de son
        # Daara. Partait vers TOUS les administrateurs et vers les seuls
        # collecteurs du même Daara — un collecteur couvrant toute la zone
        # n'apprenait rien, et chaque demande réveillait tous les admins.
        # Ceux-ci ne sont plus prévenus qu'en dernier recours : quand personne
        # ne couvre la zone, la demande serait sinon orpheline.
        de_zone = set(collecteurs_de_zone(zone_de(donor)))
        recipients = set(de_zone)
        if daara:
            recipients.update(User.objects.filter(role='chef_daara', daara=daara))
        if not de_zone:
            recipients.update(User.objects.filter(role='admin', is_active=True))

        from .views import montant_lisible

        membre = nom_de(donor)
        montant = montant_lisible(instance.amount)
        titre = "Nouveau Jëf physique à récolter"
        message = (
            f"{membre} a enregistré un Jëf de {montant} FCFA pour "
            f"« {campaign.name} ». Collecte physique à effectuer."
        )

        # Le téléphone figure dans le courriel : le collecteur appelle avant de
        # se déplacer. Il n'a pas sa place dans la notification en base, qui
        # s'affiche dans une liste.
        contexte = {
            'membre': membre,
            'montant': montant,
            'campagne': campaign.name,
            'daara': daara.name if daara else None,
            'telephone': donor.phone or '',
        }

        # En masse, hors requête : envoyés un par un, les courriels aux
        # collecteurs tenaient l'écran du membre sur « Traitement… » (16 s
        # mesurées le 2026-10-28), et son propre accusé partait en dernier.
        # `push=True` : `bulk_create` ne déclenche pas le push du signal.
        notify_many(
            [u for u in recipients if u != donor],
            code='jef_a_collecter',
            titre=titre,
            message=message,
            contexte=contexte,
            push=True,
        )
