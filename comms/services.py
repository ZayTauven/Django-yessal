import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.db import close_old_connections, transaction

logger = logging.getLogger(__name__)

#: Les envois vers Pusher et Firebase partent d'ici, hors de la requête.
#:
#: Mesuré le 2026-10-29 : envoyer un message prenait 382 ms (jusqu'à 3 s),
#: réagir 303 ms — contre 40 et 27 ms sans les appels Pusher, qui étaient
#: faits PENDANT la requête. L'expéditeur attendait l'aller-retour vers les
#: serveurs de Pusher avant de voir son message parti.
#:
#: Une petite réserve de fils plutôt qu'un fil par événement : un salon actif
#: le jour d'un Magal en créait des centaines.
_FILE_ENVOIS = ThreadPoolExecutor(max_workers=4, thread_name_prefix='yessal-temps-reel')

#: Firebase ne s'initialise qu'une fois par processus — et deux pushes
#: simultanés le tentaient tous les deux : « The default Firebase app already
#: exists », et le second push était perdu (constaté le 2026-10-29).
_VERROU_FIREBASE = threading.Lock()

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

#: Au-delà de cet effectif, un salon ne pousse plus chaque message sur les
#: téléphones : seulement les réponses et les mentions. Un salon de Daara de
#: 400 talubés qui vibre à chaque « Amine » se fait mettre en sourdine — et
#: avec lui, le message qui comptait vraiment.
SEUIL_GRAND_SALON = 50

#: Le canal Pusher que tout membre connecté écoute : les nouvelles des canaux
#: du Kourel y passent une fois pour tous.
CANAL_COMMUN = 'private-kourel'

#: Pusher accepte au plus 100 canaux par appel.
PUSHER_CANAUX_PAR_APPEL = 100


def lancer_en_arriere_plan(fn):
    """Exécute `fn` hors de la requête. Point unique, que les tests remplacent."""
    def _tache():
        # Un fil de la réserve garde sa connexion d'un envoi à l'autre : on
        # la renouvelle si elle a vieilli, comme le fait Django entre deux
        # requêtes.
        close_old_connections()
        try:
            fn()
        except Exception:
            logger.exception("Envoi temps réel en échec")
        finally:
            close_old_connections()

    _FILE_ENVOIS.submit(_tache)


def publier(channel, event, payload):
    """Un événement Pusher, envoyé APRÈS validation de la transaction.

    Après : un client qui reçoit l'événement et recharge aussitôt le fil doit
    trouver le message en base. Hors requête : l'expéditeur n'attend pas.
    """
    transaction.on_commit(lambda: lancer_en_arriere_plan(lambda: trigger_pusher(channel, event, payload)))


def trigger_pusher(channel, event, payload):
    """
    Publie un evenement Pusher sans bloquer l'ecriture metier si Pusher est
    indisponible ou mal configure en local.
    """
    if not pusher_client:
        return False
    try:
        pusher_client.trigger(channel, event, payload)
    except Exception as exc:
        # Ne jamais faire échouer l'envoi d'un message — mais ne plus se taire :
        # le 2026-10-30, l'app Pusher de développement avait disparu et AUCUN
        # événement ne partait, sans une ligne dans les journaux. Le temps réel
        # semblait marcher, seulement parce que l'écran se rechargeait au focus.
        logger.warning("Pusher refuse l'événement %s sur %s : %s", event, channel, exc)
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

        with _VERROU_FIREBASE:
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


def note_systeme(chat, auteur, texte):
    """Une ligne de service dans le fil : « X a rejoint », « Discussion démarrée ».

    Elle n'est la parole de personne ; `auteur` n'est là que parce que
    `Message.sender` est obligatoire.
    """
    from .models import Message
    return Message.objects.create(
        chat=chat, sender=auteur, message_type=Message.MessageType.SYSTEM, content=texte,
    )


