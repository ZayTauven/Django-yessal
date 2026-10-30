"""La fenêtre de suivi de Sangue Bi, la guide du Kourel.

Toute la plateforme d'un coup d'œil : les Jëfs, les Talubés, les Ndiguels,
les coordinateurs, et la répartition par LDD, par Daara, par titre et par
pays — jusqu'au montant de chaque Jëf (décision du 2026-10-30).

LECTURE SEULE. La guide voit et annonce ; elle ne valide ni ne modifie rien.
Ses annonces passent par la messagerie (`comms`), où son rôle lui ouvre le
canal « Annonces de Sangue Bi ».

Conventions de calcul, les mêmes partout ici :

- Un Jëf COMPTE quand il est confirmé (`confirmed`) — comme le tableau de
  bord de l'administration. Ce qui est demandé, encaissé par un collecteur ou
  annoncé par virement « attend la confirmation de l'équipe ».
- Un Jëf est rattaché au Daara (et donc à la LDD) de son DONATEUR.
- Le pays est celui du donateur, normalisé : « Senegal » et « Sénégal » sont
  le même pays. Un pays non renseigné compte comme le Sénégal pour la part de
  la diaspora (même règle que les cartes du canal des Jëfs).
"""
import datetime
import unicodedata
from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from contributions.models import Donation
from events.models import Campaign

from .models import LDD, Daara, MemberTitle, User

PERIODES = ('mois', '3mois', 'tout')

#: Les états d'un Jëf qui attend encore l'équipe.
EN_ATTENTE = (
    Donation.PaymentStatus.PENDING,
    Donation.PaymentStatus.PENDING_WIRE,
    Donation.PaymentStatus.COLLECTED,
)

#: Formes d'un même pays, une fois les accents et la casse retirés.
PAYS_LOCAUX = {'', 'senegal', 'sn'}
NOMS_DE_PAYS = {
    'senegal': 'Sénégal', 'sn': 'Sénégal', 'france': 'France',
    'etats-unis': 'États-Unis', 'etats unis': 'États-Unis', 'usa': 'États-Unis',
    'italie': 'Italie', 'espagne': 'Espagne', 'gambie': 'Gambie', 'gambia': 'Gambie',
}

DELAI_SANS_JEF = 30
ALERTES_MAX = 5


class EstGuideOuAdmin(permissions.BasePermission):
    message = "Cette fenêtre est réservée à la guide du Kourel."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated
            and user.role in (User.Role.GUIDE, User.Role.ADMIN)
        )


def cle_pays(valeur):
    """« Sénégal », « senegal », « SENEGAL » → « senegal »."""
    texte = unicodedata.normalize('NFKD', (valeur or '').strip().lower())
    return ''.join(c for c in texte if not unicodedata.combining(c))


def nom_pays(cle, vu):
    if not cle:
        return 'Non renseigné'
    return NOMS_DE_PAYS.get(cle) or vu


def debut_de_periode(periode, today):
    if periode == 'mois':
        return today.replace(day=1)
    if periode == '3mois':
        mois = today.replace(day=1)
        for _ in range(2):
            mois = (mois - datetime.timedelta(days=1)).replace(day=1)
        return mois
    return None


def confirmes(depuis=None):
    qs = Donation.objects.filter(payment_status=Donation.PaymentStatus.CONFIRMED)
    if depuis:
        qs = qs.filter(created_at__date__gte=depuis)
    return qs


def montant(valeur):
    return int(valeur or 0)


def nom(user):
    return (user.get_full_name() or user.email or user.phone or '').strip()


