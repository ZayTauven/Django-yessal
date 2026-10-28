from django.db.models import F, Sum
from django.utils import timezone

from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from accounts.models import User
from accounts.serializers import DirectoryUserSerializer
from comms.models import Notification
from comms.notify import notify, notify_many
from contributions.models import Donation

from .models import Campaign, CampaignTodo, Fete
from .occurrences import prochaine
from .serializers import CampaignSerializer, CampaignTodoSerializer, FeteSerializer


# ═══════════════════════════════════════════════════════════════════════════
# Qui peut LANCER un Ndiguel
# ═══════════════════════════════════════════════════════════════════════════
# 🔴 CORRIGÉ LE 2026-09-06. La liste valait
# `('admin', 'chef_daara', 'collector', 'member')` : n'importe quel talubé
# pouvait ouvrir un appel aux dons au nom de la confrérie.
#
# La règle métier ne l'a jamais permis. `AGENTS/tools/05_use_cases_regles.md`,
# UC-06 « Création d'une campagne de dons » ne porte qu'un acteur —
# **Administrateur** — et `01_vision_produit.md` attribue au chef de Daara la
# gestion de son Daara, la proposition de collecteurs et la création de salons,
# jamais la création de campagnes.
#
# Le tableau de bord tenait déjà la règle : `CampaignsClient.tsx` n'affiche
# « Lancer un Ndiguel » que derrière `isAdmin`. L'écart était donc invisible en
# usage normal — l'API acceptait ce que l'interface ne proposait pas. C'est le
# pire des deux mondes : une règle tenue par l'écran, pas par le serveur.
#
# ⚠ Ne pas rouvrir cette liste sans rouvrir UC-06. Un appel aux dons engage la
# confrérie auprès de ses membres ; ce n'est pas une ressource qu'on crée.
CAMPAIGN_CREATOR_ROLES = ('admin',)


