from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .models import Donation
from comms.models import Notification

User = get_user_model()

@receiver(post_save, sender=Donation)
def notify_for_collector_payment(sender, instance, created, **kwargs):
    if created and instance.payment_method == 'collector':
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
        
        # Create notifications
        title = "Nouveau Jëf physique à récolter"
        message = f"Le membre {donor.get_full_name()} a enregistré un Jëf de {instance.amount} FCFA pour la campagne {campaign.name}. Veuillez procéder à la collecte physique."
        
        notifications = [
            Notification(user=recipient, title=title, message=message)
            for recipient in recipients if recipient != donor
        ]
        
        if notifications:
            Notification.objects.bulk_create(notifications)
