"""Traduit une entrée du journal d'audit en phrase lisible par un administrateur.

Le middleware enregistre la requête brute (`PATCH /api/users/12/`). C'est une
bonne preuve, mais une mauvaise lecture : l'écran affichait ce chemin tel quel,
et ne pouvait dire ni ce qui s'était passé ni sur qui.

La traduction se fait à la LECTURE, pas à l'écriture : elle s'applique ainsi à
tout l'historique déjà enregistré, sans migration de données, et l'entrée brute
reste intacte — un registre d'audit ne se réécrit pas.

`routine` sépare les gestes de la vie courante (un message, une réaction, un
jeton d'appareil) des actes de gestion. Rien n'est effacé : l'écran les masque
par défaut et les rend d'un clic. Une route inconnue n'est JAMAIS routinière :
mieux vaut une ligne en trop qu'un acte qui passe inaperçu.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from django.apps import apps
from django.db.models import Q

ARGENT = 'argent'
MEMBRES = 'membres'
NDIGUELS = 'ndiguels'
ACTUALITES = 'actualites'
ORGANISATION = 'organisation'
REGLAGES = 'reglages'
MESSAGERIE = 'messagerie'
AUTRE = 'autre'

WRITE = ('PUT', 'PATCH')


@dataclass(frozen=True)
class Rule:
    methods: tuple[str, ...]
    pattern: re.Pattern
    domain: str
    label: str
    target: str | None = None
    routine: bool = False
    tone: str | None = None


def _rule(methods, pattern, domain, label, target=None, routine=False, tone=None):
    if isinstance(methods, str):
        methods = (methods,)
    return Rule(tuple(methods), re.compile(rf'^{pattern}$'), domain, label, target, routine, tone)


def _crud(prefix, domain, target, created, updated, deleted):
    """Les trois lignes d'une ressource REST ordinaire.

    Une création n'a pas d'identifiant dans son chemin : sa cible vient du champ
    `entity_id`, que le middleware remplit à partir de la réponse.
    """
    return [
        _rule('POST', f'{prefix}/', domain, created, target),
        _rule(WRITE, rf'{prefix}/(?P<id>[^/]+)/', domain, updated, target),
        _rule('DELETE', rf'{prefix}/(?P<id>[^/]+)/', domain, deleted, target),
    ]


# Les chemins s'entendent sans le préfixe `/api/`. L'ordre compte : la route
# générique `comms/<id>/` passe après `comms/messages/`, `comms/channels/`, etc.
RULES: list[Rule] = [
    # ── Argent ──────────────────────────────────────────────────────────────
    _rule('POST', r'contributions/', ARGENT, 'Jëf enregistré', 'donation'),
    _rule('POST', r'contributions/admin/confirm-cash/', ARGENT, "Remise d'espèces confirmée"),
    _rule('POST', r'contributions/admin/create-archive/', ARGENT, 'Archive des Jëfs créée'),
    _rule(WRITE, r'contributions/(?P<id>\d+)/', ARGENT, 'Jëf modifié', 'donation'),
    _rule('DELETE', r'contributions/(?P<id>\d+)/', ARGENT, 'Jëf supprimé', 'donation'),
    _rule('POST', r'contributions/(?P<id>\d+)/admin/cancel/', ARGENT, 'Jëf annulé', 'donation', tone='warning'),
    _rule('POST', r'contributions/(?P<id>\d+)/admin/confirm-wire/', ARGENT, 'Virement confirmé', 'donation'),
    _rule('POST', r'contributions/(?P<id>\d+)/claim/', ARGENT, 'Collecte prise en charge', 'donation'),
    _rule('POST', r'contributions/(?P<id>\d+)/release/', ARGENT, 'Collecte abandonnée', 'donation'),
    _rule('POST', r'contributions/(?P<id>\d+)/collect/', ARGENT, 'Espèces encaissées', 'donation'),
    _rule('POST', r'contributions/(?P<id>\d+)/pay/', ARGENT, 'Paiement lancé', 'donation'),

    # ── Membres ─────────────────────────────────────────────────────────────
    _rule('POST', r'users/import-excel/', MEMBRES, 'Membres importés'),
    _rule('POST', r'users/(?P<id>\d+)/block/', MEMBRES, 'Compte bloqué', 'user', tone='warning'),
    _rule('POST', r'users/(?P<id>\d+)/validate/', MEMBRES, 'Compte validé', 'user'),
    _rule('POST', r'users/(?P<id>\d+)/documents/', MEMBRES, "Pièce d'identité ajoutée", 'user'),
    _rule(WRITE, r'users/(?P<id>\d+)/documents/\d+/', MEMBRES, "Pièce d'identité modifiée", 'user'),
    _rule(WRITE, r'admin/documents/(?P<id>\d+)/validate/', MEMBRES, "Pièce d'identité examinée", 'document'),
    *_crud('users', MEMBRES, 'user', 'Compte créé', 'Compte modifié', 'Compte supprimé'),
    _rule('POST', r'members/(?P<id>\d+)/assign-title/', MEMBRES, 'Titre attribué', 'user'),
    _rule('POST', r'directory/users/(?P<id>\d+)/promote-collector/', MEMBRES, 'Collecteur nommé', 'user'),
    _rule('POST', r'collecteurs/(?P<id>\d+)/zones/', MEMBRES, "Zones d'un collecteur modifiées", 'user'),
    *_crud('titles', MEMBRES, 'title', 'Titre créé', 'Titre modifié', 'Titre supprimé'),
    _rule(WRITE, r'title-requests/(?P<id>\d+)/review/', MEMBRES, 'Demande de titre examinée', 'title_request'),
    *_crud(
        'title-requests', MEMBRES, 'title_request',
        'Demande de titre envoyée', 'Demande de titre modifiée', 'Demande de titre retirée',
    ),
    *_crud('tutelles', MEMBRES, 'tutelle', 'Tutelle créée', 'Tutelle modifiée', 'Tutelle supprimée'),
    _rule('POST', r'auth/change-password/', MEMBRES, 'Mot de passe changé'),
    _rule('POST', r'profile/daara/', MEMBRES, 'Daara de rattachement choisi'),
    _rule(WRITE, r'profile/', MEMBRES, 'Profil personnel modifié', routine=True),

    # ── Ndiguels ────────────────────────────────────────────────────────────
    _rule('POST', r'events/campaigns/(?P<id>\d+)/confirmer-date/', NDIGUELS, 'Date du Ndiguel confirmée', 'campaign'),
    *_crud('events/campaigns', NDIGUELS, 'campaign', 'Ndiguel créé', 'Ndiguel modifié', 'Ndiguel supprimé'),
    *_crud(
        'events/campaign-todos', NDIGUELS, 'todo',
        "Tâche d'un Ndiguel ajoutée", "Tâche d'un Ndiguel modifiée", "Tâche d'un Ndiguel supprimée",
    ),
    _rule('POST', r'events/fetes/(?P<id>\d+)/prochaine-edition/', NDIGUELS, 'Prochaine édition préparée', 'fete'),
    *_crud(
        'events/fetes', NDIGUELS, 'fete',
        'Ndiguel récurrent créé', 'Ndiguel récurrent modifié', 'Ndiguel récurrent supprimé',
    ),

    # ── Actualités ──────────────────────────────────────────────────────────
    _rule('POST', r'news/posts/(?P<id>[^/]+)/gallery/', ACTUALITES, "Photos ajoutées à l'actualité", 'news'),
    *_crud('news/posts', ACTUALITES, 'news', 'Actualité créée', 'Actualité modifiée', 'Actualité supprimée'),
    _rule('POST', r'news/gallery/', ACTUALITES, 'Photo ajoutée à une galerie'),
    _rule(WRITE, r'news/gallery/\d+/', ACTUALITES, "Photo d'une galerie modifiée"),
    _rule('DELETE', r'news/gallery/\d+/', ACTUALITES, "Photo retirée d'une galerie"),

    # ── Organisation ────────────────────────────────────────────────────────
    _rule('POST', r'ldd/(?P<id>\d+)/transferer-daaras/', ORGANISATION, 'Daaras transférés', 'ldd'),
    *_crud('ldd', ORGANISATION, 'ldd', 'LDD créée', 'LDD modifiée', 'LDD supprimée'),
    _rule('POST', r'daara/import-excel/', ORGANISATION, 'Daaras importés'),
    *_crud('daara', ORGANISATION, 'daara', 'Daara créé', 'Daara modifié', 'Daara supprimé'),

    # ── Réglages ────────────────────────────────────────────────────────────
    # L'interrupteur écrit lui-même une entrée détaillée (`collecte_suspendue`,
    # `collecte_reprise`) : sa trace HTTP ferait doublon, on la range en routine.
    _rule('POST', r'settings/pilotage/collecte/', REGLAGES, 'Interrupteur de la collecte actionné', routine=True),
    _rule('POST', r'settings/pilotage/collecte/code/', REGLAGES, 'Code de vérification de la collecte demandé'),
    _rule('POST', r'settings/pilotage/', REGLAGES, 'Réglages de pilotage modifiés'),
    _rule(WRITE, r'comms/pilotage/', REGLAGES, 'Réglages de la messagerie modifiés'),

    # ── Messagerie : gestion ────────────────────────────────────────────────
    *_crud(
        'comms/announcements', MESSAGERIE, None,
        'Annonce publiée', 'Annonce modifiée', 'Annonce supprimée',
    ),
    _rule('POST', r'comms/channels/', MESSAGERIE, 'Canal créé', 'chat'),
    _rule('DELETE', r'comms/messages/\d+/', MESSAGERIE, 'Message supprimé'),
    _rule(WRITE, r'comms/(?P<id>\d+)/settings/', MESSAGERIE, 'Réglages du salon modifiés', 'chat'),
    _rule('POST', r'comms/(?P<id>\d+)/moderators/', MESSAGERIE, 'Modérateurs du salon modifiés', 'chat'),
    _rule('POST', r'comms/(?P<id>\d+)/authors/', MESSAGERIE, 'Auteurs du canal modifiés', 'chat'),
    _rule('POST', r'comms/(?P<id>\d+)/add-members/', MESSAGERIE, 'Membres ajoutés au salon', 'chat'),
    _rule('POST', r'comms/(?P<id>\d+)/remove-member/', MESSAGERIE, 'Membre retiré du salon', 'chat'),
    _rule('POST', r'comms/(?P<id>\d+)/archive/', MESSAGERIE, 'Salon archivé ou réactivé', 'chat'),
    _rule('POST', r'comms/(?P<id>\d+)/pin/', MESSAGERIE, 'Message épinglé ou désépinglé', 'chat'),
    _rule(WRITE, r'comms/(?P<id>\d+)/', MESSAGERIE, 'Salon modifié', 'chat'),
    _rule('DELETE', r'comms/(?P<id>\d+)/', MESSAGERIE, 'Salon supprimé', 'chat'),

    # ── Messagerie : vie courante ───────────────────────────────────────────
    _rule('POST', r'comms/', MESSAGERIE, 'Discussion ouverte', routine=True),
    _rule('POST', r'comms/messages/', MESSAGERIE, 'Message envoyé', routine=True),
    _rule(WRITE, r'comms/messages/\d+/', MESSAGERIE, 'Message modifié', routine=True),
    _rule('POST', r'comms/messages/\d+/react/', MESSAGERIE, 'Réaction à un message', routine=True),
    _rule('POST', r'comms/\d+/read/', MESSAGERIE, 'Discussion lue', routine=True),
    _rule('POST', r'comms/\d+/clear/', MESSAGERIE, 'Discussion effacée pour soi', routine=True),
    _rule('POST', r'comms/\d+/leave/', MESSAGERIE, 'Discussion quittée', routine=True),
    _rule('POST', r'comms/\d+/mute/', MESSAGERIE, "Notifications d'une discussion réglées", routine=True),
    _rule('POST', r'comms/invitations/', MESSAGERIE, 'Invitation à discuter envoyée', routine=True),
    _rule(WRITE, r'comms/invitations/\d+/', MESSAGERIE, 'Invitation à discuter modifiée', routine=True),
    _rule('DELETE', r'comms/invitations/\d+/', MESSAGERIE, 'Invitation à discuter retirée', routine=True),
    _rule('POST', r'comms/invitations/\d+/accept/', MESSAGERIE, 'Invitation à discuter acceptée', routine=True),
    _rule('POST', r'comms/invitations/\d+/decline/', MESSAGERIE, 'Invitation à discuter refusée', routine=True),
    _rule(WRITE, r'comms/preferences/', MESSAGERIE, 'Préférences de messagerie modifiées', routine=True),
    _rule('POST', r'comms/pusher/auth/', MESSAGERIE, 'Connexion au temps réel', routine=True),
    _rule(('POST', 'DELETE'), r'comms/fcm-token/', MESSAGERIE, 'Appareil enregistré pour les notifications', routine=True),
    _rule('POST', r'comms/notifications/', MESSAGERIE, 'Notification créée', routine=True),
    _rule('POST', r'comms/notifications/mark-all-read/', MESSAGERIE, 'Notifications marquées comme lues', routine=True),
    _rule((*WRITE, 'DELETE'), r'comms/notifications/\d+/', MESSAGERIE, 'Notification lue ou retirée', routine=True),
]

# Entrées écrites à la main par le code, avec un nom d'action et une description
# déjà rédigés. Le middleware, lui, écrit `MÉTHODE /chemin/`.
NAMED_ACTIONS = {
    'collecte_suspendue': (REGLAGES, 'Collecte en espèces suspendue', 'warning'),
    'collecte_reprise': (REGLAGES, 'Collecte en espèces reprise', None),
}

FALLBACK_LABELS = {
    'POST': 'Action enregistrée',
    'PUT': 'Modification',
    'PATCH': 'Modification',
    'DELETE': 'Suppression',
}

_HTTP_ACTION = re.compile(r'^(POST|PUT|PATCH|DELETE) (/\S*)$')

# Le nom donné à une cible disparue : on ne sait plus qui c'était, mais on sait
# encore ce que c'était.
TARGET_NOUNS = {
    'user': 'Compte',
    'donation': 'Jëf',
    'campaign': 'Ndiguel',
    'fete': 'Ndiguel récurrent',
    'todo': 'Tâche',
    'news': 'Actualité',
    'chat': 'Salon',
    'ldd': 'LDD',
    'daara': 'Daara',
    'tutelle': 'Tutelle',
    'title': 'Titre',
    'title_request': 'Demande de titre',
    'document': "Pièce d'identité",
}


def display_name(user) -> str:
    full = f'{user.first_name or ""} {user.last_name or ""}'.strip()
    return full or user.email or user.phone or f'Compte n°{user.pk}'


def _find_rule(method: str, path: str):
    """Renvoie la règle et l'identifiant de cible trouvé dans le chemin."""
    if not path.startswith('/api/'):
        return None, None
    relative = path[len('/api/'):]
    for rule in RULES:
        if method in rule.methods:
            match = rule.pattern.match(relative)
            if match:
                return rule, match.groupdict().get('id')
    return None, None


