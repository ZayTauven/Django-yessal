from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .models import Donation
from comms.notify import notify, nom_de

User = get_user_model()

@receiver(post_save, sender=Donation)
def notify_for_collector_payment(sender, instance, created, **kwargs):
    if created and instance.payment_method in {'collector', 'manual'}:
        donor = instance.donor
        campaign = instance.campaign
        daara = donor.daara
        
        recipients = set()
        
        # 1. Admins
        admins = User.objects.filter(role='admin')
        for admin in admins:
            recipients.add(admin)
            
        if daara:
            # 2. Daara Chief
            chiefs = User.objects.filter(role='chef_daara', daara=daara)
            for chief in chiefs:
                recipients.add(chief)
                
            # 3. Collectors of the Daara
            collectors = User.objects.filter(role='collector', daara=daara)
            for collector in collectors:
                recipients.add(collector)
        
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
