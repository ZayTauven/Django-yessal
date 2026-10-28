from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .models import Donation
from comms.notify import notify, nom_de

User = get_user_model()

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

        for destinataire in recipients:
            if destinataire == donor:
                continue
            notify(
                destinataire,
                code='jef_a_collecter',
                titre=titre,
                message=message,
                contexte=contexte,
            )