class IsCampaignOrganizer(permissions.BasePermission):
    """
    - GET (liste/détail) : ouvert à tous les utilisateurs authentifiés.
    - POST (création)    : **administrateur seul** (UC-06).
    - PUT/PATCH/DELETE   : réservé à l'organisateur désigné ou admin/chef_daara.

    ⚠ La nuance du dernier point est VOULUE et confirmée par le commanditaire :
    un chef de Daara suit l'avancement des tâches d'un Ndiguel même s'il n'en
    est pas l'organisateur. Voir `Campaign.can_be_managed_by`.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        # La création est restreinte aux rôles de la confrérie
        if view.action == 'create':
            return getattr(request.user, 'role', None) in CAMPAIGN_CREATOR_ROLES
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        campaign = obj.campaign if hasattr(obj, 'campaign') else obj
        return campaign.can_be_managed_by(request.user)


class FeteViewSet(viewsets.ModelViewSet):
    queryset = Fete.objects.all().order_by('name')
    serializer_class = FeteSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['name', 'description']

    def get_permissions(self):
        if self.action in {'list', 'retrieve', 'etat'}:
            return [permissions.IsAuthenticated()]
        return [permissions.IsAdminUser()]

    @action(detail=True, methods=['post'], url_path='prochaine-edition')
    def prochaine_edition(self, request, pk=None):
        """« Préparer la prochaine édition », sans attendre le planificateur."""
        from .editions import preparer_prochaine

        fete = self.get_object()
        edition, creee = preparer_prochaine(fete, timezone.localdate())
        if not edition:
            return Response(
                {'detail': "Cette série n'a pas de prochaine date : renseignez sa date de référence et sa récurrence."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {**CampaignSerializer(edition, context={'request': request}).data, 'creee': creee},
            status=status.HTTP_201_CREATED if creee else status.HTTP_200_OK,
        )

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        previous = self.get_object()
        previous_date = previous.date
        fete = serializer.save()

        # Notify community only when the fete date changes.
        if previous_date == fete.date or fete.date is None:
            return
        # `date` est la RÉFÉRENCE d'une série : on annonce la prochaine
        # occurrence qu'elle donne, pas la référence elle-même (le Toggou de
        # référence du 2 octobre aurait été annoncé comme « prochain »).
        prochaine_date = prochaine(fete, timezone.localdate())
        if prochaine_date is None:
            return

        target_roles = [User.Role.MEMBER, User.Role.COLLECTOR, User.Role.CHEF_DAARA, User.Role.TUTELLE]
        # `only('id')` ne suffit plus : l'envoi a besoin de l'adresse et du
        # prénom. Deux colonnes de plus valent mieux qu'une requête par membre.
        targets = list(
            User.objects.filter(is_active=True, role__in=target_roles)
            .only('id', 'email', 'first_name')
        )
        formatted_date = prochaine_date.strftime('%d/%m/%Y')

        # Seul envoi de masse du produit : il part dans un fil séparé, sur une
        # seule connexion SMTP. Une boucle d'envois unitaires tiendrait la
        # requête de l'administrateur plusieurs minutes.
        notify_many(
            targets,
            code='fete_date_modifiee',
            titre=f"Prochaine {fete.name}",
            message=f"Prochaine {fete.name} le {formatted_date}.",
            contexte={'fete': fete.name, 'date': formatted_date},
            # Pas de courriel de masse pour un hebdomadaire : chaque retouche
            # de la série écrirait à tout le Kourel. La notification suffit.
            courriel=fete.recurrence != Fete.Recurrence.WEEKLY,
        )

    @action(detail=True, methods=['get'], url_path='etat')
    def etat(self, request, pk=None):
        fete = self.get_object()
        from contributions.models import Donation

        campaigns = (
            Campaign.objects.filter(fete=fete).select_related('daara', 'organizer')
            .order_by(F('occurrence_date').desc(nulls_last=True), '-created_at')
        )
        if request.user.role != User.Role.ADMIN:
            campaigns = campaigns.exclude(a_confirmer=True)
        donations = (
            Donation.objects
            .filter(campaign__fete=fete, payment_status=Donation.PaymentStatus.CONFIRMED)
            .select_related('donor', 'donor__daara', 'campaign')
            .order_by('-created_at')
        )

        total_collected = donations.aggregate(total=Sum('amount'))['total'] or 0
        donation_count = donations.count()

        contributions = []
        for d in donations:
            donor_name = d.donor.get_full_name().strip() if d.donor else ''
            member_name = 'Contributeur anonyme' if d.is_anonymous else (donor_name or getattr(d.donor, 'email', '') or getattr(d.donor, 'phone', '') or str(d.donor_id))
            contributions.append({
                'member_name': member_name,
                'member_id': d.donor_id,
                'daara_name': d.donor.daara.name if d.donor and d.donor.daara else None,
                'campaign_name': d.campaign.name if d.campaign else None,
                'amount': str(d.amount),
                'date': d.created_at.date().isoformat(),
                'payment_method': d.payment_method,
                'is_anonymous': d.is_anonymous,
            })

        campaign_data = []
        for c in campaigns:
            camp_collected = c.donations.filter(payment_status='confirmed').aggregate(total=Sum('amount'))['total'] or 0
            goal = float(c.goal_amount) if c.goal_amount else 0
            progress = int((float(camp_collected) / goal) * 100) if goal else 0
            campaign_data.append({
                'id': c.id,
                'name': c.name,
                'goal_amount': str(c.goal_amount) if c.goal_amount else None,
                'collected_amount': str(camp_collected),
                'progress_pct': progress,
                'status': c.get_effective_status(),
                'deadline': c.deadline.isoformat(),
                'opens_on': c.opens_on.isoformat() if c.opens_on else None,
                'occurrence_date': c.occurrence_date.isoformat() if c.occurrence_date else None,
                'a_confirmer': c.a_confirmer,
                'daara_name': c.daara.name if c.daara else None,
                'organizer_name': c.organizer.get_full_name().strip() if c.organizer else None,
            })

        return Response({
            'id': fete.id,
            'name': fete.name,
            'date': fete.date.isoformat() if fete.date else None,
            'description': fete.description,
            'recurrence': fete.get_recurrence_display(),
            'recurrence_code': fete.recurrence,
            'prochaine_date': (lambda d: d.isoformat() if d else None)(prochaine(fete, timezone.localdate())),
            'auto_editions': fete.auto_editions,
            'ouvre_jours_avant': fete.ouvre_jours_avant,
            'ferme_jours_apres': fete.ferme_jours_apres,
            'is_active': fete.is_active,
            'total_collected': str(total_collected),
            'donation_count': donation_count,
            'campaigns_count': campaigns.count(),
            'campaigns': campaign_data,
            'contributions': contributions,
        })


class CampaignViewSet(viewsets.ModelViewSet):
    queryset = Campaign.objects.all().select_related('organizer', 'daara', 'fete').prefetch_related('todos').order_by('-created_at')
    serializer_class = CampaignSerializer
    permission_classes = [IsCampaignOrganizer]
    filter_backends = [filters.SearchFilter]
    search_fields = ['name', 'description', 'daara__name', 'fete__name']

    # Rôles à qui la LISTE ne montre que les Ndiguels encore ouverts.
    ROLES_SANS_NDIGUELS_TERMINES = (User.Role.MEMBER, User.Role.TUTELLE)

    def destroy(self, request, *args, **kwargs):
        """Supprimer un Ndiguel : l'administration seule, et jamais s'il porte des Jëfs.

        🔴 `can_be_managed_by` ouvre la gestion à TOUT coordinateur de Daara et
        à l'organisateur désigné — une nuance voulue pour suivre les tâches.
        Elle valait aussi pour DELETE, et `Donation.campaign` était en
        CASCADE : un clic de n'importe quel coordinateur effaçait le Ndiguel ET
        tous ses Jëfs, confirmés et remis compris. La clé est désormais en
        PROTECT ; on répond ici avant que la base ne lève.
        """
        campaign = self.get_object()
        if request.user.role != User.Role.ADMIN:
            return Response(
                {'detail': "Seule l'administration peut supprimer un Ndiguel."},
                status=status.HTTP_403_FORBIDDEN,
            )
        nb = campaign.donations.count()
        if nb:
            return Response(
                {'detail': (
                    f"Ce Ndiguel porte {nb} Jëf{'s' if nb > 1 else ''} : il ne peut pas être "
                    "supprimé sans effacer l'historique des contributions. Clôturez-le."
                )},
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)

    def get_queryset(self):
        """Un talubé ou une tutelle ne voit plus, en liste, les Ndiguels terminés.

        Demande du client (réunion du 2026-10) : un appel clos n'a plus rien à
        proposer à qui ne fait que donner. Le filtre reproduit
        `Campaign.get_effective_status()` : terminé = statut `completed`, OU
        échéance passée. Un Ndiguel `inactive` dont l'échéance court encore
        reste listé, comme avant — seul « terminé » est en cause.

        ⚠ LA LISTE SEULEMENT. Le détail (`retrieve`) et les actions (`etat`)
        restent servis : une notification, un Jëf ou un lien partagé pointe
        vers un Ndiguel qui a pu se clore depuis, et doit toujours s'ouvrir.
        """
        qs = super().get_queryset()
        role = getattr(self.request.user, 'role', None)
        # Une édition à la date lunaire pas encore confirmée n'existe que pour
        # l'administration : la date peut encore bouger d'un jour ou deux.
        if role != User.Role.ADMIN:
            qs = qs.exclude(a_confirmer=True)
        if self.action == 'list' and role in self.ROLES_SANS_NDIGUELS_TERMINES:
            qs = (
                qs.exclude(status=Campaign.Status.COMPLETED)
                .exclude(deadline__lt=timezone.localdate())
            )
        return qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context

    def perform_create(self, serializer):
        organizer = serializer.validated_data.get('organizer')
        campaign = serializer.save(created_by=self.request.user, organizer_assigned_at=timezone.now() if organizer else None)
        self._notify_organizer(campaign)

    def perform_update(self, serializer):
        current = self.get_object()
        old_organizer = current.organizer
        new_organizer = serializer.validated_data.get('organizer', old_organizer)
        assigned_at = current.organizer_assigned_at
        if new_organizer and new_organizer != old_organizer:
            assigned_at = timezone.now()
        if new_organizer is None:
            assigned_at = None
        etait_ouvert = current.get_effective_status() not in (
            Campaign.Status.COMPLETED, Campaign.Status.INACTIVE,
        )
        campaign = serializer.save(organizer_assigned_at=assigned_at)
        if campaign.organizer and campaign.organizer != old_organizer:
            self._notify_organizer(campaign)

        # Clôture explicite : si des espèces sont encore en route, les admins
        # le savent. La clôture n'arrête pas les collecteurs (décision du
        # 2026-10-28) ; elle ne doit pas non plus faire oublier cet argent.
        if etait_ouvert and campaign.get_effective_status() in (
            Campaign.Status.COMPLETED, Campaign.Status.INACTIVE,
        ):
            from contributions.services.collecte import prevenir_cloture_avec_especes
            prevenir_cloture_avec_especes(campaign)

    @action(detail=True, methods=['post'], url_path='confirmer-date')
    def confirmer_date(self, request, pk=None):
        """Confirme (ou corrige) la date d'une édition hégirienne estimée.

        Tant qu'elle n'est pas confirmée, l'édition reste invisible aux membres
        et refuse les Jëfs : la date suit l'annonce, pas le calcul.
        """
        from datetime import date as date_cls

        from .editions import confirmer_date

        if request.user.role != User.Role.ADMIN:
            raise PermissionDenied("Seule l'administration confirme une date.")
        edition = self.get_object()
        try:
            jour = date_cls.fromisoformat(str(request.data.get('date', '')))
        except ValueError:
            return Response({'date': ["Date attendue au format AAAA-MM-JJ."]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            confirmer_date(edition, jour)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(CampaignSerializer(edition, context={'request': request}).data)

    def _notify_organizer(self, campaign):
        if not campaign.organizer:
            return
        notify(
            campaign.organizer,
            code='ndiguel_responsable',
            titre='Nouvelle assignation de Ndiguel',
            message=(
                f"Vous êtes responsable du Ndiguel « {campaign.name} ». "
                f"Privilèges actifs jusqu'au {campaign.deadline}."
            ),
            contexte={
                'campagne': campaign.name,
                'campaign': campaign,
                'objectif': campaign.goal_amount,
                'echeance': campaign.deadline.strftime('%d/%m/%Y') if campaign.deadline else '',
            },
        )

    @action(detail=True, methods=['get'], url_path='organizer-directory')
    def organizer_directory(self, request, pk=None):
        campaign = self.get_object()
        if not campaign.can_be_managed_by(request.user):
            return Response({'detail': "Vous n'avez pas accès à l'espace d'organisation de cette campagne."}, status=status.HTTP_403_FORBIDDEN)

        qs = User.objects.filter(role__in=[User.Role.MEMBER, User.Role.COLLECTOR, User.Role.CHEF_DAARA]).exclude(id=request.user.id).select_related('daara')

        scoped_daara_id = campaign.daara_id or getattr(campaign.organizer, 'daara_id', None)
        if scoped_daara_id:
            qs = qs.filter(daara_id=scoped_daara_id)

        return Response(
            {
                'campaign_id': campaign.id,
                'campaign_name': campaign.name,
                'organizer_id': campaign.organizer_id,
                'eligible_users': DirectoryUserSerializer(qs.order_by('last_name', 'first_name'), many=True).data,
            }
        )

    @action(detail=True, methods=['get'], url_path='etat')
    def etat(self, request, pk=None):
        campaign = self.get_object()
        donations = Donation.objects.filter(campaign=campaign, payment_status=Donation.PaymentStatus.CONFIRMED).select_related('donor', 'donor__daara')
        collected_amount = donations.aggregate(total=Sum('amount'))['total'] or 0
        donation_count = donations.count()
        goal_amount = campaign.goal_amount or 0
        progress_pct = int((collected_amount / goal_amount) * 100) if goal_amount else 0

        contributions = []
        for d in donations.order_by('-created_at'):
            donor_name = d.donor.get_full_name().strip() if d.donor else ''
            member_name = 'Contributeur anonyme' if d.is_anonymous else (donor_name or d.donor.email or d.donor.phone)
            contributions.append(
                {
                    'member_name': member_name,
                    'member_id': d.donor_id,
                    'daara_name': d.donor.daara.name if d.donor and d.donor.daara else None,
                    'amount': d.amount,
                    'date': d.created_at.date(),
                    'payment_method': d.payment_method,
                    'is_anonymous': d.is_anonymous,
                }
            )

        return Response(
            {
                'ndiguel_id': campaign.id,
                'ndiguel_name': campaign.name,
                'goal_amount': goal_amount,
                'collected_amount': collected_amount,
                'progress_pct': progress_pct,
                'donation_count': donation_count,
                'contributions': contributions,
            }
        )


class CampaignTodoViewSet(viewsets.ModelViewSet):
    queryset = CampaignTodo.objects.all().select_related('campaign').order_by('is_completed', '-created_at')
    serializer_class = CampaignTodoSerializer
    permission_classes = [IsCampaignOrganizer]
    filter_backends = [filters.SearchFilter]
    search_fields = ['title', 'description']

    def get_queryset(self):
        user = self.request.user
        qs = super().get_queryset()
        campaign_id = self.request.query_params.get('campaign')
        if campaign_id:
            qs = qs.filter(campaign_id=campaign_id)
        if user.role in ['admin', 'chef_daara']:
            return qs
        return qs.filter(campaign__organizer=user, campaign__deadline__gte=timezone.localdate()).exclude(campaign__status__in=['completed', 'inactive'])

    def perform_create(self, serializer):
        """La création vérifie le Ndiguel VISÉ, et pas seulement le rôle.

        🔴 Ce contrôle n'existait pas. `IsCampaignOrganizer.has_permission`
        n'examine que le rôle pour l'action `create`, et
        `has_object_permission` ne s'exécute JAMAIS sur une création — il n'y a
        pas encore d'objet. Or `CAMPAIGN_CREATOR_ROLES` inclut `member` :
        n'importe quel talubé pouvait donc écrire une tâche dans le Ndiguel de
        n'importe qui, y compris d'un autre Daara, et le serveur répondait 201.

        Deux conséquences, constatées le 2026-09-05 en éprouvant le contrat :

          · une écriture non autorisée dans le plan de travail d'autrui, que
            le responsable légitime voyait apparaître sans savoir d'où ;
          · une tâche que son propre auteur ne revoyait jamais —
            `get_queryset` la lui cache aussitôt, et le `PATCH` suivant
            renvoie 404. Un 201 qui ne laisse rien derrière lui est pire
            qu'un refus : il fait croire au travail enregistré.

        La règle appliquée est celle qui gouverne déjà la LECTURE des tâches
        (`CampaignSerializer.get_todos`) : `can_be_managed_by`. L'API refuse
        désormais ce qu'elle refusait déjà de montrer.
        """
        campaign = serializer.validated_data.get('campaign')
        if campaign and not campaign.can_be_managed_by(self.request.user):
            raise PermissionDenied(
                "Vous ne gérez pas ce Ndiguel : vous ne pouvez pas y ajouter de tâche."
            )
        serializer.save()
