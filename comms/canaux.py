"""Les canaux du Kourel : abonnements, auteurs, et publications des Jëfs.

═══════════════════════════════════════════════════════════════════════════
Deux canaux, globaux (décisions du 2026-10-29)
═══════════════════════════════════════════════════════════════════════════
- **Annonces de Sangue Bi** — seuls les comptes désignés par l'administrateur
  (`ChatMembership.can_post`) y publient ; tous lisent et réagissent ; chaque
  annonce part en push.
- **Les Jëfs du Kourel** — personne n'y écrit : le serveur y pose une carte à
  chaque Jëf confirmé. Le nom n'y figure que si le donateur l'a choisi
  (`Donation.public_in_channel`), le montant JAMAIS. Pas de push : un Jëf par
  minute le jour d'un Magal ferait vibrer chaque téléphone sans relâche.

═══════════════════════════════════════════════════════════════════════════
Pourquoi des cartes qui ne se ressemblent pas
═══════════════════════════════════════════════════════════════════════════
Le même gabarit répété cent fois ne se lit plus : on fait défiler une liste de
reçus. Chaque carte tire donc, de façon DÉTERMINISTE (même Jëf → même carte,
partout et à chaque chargement), une mise en forme parmi plusieurs :

    palier     un nombre rond de Jëfs atteint pour le Ndiguel — confettis
    diaspora   le donateur réside hors du Sénégal — pictogramme dédié
    photo      la bannière du Ndiguel, quand elle existe
    picto      un des pictogrammes de la marque, sur un fond pastel

et une formulation parmi plusieurs. Les pictogrammes sont ceux que
l'application embarque déjà (`yessal-mobile/assets/pictos`) : la carte ne
transporte que leur NOM, chaque client affiche sa copie.
"""
import logging

from django.contrib.auth import get_user_model
from django.db import transaction

logger = logging.getLogger(__name__)

#: Le compte au nom duquel le serveur publie. Inactif : invisible dans la
#: recherche de membres, incapable de se connecter.
EMAIL_SYSTEME = 'systeme@yessal.local'

PICTOS = ['generosite', 'main-tendue', 'repas', 'collecte', 'proche', 'appel']
TONS = ['peach', 'yellow', 'teal', 'pink', 'lilac']
PALIERS = {10, 25, 50, 75, 100, 150, 200, 250, 300, 400, 500, 750, 1000}
PAYS_LOCAUX = {'', 'senegal', 'sénégal', 'sn'}

PHRASES_NOMMEES = [
    "{nom} a fait son Jëf pour {ndiguel}.",
    "{nom} répond au Ndiguel « {ndiguel} ».",
    "Un Jëf de plus pour {ndiguel} : merci {prenom} !",
    "{nom} soutient {ndiguel}. Alhamdoulillah.",
]
PHRASES_DIASPORA_NOMMEES = [
    "{nom} envoie son Jëf pour {ndiguel} depuis la diaspora.",
    "De loin, mais présent : {nom} soutient {ndiguel}.",
    "{nom} répond au Ndiguel « {ndiguel} » depuis l'étranger.",
]
PHRASES_DIASPORA_ANONYMES = [
    "Un membre de la diaspora a fait son Jëf pour {ndiguel}.",
    "Depuis la diaspora, un Jëf de plus pour {ndiguel}.",
    "La diaspora répond au Ndiguel « {ndiguel} ».",
]
PHRASES_ANONYMES = [
    "{qui} a fait son Jëf pour {ndiguel}.",
    "Un nouveau Jëf pour {ndiguel}, dans la discrétion.",
    "{qui} répond au Ndiguel « {ndiguel} ».",
]


def canal(kind):
    from .models import Chat
    return Chat.objects.filter(chat_type=Chat.ChatType.CHANNEL, channel_kind=kind).first()


def compte_systeme():
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        email=EMAIL_SYSTEME,
        defaults={'first_name': 'Yessal', 'last_name': '', 'is_active': False},
    )
    return user


def abonner(user):
    """Abonne un nouveau membre aux fils qui s'adressent à lui — à la création
    du compte : les canaux du Kourel entier, et ceux dont l'audience le vise
    (`comms/audience.py`)."""
    from .audience import resynchroniser_membre
    if user.email == EMAIL_SYSTEME:
        return
    resynchroniser_membre(user)


def _hors_senegal(user):
    pays = (getattr(user, 'residence_country', '') or '').strip().lower()
    return pays not in PAYS_LOCAUX