def destinataires_push(message):
    """Qui reçoit un push pour ce message — la règle produit du 2026-10-29.

    - jamais l'auteur, jamais un fil en sourdine, jamais qui a coupé ses
      notifications de messagerie ;
    - tête-à-tête et salons de ``SEUIL_GRAND_SALON`` membres au plus : chacun ;
    - grands salons, ou membre en « mentions seulement » : seulement la
      personne à qui l'on RÉPOND, ou que l'on MENTIONNE (« @Prénom »).
    """
    from .models import Chat, ChatMembership

    chat = message.chat
    if chat.chat_type == Chat.ChatType.CHANNEL and chat.channel_kind != Chat.ChannelKind.GUIDE:
        # « Les Jëfs du Kourel » ne sonne jamais : un Jëf par minute le jour
        # d'un Magal ferait vibrer chaque téléphone sans relâche.
        return []

    memberships = list(
        ChatMembership.objects.filter(chat_id=message.chat_id)
        .exclude(user_id=message.sender_id)
        .select_related('user', 'user__messaging_preferences')
    )
    # Une annonce du guide atteint tout le monde, quel que soit l'effectif.
    canal_du_guide = chat.chat_type == Chat.ChatType.CHANNEL
    grand = not canal_du_guide and (
        message.chat.chat_type == Chat.ChatType.GROUP
        and len(memberships) + 1 > SEUIL_GRAND_SALON
    )
    texte = (message.content or '').lower()
    repondu = message.reply_to.sender_id if message.reply_to_id and message.reply_to else None

    def vise(user):
        if user.id == repondu:
            return True
        prenom = (user.first_name or '').strip().lower()
        return bool(prenom) and f'@{prenom}' in texte

    elus = []
    for m in memberships:
        if m.is_muted or not m.user.is_active:
            continue
        prefs = getattr(m.user, 'messaging_preferences', None)
        if prefs is not None and not prefs.notifications_enabled:
            continue
        mentions_seules = grand or (
            not canal_du_guide and prefs is not None and prefs.notify_on_mention_only
        )
        if mentions_seules and not vise(m.user):
            continue
        elus.append(m.user_id)
    return elus


def diffuser_message(message):
    """Tout ce qu'un nouveau message déclenche hors de son propre fil.

    1. `chat-updated` sur le canal PERSONNEL de chaque membre : la liste des
       conversations remonte le fil et incrémente sa pastille sans recharger.
       Jusqu'ici seul le fil OUVERT écoutait ; un message dans un autre salon
       restait invisible jusqu'au rafraîchissement de la page.
    2. Le push FCM, selon `destinataires_push`.

    Les destinataires se calculent ICI, dans la requête — la transaction
    voit le message. Les appels réseau partent ensuite en arrière-plan.
    """
    from .models import Chat, ChatMembership
    from .serializers import apercu_message
    from .notify import nom_de

    chat = message.chat
    est_canal = chat.chat_type == Chat.ChatType.CHANNEL
    # Un canal compte tout le Kourel : un seul événement sur le canal commun
    # plutôt que des milliers de canaux personnels.
    membres = [] if est_canal else list(
        ChatMembership.objects.filter(chat_id=message.chat_id).values_list('user_id', flat=True)
    )
    pousses = destinataires_push(message)

    auteur = nom_de(message.sender)
    apercu = apercu_message(message)
    if chat.chat_type == Chat.ChatType.DIRECT:
        titre, corps = auteur, apercu
    else:
        titre, corps = (chat.name or 'Salon'), f'{auteur} : {apercu}'
    corps = corps if len(corps) <= 140 else corps[:139] + '…'

    resume = {
        'chat_id': message.chat_id,
        'message_id': message.id,
        'last_message': {
            'content': apercu,
            'message_type': message.message_type,
            'sent_at': message.sent_at.isoformat(),
            'sender_name': auteur,
            'sender_id': message.sender_id,
        },
    }
    donnees = {'url': f'/dashboard/chat/{message.chat_id}', 'chat_id': message.chat_id, 'type': 'chat_message'}

    def _envoyer():
        if est_canal:
            trigger_pusher(CANAL_COMMUN, 'chat-updated', resume)
        canaux = [f'private-user.{uid}' for uid in membres]
        for i in range(0, len(canaux), PUSHER_CANAUX_PAR_APPEL):
            trigger_pusher(canaux[i:i + PUSHER_CANAUX_PAR_APPEL], 'chat-updated', resume)
        for uid in pousses:
            send_fcm_push(uid, titre, corps, data=donnees)

    transaction.on_commit(lambda: lancer_en_arriere_plan(_envoyer))
