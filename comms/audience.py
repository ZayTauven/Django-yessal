"""À qui s'adresse un fil — canaux ciblés et salons par titre.

Arbitrages du 2026-10-30 :

- un canal créé par l'administration s'adresse à tout le Kourel, ou à une
  audience ciblée (titre, LDD, Daara, rôle) ;
- un salon créé à partir d'un titre SUIT ce titre : qui le reçoit y entre, qui
  le perd en sort ;
- un ajout à la main n'est jamais retiré par la synchronisation
  (`ChatMembership.par_audience` le distingue).

Une audience est un dict de listes — `{"titles": [3], "ldds": [], "daaras": [],
"roles": ["chef_daara"]}`. Les catégories se combinent en ET (« les Serigne de
la LDD X »), les valeurs d'une même catégorie en OU (« les Serigne ou les
Sokhna »). Une audience vide, ou `None` pour un canal, c'est tout le Kourel.
"""
from django.contrib.auth import get_user_model
from rest_framework.exceptions import ValidationError

CRITERES = ('titles', 'ldds', 'daaras', 'roles')

#: Les icônes qu'un administrateur peut donner à un canal. Liste FERMÉE : le
#: web (lucide-react) et le mobile (lucide-react-native) dessinent chacune.
ICONES = (
    'megaphone', 'hand-heart', 'book-open', 'calendar', 'users',
    'star', 'bell', 'heart', 'graduation-cap', 'sparkles', 'moon', 'landmark',
)


def normaliser(brut):
    """Valide une audience reçue du client ; `None` pour « tout le Kourel »."""
    from accounts.models import LDD, Daara, MemberTitle

    if brut in (None, '', {}):
        return None
    if not isinstance(brut, dict):
        raise ValidationError({'audience': "L'audience doit être un objet."})
    inconnues = set(brut) - set(CRITERES) - {'tout'}
    if inconnues:
        raise ValidationError({'audience': f"Critère inconnu : {', '.join(sorted(inconnues))}."})
    if brut.get('tout'):
        return None

    User = get_user_model()
    modeles = {'titles': MemberTitle, 'ldds': LDD, 'daaras': Daara}
    propre = {}
    for cle in CRITERES:
        valeurs = brut.get(cle) or []
        if not isinstance(valeurs, list):
            raise ValidationError({'audience': f"« {cle} » doit être une liste."})
        if cle == 'roles':
            permis = {r for r, _ in User.Role.choices}
            if set(valeurs) - permis:
                raise ValidationError({'audience': "Rôle inconnu dans l'audience."})
            propre[cle] = sorted(set(valeurs))
        else:
            try:
                ids = sorted({int(v) for v in valeurs})
            except (TypeError, ValueError):
                raise ValidationError({'audience': f"« {cle} » attend des identifiants."})
            if modeles[cle].objects.filter(pk__in=ids).count() != len(ids):
                raise ValidationError({'audience': f"Un élément de « {cle} » n'existe pas."})
            propre[cle] = ids
    if not any(propre.values()):
        return None
    return propre


def membres_de(audience):
    """Les comptes visés, actifs, hors compte système."""
    from .canaux import EMAIL_SYSTEME

    User = get_user_model()
    qs = User.objects.filter(is_active=True).exclude(email=EMAIL_SYSTEME)
    if not audience:
        return qs
    if audience.get('titles'):
        qs = qs.filter(title_id__in=audience['titles'])
    if audience.get('ldds'):
        qs = qs.filter(daara__ldd_id__in=audience['ldds'])
    if audience.get('daaras'):
        qs = qs.filter(daara_id__in=audience['daaras'])
    if audience.get('roles'):
        qs = qs.filter(role__in=audience['roles'])
    return qs


def correspond(user, audience):
    """Le membre fait-il partie de l'audience ? Sans requête pour un canal global."""
    from .canaux import EMAIL_SYSTEME

    if not user.is_active or user.email == EMAIL_SYSTEME:
        return False
    if not audience:
        return True
    if audience.get('titles') and user.title_id not in audience['titles']:
        return False
    if audience.get('daaras') and user.daara_id not in audience['daaras']:
        return False
    if audience.get('roles') and user.role not in audience['roles']:
        return False
    if audience.get('ldds'):
        ldd_id = user.daara.ldd_id if user.daara_id else None
        if ldd_id not in audience['ldds']:
            return False
    return True


def suit_une_audience(chat):
    """Ce fil recalcule-t-il ses membres ? Tout canal, et les salons ciblés."""
    from .models import Chat

    return chat.chat_type == Chat.ChatType.CHANNEL or (
        chat.chat_type == Chat.ChatType.GROUP and bool(chat.audience)
    )


def synchroniser(chat):
    """Aligne les membres d'un fil sur son audience. Renvoie (ajoutés, retirés).

    Ne retire JAMAIS un membre ajouté à la main, ni un auteur désigné du canal.
    """
    from .models import ChatMembership

    if not suit_une_audience(chat):
        return 0, 0
    vises = set(membres_de(chat.audience).values_list('pk', flat=True))
    presents = dict(ChatMembership.objects.filter(chat=chat).values_list('user_id', 'par_audience'))
    nouveaux = vises - set(presents)
    ChatMembership.objects.bulk_create(
        [ChatMembership(chat=chat, user_id=uid, par_audience=True) for uid in nouveaux],
        ignore_conflicts=True,
    )
    partants = [uid for uid, par_audience in presents.items() if par_audience and uid not in vises]
    retires = 0
    if partants:
        retires, _ = ChatMembership.objects.filter(
            chat=chat, user_id__in=partants, par_audience=True, can_post=False,
        ).delete()
    return len(nouveaux), retires


def resynchroniser_membre(user):
    """Un membre a changé (titre, Daara, rôle, statut) : ses fils ciblés suivent."""
    from .models import Chat, ChatMembership

    fils = Chat.objects.filter(chat_type=Chat.ChatType.CHANNEL) | Chat.objects.filter(
        chat_type=Chat.ChatType.GROUP, audience__isnull=False,
    )
    presences = dict(ChatMembership.objects.filter(user=user).values_list('chat_id', 'par_audience'))
    for chat in fils.distinct():
        if chat.chat_type == Chat.ChatType.GROUP and not chat.audience:
            continue
        dedans = correspond(user, chat.audience)
        if dedans and chat.pk not in presences:
            ChatMembership.objects.get_or_create(chat=chat, user=user, defaults={'par_audience': True})
        elif not dedans and presences.get(chat.pk):
            ChatMembership.objects.filter(
                chat=chat, user=user, par_audience=True, can_post=False,
            ).delete()