def _amount(value) -> str:
    return f'{int(value):,}'.replace(',', ' ') + ' FCFA'


def _load_targets(kind: str, ids: set[str]) -> dict[str, tuple[str, str | None]]:
    """Charge d'un coup toutes les cibles d'un même type : `{id: (nom, lien)}`."""
    numeric = {i for i in ids if i.isdigit()}

    if kind == 'user':
        users = apps.get_model('accounts', 'User').objects.filter(pk__in=numeric)
        return {str(u.pk): (display_name(u), f'/dashboard/users/{u.pk}') for u in users}
    if kind == 'donation':
        # Jamais le nom du donateur : un Jëf peut être anonyme.
        donations = apps.get_model('contributions', 'Donation').objects.filter(pk__in=numeric)
        return {
            str(d.pk): (f'Jëf n°{d.pk} · {_amount(d.amount)}', f'/dashboard/donations/{d.pk}')
            for d in donations
        }
    if kind == 'campaign':
        campaigns = apps.get_model('events', 'Campaign').objects.filter(pk__in=numeric)
        return {str(c.pk): (c.name, f'/dashboard/campaigns/{c.pk}') for c in campaigns}
    if kind == 'fete':
        return {
            str(f.pk): (f.name, None)
            for f in apps.get_model('events', 'Fete').objects.filter(pk__in=numeric)
        }
    if kind == 'todo':
        todos = apps.get_model('events', 'CampaignTodo').objects.filter(pk__in=numeric)
        return {str(t.pk): (t.title, None) for t in todos}
    if kind == 'news':
        # Le chemin d'une actualité porte son slug, mais `entity_id` son numéro.
        posts = apps.get_model('news', 'NewsPost').objects.filter(Q(slug__in=ids) | Q(pk__in=numeric))
        found = {}
        for post in posts:
            entry = (post.title, f'/dashboard/news/{post.slug}')
            found[post.slug] = entry
            found[str(post.pk)] = entry
        return found
    if kind == 'chat':
        # Un tête-à-tête n'a pas de nom, et le journal n'a pas à en inventer un.
        chats = apps.get_model('comms', 'Chat').objects.filter(pk__in=numeric)
        return {str(c.pk): (c.name or 'Discussion privée', None) for c in chats}
    if kind == 'ldd':
        ldds = apps.get_model('accounts', 'LDD').objects.filter(pk__in=numeric)
        return {str(l.pk): (f'{l.code} · {l.name}', None) for l in ldds}
    if kind == 'daara':
        daaras = apps.get_model('accounts', 'Daara').objects.filter(pk__in=numeric)
        return {str(d.pk): (d.name, f'/dashboard/admin/daara/{d.pk}') for d in daaras}
    if kind == 'tutelle':
        tutelles = apps.get_model('accounts', 'Tutelle').objects.filter(pk__in=numeric)
        return {str(t.pk): (f'{t.first_name} {t.last_name}'.strip(), None) for t in tutelles}
    if kind == 'title':
        titles = apps.get_model('accounts', 'MemberTitle').objects.filter(pk__in=numeric)
        return {str(t.pk): (t.name, None) for t in titles}
    if kind == 'title_request':
        requests = (
            apps.get_model('accounts', 'TitleRequest').objects
            .filter(pk__in=numeric).select_related('member', 'title')
        )
        return {
            str(r.pk): (f'{display_name(r.member)} · {r.title.name}', f'/dashboard/users/{r.member_id}')
            for r in requests
        }
    if kind == 'document':
        documents = (
            apps.get_model('accounts', 'UserDocument').objects
            .filter(pk__in=numeric).select_related('user')
        )
        return {
            str(d.pk): (display_name(d.user), f'/dashboard/users/{d.user_id}')
            for d in documents
        }
    return {}


