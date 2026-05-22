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

def send_fcm_push(user_id: int, title: str, body: str, data: dict | None = None) -> None:
    """
    Sends an FCM push notification to all registered tokens for a user.
    Requires FIREBASE_CREDENTIALS_PATH in Django settings pointing to a
    service-account JSON downloaded from Firebase Console → Project Settings → Service Accounts.
    Silently no-ops if firebase-admin is not installed or not configured.
    """
    try:
        import firebase_admin
        from firebase_admin import credentials, messaging

        if not firebase_admin._apps:
            cred_path = getattr(settings, 'FIREBASE_CREDENTIALS_PATH', None)
            if not cred_path:
                return
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)

        from .models import FCMToken
        tokens = list(FCMToken.objects.filter(user_id=user_id).values_list('token', flat=True))
        if not tokens:
            return

        multicast = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            tokens=tokens,
        )
        batch_response = messaging.send_each_for_multicast(multicast)

        # Remove invalidated tokens
        invalid_tokens = []
        for idx, resp in enumerate(batch_response.responses):
            if not resp.success and resp.exception:
                code = getattr(resp.exception, 'code', '')
                if code in ('registration-token-not-registered', 'invalid-argument'):
                    invalid_tokens.append(tokens[idx])
        if invalid_tokens:
            FCMToken.objects.filter(token__in=invalid_tokens).delete()
    except Exception:
        pass  # Never block the main flow


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
