from django.db.models import Sum, Count
from django.contrib.auth import get_user_model
from django.utils import timezone

from rest_framework import generics, permissions, status, viewsets, filters, serializers
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.views import APIView

from .models import (
    Daara,
    Tutelle,
    AuditLog,
    LDD,
    PilotageSettings,
    MemberTitle,
    TitleRequest,
    UserDocument,
)
from .serializers import (
    RegisterSerializer,
    LoginSerializer,
    UserSerializer,
    ProfileUpdateSerializer,
    LDDSerializer,
    DaaraSerializer,
    TutelleSerializer,
    AuditLogSerializer,
    DirectoryUserSerializer,
    PilotageSettingsSerializer,
    MemberTitleSerializer,
    TitleRequestSerializer,
    TitleRequestReviewSerializer,
    UserDocumentSerializer,
    UserDocumentValidationSerializer,
)
from .services.title_service import approve_title_request, refuse_title_request

from events.models import Campaign
from contributions.models import Donation
from comms.models import Notification

User = get_user_model()


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    permission_classes = (permissions.AllowAny,)
    serializer_class = RegisterSerializer


class CustomTokenObtainPairView(APIView):
    permission_classes = (permissions.AllowAny,)

    def post(self, request, *args, **kwargs):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        return Response(
            {
                'refresh': serializer.validated_data['refresh'],
                'access': serializer.validated_data['access'],
                'role': user.role,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'email': user.email,
                'phone': user.phone,
            },
            status=status.HTTP_200_OK,
        )