def target_label_for_path(method: str, path: str) -> str | None:
    """Le nom de la cible à cet instant : appelé par le middleware AVANT une
    suppression, seul moment où on peut encore le lire."""
    rule, target_id = _find_rule(method, path)
    if not rule or not rule.target or target_id is None:
        return None
    found = _load_targets(rule.target, {target_id}).get(target_id)
    return found[0] if found else None


class AuditPresenter:
    """Présente un lot d'entrées, en une requête par TYPE de cible et non par ligne."""

    def __init__(self, logs):
        self._parsed = {log.pk: self._parse(log) for log in logs}

        wanted = defaultdict(set)
        for rule, target_id in self._parsed.values():
            if rule and rule.target and target_id is not None:
                wanted[rule.target].add(target_id)
        self._targets = {kind: _load_targets(kind, ids) for kind, ids in wanted.items()}

    @staticmethod
    def _parse(log):
        match = _HTTP_ACTION.match(log.action or '')
        if not match:
            return None, None
        rule, target_id = _find_rule(match[1], match[2])
        if target_id is None and log.entity_id is not None:
            target_id = str(log.entity_id)
        return rule, target_id

    def _target(self, log, rule, target_id):
        if not rule or not rule.target or target_id is None:
            return None, None

        found = self._targets.get(rule.target, {}).get(target_id)
        if found:
            return found

        # La cible n'existe plus : le nom relevé juste avant la suppression fait
        # foi. Les entrées plus anciennes que ce relevé n'ont que leur numéro.
        snapshot = (log.metadata or {}).get('target_label')
        if snapshot:
            return snapshot, None
        reference = f'n°{target_id}' if target_id.isdigit() else f'« {target_id} »'
        return f'{TARGET_NOUNS[rule.target]} {reference} (n’existe plus)', None

    def present(self, log) -> dict:
        match = _HTTP_ACTION.match(log.action or '')
        rule, target_id = self._parsed.get(log.pk) or self._parse(log)

        if rule:
            target_label, target_href = self._target(log, rule, target_id)
            return {
                'label': rule.label,
                'domain': rule.domain,
                'routine': rule.routine,
                'tone': rule.tone or ('danger' if match[1] == 'DELETE' else None),
                'target_label': target_label,
                'target_href': target_href,
                'detail': None,
            }

        if match:
            # Route inconnue : on dit ce qu'on sait, et elle reste visible.
            method, path = match[1], match[2]
            return {
                'label': FALLBACK_LABELS[method],
                'domain': AUTRE,
                'routine': False,
                'tone': 'danger' if method == 'DELETE' else None,
                'target_label': path,
                'target_href': None,
                'detail': None,
            }

        domain, label, tone = NAMED_ACTIONS.get(log.action, (AUTRE, log.action, None))
        return {
            'label': label,
            'domain': domain,
            'routine': False,
            'tone': tone,
            'target_label': None,
            'target_href': None,
            'detail': log.description or None,
        }
