from django.conf import settings

try:
    import pusher
except ImportError:
    pusher = None

# Initialisation du client Pusher
pusher_client = (
    pusher.Pusher(
        app_id=settings.PUSHER_APP_ID,
        key=settings.PUSHER_KEY,
        secret=settings.PUSHER_SECRET,
        cluster=settings.PUSHER_CLUSTER,
        ssl=settings.PUSHER_SSL,
    )
    if pusher
    else None
)

def trigger_pusher(channel, event, payload):
    """
    Publie un evenement Pusher sans bloquer l'ecriture metier si Pusher est
    indisponible ou mal configure en local.
    """
    if not pusher_client:
        return False
    try:
        pusher_client.trigger(channel, event, payload)
    except Exception:
        return False
    return True

def get_effective_config(user):
    """
    Retourne la MessagingPilotageConfig applicable à cet utilisateur.
    Priorité : config de sa Daara > config globale > valeurs par défaut (si pas en DB).
    """
    from .models import MessagingPilotageConfig

    daara = getattr(user, 'daara', None)
    if daara:
        config = MessagingPilotageConfig.objects.filter(daara=daara).first()
        if config:
            return config

    global_config = MessagingPilotageConfig.objects.filter(daara=None).first()
    if global_config:
        return global_config

    # Fallback si aucun objet n'existe du tout
    return MessagingPilotageConfig()

def can_invite(sender, recipient):
    """
    Détermine si sender peut inviter recipient à discuter (ou démarrer un chat).
    Vérifie les configurations de pilotage ainsi que les préférences de visibilité et d'invitations du destinataire.
    """
    from .models import UserMessagingPreferences, MessagingPilotageConfig

    # Si c'est l'utilisateur lui-même, on refuse d'auto-inviter
    if sender == recipient:
        return False

    prefs, _ = UserMessagingPreferences.objects.get_or_create(user=recipient)

    # 1. Vérification des préférences individuelles du destinataire
    if not prefs.allow_direct_invites:
        return False

    if prefs.visibility == UserMessagingPreferences.Visibility.NOBODY:
        return False

    sender_daara = getattr(sender, 'daara', None)
    recipient_daara = getattr(recipient, 'daara', None)
    same_daara = (sender_daara is not None and sender_daara == recipient_daara)

    if prefs.visibility == UserMessagingPreferences.Visibility.DAARA_ONLY and not same_daara:
        return False

    # 2. Vérification de la configuration de pilotage en cascade
    # Priorité au pilotage de la Daara du sender
    config = get_effective_config(sender)

    if not config.allow_member_invite:
        return False

    if not same_daara and not config.allow_cross_daara_search:
        return False

    return True
