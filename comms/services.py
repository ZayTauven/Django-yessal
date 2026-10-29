import logging

from django.conf import settings

logger = logging.getLogger(__name__)

#: Le canal de notification Android créé par l'application mobile — à garder
#: identique à `setNotificationChannelAsync("default", …)` dans
#: `yessal-mobile/lib/push.service.ts` et à `defaultChannel` dans `app.json`.
ANDROID_CHANNEL_ID = 'default'

try:
    import pusher
except ImportError:
    pusher = None

# Initialisation du client Pusher.
#
# `pusher.Pusher(app_id='')` lève `ValueError: Invalid app id` DANS le
# constructeur, à l'import du module. Le garde-fou `if pusher` ne couvrait que
# l'absence de la bibliothèque, pas l'absence d'identifiants — or c'est le cas
# courant : un développeur qui clone le dépôt sans compte Pusher voyait Django
# refuser de démarrer, alors que `trigger_pusher` sait déjà se taire quand le
# client est absent. On teste donc aussi la configuration (PUSHER_ENABLED).
pusher_client = (
    pusher.Pusher(
        app_id=settings.PUSHER_APP_ID,
        key=settings.PUSHER_KEY,
        secret=settings.PUSHER_SECRET,
        cluster=settings.PUSHER_CLUSTER,
        ssl=settings.PUSHER_SSL,
    )
    if pusher and getattr(settings, 'PUSHER_ENABLED', False)
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
            cred = credentials.Certificate(str(cred_path))
            firebase_admin.initialize_app(cred)

        from .models import FCMToken
        tokens = list(FCMToken.objects.filter(user_id=user_id).values_list('token', flat=True))
        if not tokens:
            return

        multicast = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            # Le canal « Yessal Gui » que l'application crée au démarrage
            # (`lib/push.service.ts`). Sans lui, expo-notifications range le
            # push dans son canal de repli, et c'est ce nom-là que le membre
            # voit dans les réglages de son téléphone (constaté le 2026-10-29).
            # Ignoré pour les jetons web.
            android=messaging.AndroidConfig(
                notification=messaging.AndroidNotification(channel_id=ANDROID_CHANNEL_ID),
            ),
            tokens=tokens,
        )
        batch_response = messaging.send_each_for_multicast(multicast)

        # Les jetons DÉFINITIVEMENT morts sortent de la base : application
        # désinstallée ou réinstallée (`UnregisteredError`), ou jeton émis pour
        # un autre projet Firebase (`SenderIdMismatchError`).
        #
        # On teste la CLASSE, pas le code. Le filtre précédent attendait
        # 'registration-token-not-registered', nom de l'ancien SDK ;
        # firebase_admin 7 rend `code='NOT_FOUND'`. Rien n'était jamais purgé :
        # en production le 2026-10-29, 3 jetons sur 10 échouaient à chaque envoi.
        #
        # Surtout PAS `InvalidArgumentError` : il désigne aussi une charge mal
        # formée, et purger sur lui viderait d'un coup tous les jetons du membre.
        morts = (messaging.UnregisteredError, messaging.SenderIdMismatchError)
        invalid_tokens = [
            tokens[idx]
            for idx, resp in enumerate(batch_response.responses)
            if not resp.success and isinstance(resp.exception, morts)
        ]
        if invalid_tokens:
            FCMToken.objects.filter(token__in=invalid_tokens).delete()
    except Exception:
        # On n'interrompt JAMAIS le flux principal pour un push. Mais on ne le
        # TAIT plus : c'est ce silence qui a laissé passer un montage Docker
        # cassé — la clé de service manquait, chaque envoi levait, et rien
        # nulle part ne le disait. Un push qui échoue en silence est pire
        # qu'un push absent : on croit la fonction livrée.
        logger.exception("Envoi FCM impossible pour l'utilisateur %s", user_id)


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