def composer_carte(donation, rang):
    """La carte d'un Jëf : texte et illustration. Pure, donc testable seule.

    `rang` = le nombre de Jëfs confirmés du Ndiguel, celui-ci compris.
    """
    from .notify import nom_de

    donor = donation.donor
    ndiguel = donation.campaign.name
    public = bool(donation.public_in_channel)
    graine = donation.pk or 0

    if public:
        nom = nom_de(donor)
        prenom = (donor.first_name or '').strip() or nom
        texte = PHRASES_NOMMEES[graine % len(PHRASES_NOMMEES)].format(nom=nom, prenom=prenom, ndiguel=ndiguel)
    else:
        daara = getattr(donor, 'daara', None)
        qui = f"Un talubé du Daara {daara.name}" if daara else "Un membre du Kourel"
        texte = PHRASES_ANONYMES[graine % len(PHRASES_ANONYMES)].format(qui=qui, ndiguel=ndiguel)

    if rang in PALIERS or (rang >= 1000 and rang % 500 == 0):
        illustration = {'variant': 'palier', 'milestone': rang}
        texte = f"{rang}e Jëf pour {ndiguel}. Alhamdoulillah !"
    elif _hors_senegal(donor) and graine % 2 == 0:
        # Une fois sur deux seulement : dans un Kourel où la diaspora donne
        # beaucoup, la même carte turquoise revenait six fois sur dix.
        illustration = {'variant': 'diaspora', 'picto': 'diaspora', 'tone': 'teal'}
        phrases = PHRASES_DIASPORA_NOMMEES if public else PHRASES_DIASPORA_ANONYMES
        texte = phrases[(graine // 2) % len(phrases)].format(nom=nom_de(donor), ndiguel=ndiguel)
    elif graine % 3 == 0 and donation.campaign.illustrative_photo:
        illustration = {'variant': 'photo', 'photo_url': donation.campaign.illustrative_photo.url}
    else:
        illustration = {
            'variant': 'picto',
            'picto': PICTOS[(graine // 3) % len(PICTOS)],
            'tone': TONS[graine % len(TONS)],
        }

    auteur = None
    if public:
        titre = getattr(getattr(donor, 'title', None), 'name', None)
        avatar = None
        try:
            avatar = donor.avatar.url if donor.avatar else (donor.avatar_url or None)
        except (ValueError, AttributeError):
            avatar = None
        auteur = {'id': donor.pk, 'name': nom_de(donor), 'title': titre, 'avatar': avatar}

    return texte, {
        'kind': 'jef',
        'donation_id': donation.pk,
        'campaign_id': donation.campaign_id,
        'campaign_name': ndiguel,
        'donor': auteur,
        # Le montant, SEULEMENT si le donateur a accepté d'être nommé ; il ne
        # quitte le serveur que si l'administration l'affiche sur le canal
        # (`MessageSerializer.to_representation`). Un Jëf anonyme n'en a pas.
        **({'amount': int(donation.amount)} if public else {}),
        **illustration,
    }


def publier_jef(donation):
    """Pose la carte d'un Jëf confirmé dans son canal — une seule fois.

    Ne lève jamais : confirmer un paiement compte plus que l'annoncer.
    """
    from contributions.models import Donation
    from .models import Message

    try:
        if donation.payment_status != Donation.PaymentStatus.CONFIRMED:
            return None
        chat = canal('jefs')
        if chat is None:
            return None
        if Message.objects.filter(chat=chat, payload__donation_id=donation.pk).exists():
            return None
        # La PLACE de ce Jëf parmi les confirmés du Ndiguel, et non leur total
        # du jour : publié en retard ou en même temps qu'un autre, chaque Jëf
        # garde son rang — sans quoi trois Jëfs annonçaient tous « 10e Jëf ».
        rang = Donation.objects.filter(
            campaign_id=donation.campaign_id,
            payment_status=Donation.PaymentStatus.CONFIRMED,
            pk__lte=donation.pk,
        ).count()
        texte, payload = composer_carte(donation, rang)
        if payload['variant'] == 'palier' and Message.objects.filter(
            chat=chat, payload__campaign_id=donation.campaign_id, payload__milestone=rang,
        ).exists():
            # Un palier ne s'annonce qu'une fois : la carte reprend une forme ordinaire.
            texte, payload = composer_carte(donation, rang=0)
        with transaction.atomic():
            return Message.objects.create(
                chat=chat,
                sender=compte_systeme(),
                message_type=Message.MessageType.CARD,
                content=texte,
                payload=payload,
            )
    except Exception:
        logger.exception("Carte de Jëf non publiée pour le don %s", getattr(donation, 'pk', '?'))
        return None