class ProfileView(generics.RetrieveUpdateAPIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get_serializer_class(self):
        if self.request.method in ('PUT', 'PATCH'):
            return ProfileUpdateSerializer
        return UserSerializer

    def get_object(self):
        return self.request.user

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['request'] = self.request
        return ctx


class LDDViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = LDDSerializer
    queryset = LDD.objects.filter(is_active=True).order_by('code')
    permission_classes = [permissions.AllowAny]


class DaaraViewSet(viewsets.ModelViewSet):
    serializer_class = DaaraSerializer
    queryset = Daara.objects.all()

    def get_permissions(self):
        if self.action in {'list', 'retrieve'}:
            return [permissions.AllowAny()]
        if self.action in {'create', 'update', 'partial_update', 'destroy', 'import_excel'}:
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]

    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        ldd_id = data.pop('ldd_id', None)
        if not ldd_id:
            return Response({'error': 'Le champ ldd_id (LDD) est obligatoire.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            ldd = LDD.objects.get(pk=ldd_id)
        except LDD.DoesNotExist:
            return Response({'error': 'LDD introuvable.'}, status=status.HTTP_400_BAD_REQUEST)
        daara = Daara.objects.create(
            name=data.get('name', '').strip(),
            ldd=ldd,
            is_active=data.get('is_active', True),
        )
        return Response(DaaraSerializer(daara).data, status=status.HTTP_201_CREATED)

    def get_queryset(self):
        qs = super().get_queryset().annotate(members_count=Count('members'))
        if self.request.user.is_authenticated:
            return qs
        return qs.filter(is_active=True)

    @action(detail=False, methods=['post'], url_path='import-excel')
    def import_excel(self, request):
        if 'file' not in request.FILES:
            return Response({'error': 'Aucun fichier fourni.'}, status=status.HTTP_400_BAD_REQUEST)

        excel_file = request.FILES['file']
        try:
            import pandas as pd

            df = pd.read_excel(excel_file)
            required_columns = {'DAARA', 'LDD', 'CODE LDD'}
            if not required_columns.issubset(set(df.columns)):
                return Response(
                    {'error': "Format invalide. Les colonnes 'DAARA', 'LDD' et 'CODE LDD' sont requises."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            df[['LDD', 'CODE LDD']] = df[['LDD', 'CODE LDD']].ffill()

            daara_count = 0
            for _, row in df.iterrows():
                if pd.isna(row['DAARA']):
                    continue
                daara_name = str(row['DAARA']).strip()
                ldd_name = str(row['LDD']).strip()
                ldd_code = str(row['CODE LDD']).strip()

                if not daara_name:
                    continue

                ldd, _ = LDD.objects.get_or_create(code=ldd_code, defaults={'name': ldd_name})
                Daara.objects.get_or_create(name=daara_name, ldd=ldd)
                daara_count += 1

            return Response({'success': f'{daara_count} Daaras importés ou mis à jour avec succès.'})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


COMMUNITY_ROLES = (
    User.Role.MEMBER,
    User.Role.CHEF_DAARA,
    User.Role.COLLECTOR,
)


class DirectoryUserViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = DirectoryUserSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['first_name', 'last_name', 'email', 'phone']

    def get_queryset(self):
        user = self.request.user
        base = User.objects.filter(role__in=COMMUNITY_ROLES).select_related('daara', 'title')
        if user.role == User.Role.ADMIN:
            return base.order_by('last_name', 'first_name')

        if user.role in (User.Role.CHEF_DAARA, User.Role.COLLECTOR, User.Role.MEMBER):
            # Multi-Daara collector support: no daara restriction for collectors.
            if user.role == User.Role.COLLECTOR:
                return base.order_by('last_name', 'first_name')
            if not user.daara_id:
                return User.objects.none()
            return base.filter(daara_id=user.daara_id).order_by('last_name', 'first_name')

        return User.objects.none()

    @action(detail=True, methods=['post'], url_path='promote-collector')
    def promote_collector(self, request, pk=None):
        target = self.get_object()
        if target.role != User.Role.MEMBER:
            return Response({'detail': 'Seuls les membres peuvent être promus collecteur.'}, status=status.HTTP_400_BAD_REQUEST)

        if request.user.role == User.Role.ADMIN:
            target.role = User.Role.COLLECTOR
            target.save(update_fields=['role'])
            return Response(DirectoryUserSerializer(target).data, status=status.HTTP_200_OK)

        if request.user.role != User.Role.CHEF_DAARA:
            return Response(
                {'detail': 'Seuls le chef de Daara ou un administrateur peuvent nommer un collecteur.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not request.user.daara_id:
            return Response({'detail': 'Aucun Daara associé à votre compte.'}, status=status.HTTP_400_BAD_REQUEST)
        if target.daara_id != request.user.daara_id:
            return Response({'detail': 'Ce membre n’appartient pas à votre Daara.'}, status=status.HTTP_403_FORBIDDEN)

        target.role = User.Role.COLLECTOR
        target.save(update_fields=['role'])
        return Response(DirectoryUserSerializer(target).data, status=status.HTTP_200_OK)


class MemberTitleViewSet(viewsets.ModelViewSet):
    serializer_class = MemberTitleSerializer
    queryset = MemberTitle.objects.all().order_by('name')

    def get_permissions(self):
        if self.action in {'list', 'retrieve'}:
            return [permissions.IsAuthenticated()]
        return [permissions.IsAdminUser()]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_staff or self.request.user.role == User.Role.ADMIN:
            return qs
        return qs.filter(is_active=True)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class TitleRequestViewSet(viewsets.ModelViewSet):
    serializer_class = TitleRequestSerializer
    queryset = TitleRequest.objects.select_related('member', 'title', 'reviewed_by').all()

    def get_permissions(self):
        if self.action in {'create'}:
            return [permissions.IsAuthenticated()]
        if self.action in {'list', 'retrieve', 'review'}:
            return [permissions.IsAuthenticated()]
        return [permissions.IsAdminUser()]

    def get_queryset(self):
        user = self.request.user
        qs = super().get_queryset()
        if user.role == User.Role.ADMIN or user.is_staff:
            return qs
        return qs.filter(member=user)

    def perform_create(self, serializer):
        member = self.request.user
        if member.title_change_count >= 1:
            raise serializers.ValidationError({'detail': 'Vous avez déjà utilisé votre unique changement de titre.'})

        pending_exists = TitleRequest.objects.filter(member=member, status=TitleRequest.Status.PENDING).exists()
        if pending_exists:
            raise serializers.ValidationError({'detail': 'Une demande de titre est déjà en attente.'})

        serializer.save(member=member)

    @action(detail=True, methods=['patch'], url_path='review', permission_classes=[permissions.IsAdminUser])
    def review(self, request, pk=None):
        tr = self.get_object()
        if tr.status != TitleRequest.Status.PENDING:
            return Response({'detail': 'Cette demande a déjà été traitée.'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = TitleRequestReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action_name = serializer.validated_data['action']
        note = serializer.validated_data.get('note', '')

        if action_name == 'approve':
            tr = approve_title_request(tr, request.user, note=note)
        else:
            tr = refuse_title_request(tr, request.user, note=note)

        return Response(TitleRequestSerializer(tr).data)


class UserDocumentViewSet(viewsets.ModelViewSet):
    serializer_class = UserDocumentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user_id = self.kwargs.get('user_id')
        qs = UserDocument.objects.select_related('user', 'validated_by').all()
        if self.request.user.role == User.Role.ADMIN or self.request.user.is_staff:
            if user_id:
                return qs.filter(user_id=user_id)
            return qs
        return qs.filter(user=self.request.user)

    def perform_create(self, serializer):
        user_id = self.kwargs.get('user_id')
        if self.request.user.role == User.Role.ADMIN and user_id:
            doc = serializer.save(user_id=user_id)
            self._notify_admins_document_submission(doc)
            return
        doc = serializer.save(user=self.request.user)
        self._notify_admins_document_submission(doc)

    def perform_update(self, serializer):
        doc = serializer.save()
        if self.request.user.role != User.Role.ADMIN and not self.request.user.is_staff:
            self._notify_admins_document_submission(doc)

    def _notify_admins_document_submission(self, doc: UserDocument):
        admin_users = User.objects.filter(role=User.Role.ADMIN, is_active=True)
        member_name = doc.user.get_full_name().strip() or doc.user.email or doc.user.phone
        doc_type = doc.get_doc_type_display()
        for admin in admin_users:
            Notification.objects.create(
                user=admin,
                title='Nouveau document soumis',
                message=f"{member_name} a soumis le document: {doc_type}.",
            )


class PendingDocumentValidationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = UserDocumentSerializer
    permission_classes = [permissions.IsAdminUser]
    queryset = UserDocument.objects.filter(status=UserDocument.ValidationStatus.PENDING).select_related('user')


class DocumentValidationView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def patch(self, request, pk):
        try:
            doc = UserDocument.objects.get(pk=pk)
        except UserDocument.DoesNotExist:
            return Response({'detail': 'Document introuvable.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = UserDocumentValidationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        doc.status = serializer.validated_data['status']
        doc.rejection_note = serializer.validated_data.get('rejection_note', '')
        doc.validated_by = request.user
        doc.validated_at = timezone.now()
        doc.save(update_fields=['status', 'rejection_note', 'validated_by', 'validated_at'])
        return Response(UserDocumentSerializer(doc).data)


class MemberAssignTitleView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, pk):
        user = User.objects.filter(pk=pk).first()
        if not user:
            return Response({'detail': 'Membre introuvable.'}, status=status.HTTP_404_NOT_FOUND)

        title_id = request.data.get('title_id')
        title = MemberTitle.objects.filter(pk=title_id, is_active=True).first()
        if not title:
            return Response({'detail': 'Titre invalide.'}, status=status.HTTP_400_BAD_REQUEST)

        user.title = title
        user.title_change_count = user.title_change_count + 1
        user.title_changed_at = timezone.now()
        user.save(update_fields=['title', 'title_change_count', 'title_changed_at'])
        return Response(UserSerializer(user).data)


class TutelleViewSet(viewsets.ModelViewSet):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = TutelleSerializer

    def get_queryset(self):
        return Tutelle.objects.filter(tutor=self.request.user)

    def perform_create(self, serializer):
        serializer.save(tutor=self.request.user)


class AnalyticsViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        user = request.user
        role = user.role

        all_donations = Donation.objects.filter(payment_status='confirmed')
        all_campaigns = Campaign.objects.filter(status='active')

        if role == 'admin':
            pass
        elif role == 'chef_daara':
            if user.daara:
                all_donations = all_donations.filter(donor__daara=user.daara)
                all_campaigns = all_campaigns.filter(daara=user.daara) | all_campaigns.filter(daara__isnull=True)
        elif role == 'collector':
            all_donations = all_donations.filter(collector=user)
        else:
            all_donations = all_donations.filter(donor=user)

        total_amount = all_donations.aggregate(Sum('amount'))['amount__sum'] or 0
        donation_count = all_donations.count()
        active_campaigns_count = all_campaigns.count()

        kpis = []
        if role != 'member':
            kpis.append({'title': 'Total Dons', 'value': f'{int(total_amount):,} FCFA', 'change': '+12%', 'icon': 'Wallet'})

        kpis.extend(
            [
                {'title': 'Contributions', 'value': str(donation_count), 'change': '+2', 'icon': 'HandCoins'},
                {'title': 'Campagnes', 'value': str(active_campaigns_count), 'change': 'Actives', 'icon': 'Landmark'},
            ]
        )

        if role == 'collector':
            today_total = all_donations.filter(created_at__date=timezone.now().date()).aggregate(Sum('amount'))['amount__sum'] or 0
            kpis.append({'title': "Aujourd'hui", 'value': f'{int(today_total):,} FCFA', 'change': 'Journalier', 'icon': 'TrendingUp'})
        else:
            kpis.append({'title': 'Tutelles', 'value': str(user.tutelles.count()), 'change': 'Membres', 'icon': 'Users'})

        import datetime
        from django.db.models.functions import TruncMonth

        bar_chart = []
        six_months_ago = timezone.now() - datetime.timedelta(days=180)
        monthly_stats = (
            all_donations.filter(created_at__gte=six_months_ago)
            .annotate(month=TruncMonth('created_at'))
            .values('month', 'payment_method')
            .annotate(total=Sum('amount'))
            .order_by('month')
        )

        months_map = {}
        for entry in monthly_stats:
            m_str = entry['month'].strftime('%b')
            if m_str not in months_map:
                months_map[m_str] = {'month': m_str, 'online': 0, 'manual': 0}

            if entry['payment_method'] in ['wave', 'orange_money', 'card', 'bictorys']:
                months_map[m_str]['online'] += int(entry['total'])
            else:
                months_map[m_str]['manual'] += int(entry['total'])
        bar_chart = list(months_map.values())

        method_stats = all_donations.values('payment_method').annotate(dons=Count('id'))
        pie_chart = []
        method_labels = {
            'wave': 'Wave',
            'orange_money': 'Orange Money',
            'manual': 'Espèces',
            'virement': 'Virement',
            'bictorys': 'Bictorys',
        }
        for entry in method_stats:
            method = entry['payment_method']
            pie_chart.append(
                {
                    'method': method_labels.get(method, method.replace('_', ' ').title()),
                    'dons': entry['dons'],
                    'fill': f"var(--chart-{len(pie_chart) + 1})",
                }
            )

        area_chart = []
        today = timezone.now().date()
        for i in range(6, -1, -1):
            d = today - datetime.timedelta(days=i)
            day_total = all_donations.filter(created_at__date=d).aggregate(Sum('amount'))['amount__sum'] or 0
            area_chart.append({'name': d.strftime('%d/%m'), 'total': day_total})

        from comms.models import Announcement
        from comms.serializers import AnnouncementSerializer

        announcements_qs = Announcement.objects.filter(is_published=True).order_by('-created_at')
        if role != 'admin':
            from django.db.models import Q

            announcements_qs = announcements_qs.filter(Q(target='global') | Q(daara=user.daara))
        latest_announcements = AnnouncementSerializer(announcements_qs[:3], many=True).data

        collectors_data = []
        if role == 'chef_daara' and user.daara:
            collectors = User.objects.filter(daara=user.daara, role='collector')
            for c in collectors:
                c_dons = Donation.objects.filter(collector=c, payment_status='confirmed').count()
                collectors_data.append(
                    {
                        'id': c.id,
                        'first_name': c.first_name,
                        'last_name': c.last_name,
                        'donations_count': c_dons,
                    }
                )

        campaign_donations = []
        for c in all_campaigns:
            c_total = all_donations.filter(campaign=c).aggregate(Sum('amount'))['amount__sum'] or 0
            campaign_donations.append({'id': c.id, 'name': c.name, 'total': c_total})

        return Response(
            {
                'role': role,
                'kpis': kpis,
                'bar_chart': bar_chart,
                'pie_chart': pie_chart,
                'area_chart': area_chart,
                'chartData': area_chart,
                'campaign_donations': campaign_donations,
                'daara': user.daara.name if user.daara else None,
                'announcements': latest_announcements,
                'collectors': collectors_data,
            }
        )

    @action(detail=False, methods=['get'], url_path='campaign-metrics', permission_classes=[permissions.IsAdminUser])
    def campaign_metrics(self, request):
        campaigns = Campaign.objects.filter(organizer__isnull=False).select_related('organizer')
        data = []
        for c in campaigns:
            collected = Donation.objects.filter(campaign=c, payment_status='confirmed').aggregate(Sum('amount'))['amount__sum'] or 0
            tasks_total = c.todos.count()
            tasks_completed = c.todos.filter(is_completed=True).count()
            anchor_date = c.organizer_assigned_at.date() if c.organizer_assigned_at else c.created_at.date()
            days_active = (timezone.now().date() - anchor_date).days
            days_total = max(1, (c.deadline - anchor_date).days + 1)

            data.append(
                {
                    'id': c.id,
                    'organizer_name': c.organizer.get_full_name() or c.organizer.email or c.organizer.phone,
                    'organizer_role': c.organizer.role,
                    'campaign_name': c.name,
                    'collected_amount': collected,
                    'goal_amount': c.goal_amount,
                    'objective': c.objective,
                    'tasks_total': tasks_total,
                    'tasks_completed': tasks_completed,
                    'days_active': max(0, days_active),
                    'days_total': days_total,
                    'days_remaining': max(0, (c.deadline - timezone.now().date()).days),
                    'chat_count': c.chats.count(),
                    'status': c.get_effective_status(),
                }
            )
        return Response(data)


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AuditLog.objects.all().order_by('-created_at')
    serializer_class = AuditLogSerializer
    permission_classes = [permissions.IsAdminUser]


class UserManagementViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by('-date_joined')
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAdminUser]
    filter_backends = [filters.SearchFilter]
    search_fields = ['first_name', 'last_name', 'email', 'phone', 'daara__name']

    @action(detail=True, methods=['post'])
    def validate(self, request, pk=None):
        user = self.get_object()
        user.status = User.Status.ACTIVE
        user.save(update_fields=['status'])
        return Response({'status': 'user validated'})

    @action(detail=True, methods=['post'])
    def block(self, request, pk=None):
        user = self.get_object()
        user.status = User.Status.BLOCKED
        user.save(update_fields=['status'])
        return Response({'status': 'user blocked'})

    def perform_create(self, serializer):
        serializer.save(status=User.Status.ACTIVE)


class ForgotPasswordView(generics.GenericAPIView):
    permission_classes = (permissions.AllowAny,)

    def post(self, request):
        email = request.data.get('email')
        if not email:
            return Response({'detail': 'Email requis.'}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'detail': 'Un email de récupération a été envoyé si le compte existe.'})


class PilotageSettingsViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        settings = PilotageSettings.load()
        return Response(PilotageSettingsSerializer(settings).data)

    def create(self, request):
        if request.user.role != User.Role.ADMIN:
            return Response({'detail': 'Seuls les administrateurs peuvent modifier ces paramètres.'}, status=status.HTTP_403_FORBIDDEN)

        settings = PilotageSettings.load()
        serializer = PilotageSettingsSerializer(settings, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