class SuiviViewSet(viewsets.ViewSet):
    """`/api/suivi/` — vue d'ensemble, répartition, détail d'un Daara."""

    permission_classes = [EstGuideOuAdmin]

    # ── Outils ────────────────────────────────────────────────────────────
    def _periode(self, request):
        periode = request.query_params.get('periode', 'tout')
        return periode if periode in PERIODES else 'tout'

    def _avatar(self, user):
        if not user:
            return None
        if user.avatar:
            return self.request.build_absolute_uri(user.avatar.url)
        return user.avatar_url or None

    def _personne(self, user):
        return {
            'id': user.id,
            'name': nom(user),
            'title': user.title.name if user.title_id else None,
            'avatar': self._avatar(user),
        }

    def _jef(self, d):
        return {
            'id': d.id,
            'donor': self._personne(d.donor),
            'amount': montant(d.amount),
            'campaign': {'id': d.campaign_id, 'name': d.campaign.name},
            'method': d.get_payment_method_display(),
            'daara': d.donor.daara.name if d.donor.daara_id else None,
            'created_at': d.created_at.isoformat(),
        }

    # ── Vue d'ensemble ────────────────────────────────────────────────────
    def list(self, request):
        today = timezone.localdate()
        periode = self._periode(request)
        depuis = debut_de_periode(periode, today)
        jefs = confirmes(depuis)

        total = jefs.aggregate(s=Sum('amount'), n=Count('id'))
        attente = Donation.objects.filter(payment_status__in=EN_ATTENTE).aggregate(
            s=Sum('amount'), n=Count('id'),
        )

        diaspora = Decimal(0)
        for pays, somme in jefs.values_list('donor__residence_country').annotate(s=Sum('amount')):
            if cle_pays(pays) not in PAYS_LOCAUX:
                diaspora += somme or 0
        diaspora_pct = round(diaspora * 100 / total['s']) if total['s'] else 0

        talubes = User.objects.filter(status=User.Status.ACTIVE).exclude(
            role__in=(User.Role.ADMIN, User.Role.GUIDE),
        )

        ldd_actives = list(
            jefs.exclude(donor__daara__ldd=None)
            .values('donor__daara__ldd__name')
            .annotate(s=Sum('amount')).order_by('-s')
        )

        coordinateurs = User.objects.filter(role=User.Role.CHEF_DAARA).select_related('daara', 'title')

        ndiguels = self._ndiguels(today)

        return Response({
            'period': periode,
            'since': depuis.isoformat() if depuis else None,
            'donations': {
                'amount': montant(total['s']),
                'count': total['n'],
                'diaspora_pct': diaspora_pct,
                'pending': {'amount': montant(attente['s']), 'count': attente['n']},
            },
            'members': {
                'total': talubes.count(),
                'pending': User.objects.filter(status=User.Status.PENDING).count(),
                'titled': talubes.exclude(title=None).count(),
            },
            'campaigns': {
                'active': sum(1 for n in ndiguels if n['status'] == Campaign.Status.ACTIVE),
                'upcoming': sum(1 for n in ndiguels if n['status'] == Campaign.Status.PENDING),
            },
            'ldd': {
                'active': len(ldd_actives),
                'total': LDD.objects.count(),
                'top': ldd_actives[0]['donor__daara__ldd__name'] if ldd_actives else None,
            },
            'coordinators': {
                'total': coordinateurs.count(),
                'daaras': Daara.objects.filter(is_active=True).count(),
            },
            'monthly': self._mensuel(today),
            'campaigns_list': ndiguels,
            'alerts': self._a_regarder(today, depuis, attente, len(ldd_actives)),
            'coordinators_list': self._coordinateurs(coordinateurs, depuis),
            'recent': [
                self._jef(d) for d in jefs.select_related(
                    'donor', 'donor__title', 'donor__daara', 'campaign',
                ).order_by('-created_at')[:10]
            ],
        })

    def _mensuel(self, today, nombre=8):
        mois = [today.replace(day=1)]
        while len(mois) < nombre:
            mois.insert(0, (mois[0] - datetime.timedelta(days=1)).replace(day=1))
        lignes = {
            (r['m'].year, r['m'].month): r
            for r in confirmes(mois[0]).annotate(m=TruncMonth('created_at'))
            .values('m').annotate(s=Sum('amount'), n=Count('id'))
        }
        return [
            {
                'month': m.strftime('%Y-%m'),
                'amount': montant(lignes.get((m.year, m.month), {}).get('s')),
                'count': lignes.get((m.year, m.month), {}).get('n', 0),
            }
            for m in mois
        ]

    def _ndiguels(self, today):
        """Les Ndiguels en cours puis à venir — ce qu'un membre voit aussi."""
        recus = dict(
            confirmes().values_list('campaign').annotate(s=Sum('amount'))
        )
        lignes = []
        for c in Campaign.objects.filter(a_confirmer=False).exclude(
            status__in=(Campaign.Status.COMPLETED, Campaign.Status.INACTIVE),
        ).filter(deadline__gte=today):
            statut = c.get_effective_status(today)
            if statut not in (Campaign.Status.ACTIVE, Campaign.Status.PENDING):
                continue
            recu = montant(recus.get(c.id))
            but = montant(c.goal_amount)
            lignes.append({
                'id': c.id,
                'name': c.name,
                'status': statut,
                'goal_amount': but or None,
                'collected': recu,
                'pct': round(recu * 100 / but) if but else None,
                'deadline': c.deadline.isoformat(),
                'opens_on': c.opens_on.isoformat() if c.opens_on else None,
                'recurrent': c.fete_id is not None,
            })
        lignes.sort(key=lambda n: (n['status'] != Campaign.Status.ACTIVE, n['deadline']))
        return lignes

    def _a_regarder(self, today, depuis, attente, ldd_actives):
        """Ce qui mérite l'attention de la guide, le plus pressant d'abord.

        Chaque alerte est `{kind, strong, text}` : `strong` est le nom à mettre
        en gras, `text` la suite de la phrase, qui le suit directement.
        """
        alertes = []

        bientot = today + datetime.timedelta(days=7)
        recus = dict(confirmes().values_list('campaign').annotate(s=Sum('amount')))
        for c in Campaign.objects.filter(
            status=Campaign.Status.ACTIVE, a_confirmer=False,
            deadline__gte=today, deadline__lte=bientot, goal_amount__gt=0,
        ).order_by('deadline'):
            # Le statut ENREGISTRÉ dit « en cours » ; les dates peuvent dire
            # « pas encore ouvert » (`opens_on`). Seul l'effectif fait foi.
            if c.get_effective_status(today) != Campaign.Status.ACTIVE:
                continue
            pct = round(montant(recus.get(c.id)) * 100 / montant(c.goal_amount))
            if pct < 100:
                jours = (c.deadline - today).days
                quand = "aujourd'hui" if jours == 0 else f"dans {jours} jour{'s' if jours > 1 else ''}"
                alertes.append({
                    'kind': 'ndiguel_fin', 'strong': c.name,
                    'text': f" se termine {quand}, à {pct} % de son objectif.",
                })

        il_y_a_30 = today - datetime.timedelta(days=DELAI_SANS_JEF)
        for c in Campaign.objects.filter(
            deadline__lt=today, deadline__gte=il_y_a_30, goal_amount__gt=0, a_confirmer=False,
        ).exclude(status=Campaign.Status.INACTIVE).order_by('-deadline'):
            pct = round(montant(recus.get(c.id)) * 100 / montant(c.goal_amount))
            if pct < 50:
                alertes.append({
                    'kind': 'ndiguel_sous_objectif', 'strong': c.name,
                    'text': f" s'est terminé le {c.deadline:%d/%m} à {pct} % de son objectif.",
                })

        if attente['n']:
            alertes.append({
                'kind': 'en_attente',
                'strong': f"{attente['n']} Jëf{'s' if attente['n'] > 1 else ''}",
                'text': f" ({montant(attente['s']):,} FCFA) attend{'ent' if attente['n'] > 1 else ''} "
                        "la confirmation de l'équipe.".replace(',', ' '),
            })

        donateurs_recents = set(
            confirmes(il_y_a_30).exclude(donor__daara=None).values_list('donor__daara', flat=True)
        )
        for chef in User.objects.filter(role=User.Role.CHEF_DAARA).exclude(daara=None).select_related('daara'):
            if chef.daara_id not in donateurs_recents:
                alertes.append({
                    'kind': 'daara_sans_jef', 'strong': f"Daara {chef.daara.name}",
                    'text': f" (coordination : {nom(chef)}) n'a fait aucun Jëf depuis {DELAI_SANS_JEF} jours.",
                })

        total_ldd = LDD.objects.count()
        if total_ldd and ldd_actives < total_ldd:
            sans = total_ldd - ldd_actives
            periode = "ce mois-ci" if depuis and depuis == today.replace(day=1) else (
                "sur la période" if depuis else "pour l'instant"
            )
            alertes.append({
                'kind': 'ldd_sans_jef', 'strong': f"{sans} LDD sur {total_ldd}",
                'text': f" n'ont fait aucun Jëf {periode}.",
            })
        return alertes[:ALERTES_MAX]

    def _coordinateurs(self, coordinateurs, depuis):
        par_daara = {
            r['donor__daara']: r
            for r in confirmes(depuis).exclude(donor__daara=None)
            .values('donor__daara').annotate(s=Sum('amount'), n=Count('id'))
        }
        lignes = []
        for chef in coordinateurs:
            r = par_daara.get(chef.daara_id, {})
            lignes.append({
                **self._personne(chef),
                'daara': {'id': chef.daara_id, 'name': chef.daara.name} if chef.daara_id else None,
                'amount': montant(r.get('s')),
                'count': r.get('n', 0),
            })
        lignes.sort(key=lambda l: -l['amount'])
        return lignes

    # ── Répartition ───────────────────────────────────────────────────────
    @action(detail=False, methods=['get'])
    def repartition(self, request):
        """`?par=ldd|daara|titre|pays` — un classement, le plus grand d'abord."""
        today = timezone.localdate()
        periode = self._periode(request)
        jefs = confirmes(debut_de_periode(periode, today))
        par = request.query_params.get('par', 'daara')
        total = montant(jefs.aggregate(s=Sum('amount'))['s'])

        if par == 'ldd':
            lignes = [
                {'id': r['donor__daara__ldd'], 'name': r['donor__daara__ldd__name'],
                 'amount': montant(r['s']), 'count': r['n']}
                for r in jefs.exclude(donor__daara__ldd=None)
                .values('donor__daara__ldd', 'donor__daara__ldd__name')
                .annotate(s=Sum('amount'), n=Count('id')).order_by('-s')
            ]
            sans = LDD.objects.count() - len(lignes)
        elif par == 'titre':
            membres = dict(
                User.objects.filter(status=User.Status.ACTIVE).exclude(title=None)
                .values_list('title').annotate(n=Count('id'))
            )
            recus = {
                r['donor__title']: r
                for r in jefs.values('donor__title').annotate(s=Sum('amount'), n=Count('id'))
            }
            lignes = [
                {'id': t.id, 'name': t.name, 'members': membres.get(t.id, 0),
                 'amount': montant(recus.get(t.id, {}).get('s')),
                 'count': recus.get(t.id, {}).get('n', 0)}
                for t in MemberTitle.objects.all()
            ]
            sans_titre = recus.get(None, {})
            lignes.append({
                'id': None, 'name': 'Sans titre',
                'members': User.objects.filter(status=User.Status.ACTIVE, title=None)
                .exclude(role__in=(User.Role.ADMIN, User.Role.GUIDE)).count(),
                'amount': montant(sans_titre.get('s')), 'count': sans_titre.get('n', 0),
            })
            lignes.sort(key=lambda l: (-l['amount'], -l['members']))
            sans = None
        elif par == 'pays':
            groupes = defaultdict(lambda: {'amount': 0, 'count': 0, 'vu': ''})
            for pays, s, n in jefs.values_list('donor__residence_country').annotate(
                s=Sum('amount'), n=Count('id'),
            ):
                g = groupes[cle_pays(pays)]
                g['amount'] += montant(s)
                g['count'] += n
                # La graphie accentuée l'emporte : « Sénégal » plutôt que « Senegal ».
                if len((pays or '').encode()) > len(g['vu'].encode()):
                    g['vu'] = (pays or '').strip()
            lignes = sorted(
                (
                    {'id': cle or None, 'name': nom_pays(cle, g['vu']),
                     'amount': g['amount'], 'count': g['count']}
                    for cle, g in groupes.items()
                ),
                key=lambda l: -l['amount'],
            )
            sans = None
        else:
            par = 'daara'
            lignes = [
                {'id': r['donor__daara'], 'name': r['donor__daara__name'],
                 'ldd': r['donor__daara__ldd__name'],
                 'amount': montant(r['s']), 'count': r['n']}
                for r in jefs.exclude(donor__daara=None)
                .values('donor__daara', 'donor__daara__name', 'donor__daara__ldd__name')
                .annotate(s=Sum('amount'), n=Count('id')).order_by('-s')
            ]
            sans = Daara.objects.filter(is_active=True).count() - len(lignes)

        for ligne in lignes:
            ligne['pct'] = round(ligne['amount'] * 100 / total) if total else 0
        return Response({
            'by': par, 'period': periode, 'total': total,
            'without_jef': max(sans, 0) if sans is not None else None,
            'rows': lignes,
        })

    # ── Un Daara, jusqu'au membre ─────────────────────────────────────────
    @action(detail=False, methods=['get'], url_path=r'daara/(?P<daara_id>\d+)')
    def daara(self, request, daara_id=None):
        today = timezone.localdate()
        periode = self._periode(request)
        daara = get_object_or_404(Daara.objects.select_related('ldd'), pk=daara_id)
        jefs = confirmes(debut_de_periode(periode, today)).filter(donor__daara=daara)
        total = jefs.aggregate(s=Sum('amount'), n=Count('id'))

        membres = User.objects.filter(daara=daara).exclude(
            role__in=(User.Role.ADMIN, User.Role.GUIDE),
        ).select_related('title')
        par_membre = {
            r['donor']: r for r in jefs.values('donor').annotate(s=Sum('amount'), n=Count('id'))
        }
        lignes = [
            {**self._personne(m), 'role': m.role,
             'amount': montant(par_membre.get(m.id, {}).get('s')),
             'count': par_membre.get(m.id, {}).get('n', 0)}
            for m in membres
        ]
        lignes.sort(key=lambda l: (-l['amount'], l['name']))
        chef = next((m for m in membres if m.role == User.Role.CHEF_DAARA), None)

        return Response({
            'id': daara.id,
            'name': daara.name,
            'ldd': {'id': daara.ldd_id, 'name': daara.ldd.name} if daara.ldd_id else None,
            'period': periode,
            'coordinator': {**self._personne(chef), 'phone': chef.phone} if chef else None,
            'amount': montant(total['s']),
            'count': total['n'],
            'members_count': len(lignes),
            'donors_count': len(par_membre),
            'members': lignes,
            'donations': [
                self._jef(d) for d in jefs.select_related(
                    'donor', 'donor__title', 'donor__daara', 'campaign',
                ).order_by('-created_at')[:50]
            ],
        })
