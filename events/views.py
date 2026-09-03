from django.db.models import Sum
from django.utils import timezone

from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.models import User
from accounts.serializers import DirectoryUserSerializer
from comms.models import Notification
from comms.notify import notify, notify_many
from contributions.models import Donation

from .models import Campaign, CampaignTodo, Fete
from .serializers import CampaignSerializer, CampaignTodoSerializer, FeteSerializer


# Rôles autorisés à créer / gérer des campagnes
CAMPAIGN_CREATOR_ROLES = ('admin', 'chef_daara', 'collector', 'member')


class IsCampaignOrganizer(permissions.BasePermission):
    """
    - GET (liste/détail) : ouvert à tous les utilisateurs authentifiés.
    - POST (création)    : réservé aux rôles actifs de la confrérie (pas tutelle).
    - PUT/PATCH/DELETE   : réservé à l'organisateur désigné ou admin/chef_daara.
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

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        previous = self.get_object()
        previous_date = previous.date
        fete = serializer.save()

        # Notify community only when the fete date changes.
        if previous_date == fete.date or fete.date is None:
            return

        target_roles = [User.Role.MEMBER, User.Role.COLLECTOR, User.Role.CHEF_DAARA, User.Role.TUTELLE]
        # `only('id')` ne suffit plus : l'envoi a besoin de l'adresse et du
        # prénom. Deux colonnes de plus valent mieux qu'une requête par membre.
        targets = list(
            User.objects.filter(is_active=True, role__in=target_roles)
            .only('id', 'email', 'first_name')
        )
        formatted_date = fete.date.strftime('%d/%m/%Y')

        # Seul envoi de masse du produit : il part dans un fil séparé, sur une
        # seule connexion SMTP. Une boucle d'envois unitaires tiendrait la
        # requête de l'administrateur plusieurs minutes.
        notify_many(
            targets,
            code='fete_date_modifiee',
            titre=f"Prochaine {fete.name}",
            message=f"Prochaine {fete.name} le {formatted_date}.",
            contexte={'fete': fete.name, 'date': formatted_date},
        )

    @action(detail=True, methods=['get'], url_path='etat')
    def etat(self, request, pk=None):
        fete = self.get_object()
        from contributions.models import Donation

        campaigns = Campaign.objects.filter(fete=fete).select_related('daara', 'organizer').order_by('-created_at')
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
                'daara_name': c.daara.name if c.daara else None,
                'organizer_name': c.organizer.get_full_name().strip() if c.organizer else None,
            })

        return Response({
            'id': fete.id,
            'name': fete.name,
            'date': fete.date.isoformat() if fete.date else None,
            'description': fete.description,
            'recurrence': fete.get_recurrence_display(),
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
        campaign = serializer.save(organizer_assigned_at=assigned_at)
        if campaign.organizer and campaign.organizer != old_organizer:
            self._notify_organizer(campaign)

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
        return qs.filter(campaign__organizer=user, campaign__deadline__gte=timezone.now().date()).exclude(campaign__status__in=['completed', 'inactive'])
