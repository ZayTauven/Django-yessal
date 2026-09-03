from django.db.models import Sum, Count
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
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
    CustomRefreshToken,
    RegisterSerializer,
    LoginSerializer,
    UserSerializer,
    ProfileUpdateSerializer,
    LDDSerializer,
    DaaraSerializer,
    PublicDaaraSerializer,
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
import logging

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from comms.models import Notification
from comms.notify import administrateurs, nom_de, notify
from core.mail import send_to_user

User = get_user_model()

logger = logging.getLogger(__name__)


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    permission_classes = (permissions.AllowAny,)
    serializer_class = RegisterSerializer

    def perform_create(self, serializer):
        membre = serializer.save()
        # Accusé de réception : l'inscription reste `pending` jusqu'à
        # validation par un administrateur, et sans ce message le nouvel
        # inscrit n'a aucun moyen de savoir que sa demande est bien partie.
        # Pas de notification en base : le compte n'est pas encore actif, il
        # ne verra la liste qu'après validation.
        send_to_user(membre, 'inscription_recue', {
            'daara': membre.daara.name if membre.daara else None,
        })


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

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = ProfileUpdateSerializer(instance, data=request.data, partial=partial, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(UserSerializer(instance, context=self.get_serializer_context()).data)


class ChangePasswordView(APIView):
    """Changement de mot de passe par l'intéressé lui-même.

    Il n'existait aucun moyen pour un membre de changer son mot de passe :
    `ProfileUpdateSerializer` ne porte pas le champ, et seul le parcours « mot
    de passe oublié » — qui suppose une adresse e-mail valide — permettait d'en
    obtenir un nouveau. Or les comptes créés par un tiers reçoivent un mot de
    passe attribué, parfois commun à toute une promotion dans le cas de l'import
    Excel : leur demander de le remplacer supposait d'abord qu'ils le puissent.

    L'ancien mot de passe est exigé. Sans lui, un jeton volé suffirait à
    verrouiller un compte de façon définitive.
    """

    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request):
        current = request.data.get('current_password') or ''
        new = request.data.get('new_password') or ''

        if not current or not new:
            return Response(
                {'error': 'Mot de passe actuel et nouveau mot de passe requis.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = request.user

        if not user.check_password(current):
            return Response(
                {'error': 'Mot de passe actuel incorrect.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if new == current:
            return Response(
                {'error': 'Le nouveau mot de passe doit être différent de l\'ancien.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Les règles de robustesse de Django, et non les nôtres : longueur
        # minimale, mots de passe trop courants, similarité avec le nom ou
        # l'adresse. Les messages remontent déjà traduits.
        try:
            validate_password(new, user=user)
        except DjangoValidationError as exc:
            return Response({'error': ' '.join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(new)
        user.must_change_password = False
        # Toutes les sessions ouvertes tombent, y compris celle d'où part la
        # demande : quelqu'un qui change son mot de passe parce qu'il le croit
        # connu d'un autre veut d'abord que cet autre soit déconnecté.
        user.token_version += 1
        user.save(update_fields=['password', 'must_change_password', 'token_version'])

        # Courriel de sécurité : il part même quand tout va bien, parce que
        # c'est précisément le cas où il alerte. Quelqu'un dont le compte a été
        # pris n'a que ce message pour s'en apercevoir.
        maintenant = timezone.localtime()
        notify(
            user,
            code='mot_de_passe_modifie',
            titre='Mot de passe modifié',
            message='Votre mot de passe a été modifié. Vos autres sessions ont été fermées.',
            contexte={
                'date': maintenant.strftime('%d/%m/%Y'),
                'heure': maintenant.strftime('%Hh%M'),
            },
        )

        # … mais on ne met pas l'intéressé dehors pour autant : un jeton neuf
        # lui est remis dans la foulée, sur la nouvelle génération. Il reste
        # connecté ici, et nulle part ailleurs.
        refresh = CustomRefreshToken.for_user(user)

        return Response({
            'detail': 'Mot de passe modifié.',
            'access': str(refresh.access_token),
            'refresh': str(refresh),
        })


class LDDViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = LDDSerializer
    queryset = LDD.objects.filter(is_active=True).order_by('code')
    permission_classes = [permissions.AllowAny]


class DaaraViewSet(viewsets.ModelViewSet):
    serializer_class = DaaraSerializer
    queryset = Daara.objects.all()

    def get_serializer_class(self):
        """
        Un visiteur non authentifié reçoit la version réduite.

        La liste des Daaras doit rester lisible sans compte — le formulaire
        d'inscription s'en sert pour son menu déroulant. Elle répondait
        toutefois avec le sérialiseur complet : nom du chef, collecteurs
        nominatifs, effectifs. Ces champs ne servent qu'aux écrans internes,
        et n'ont donc rien à faire dans une réponse anonyme.
        """
        if self.action in {'list', 'retrieve'} and not self.request.user.is_authenticated:
            return PublicDaaraSerializer
        return super().get_serializer_class()

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

    @action(detail=True, methods=['get'], url_path='etat', permission_classes=[permissions.IsAdminUser])
    def etat(self, request, pk=None):
        daara = Daara.objects.select_related('ldd', 'chef', 'chef__title').get(pk=pk)

        def _avatar(u):
            if not u:
                return None
            if u.avatar:
                return request.build_absolute_uri(u.avatar.url)
            return u.avatar_url or None

        def _user_dict(u):
            return {
                'id': u.id,
                'first_name': u.first_name,
                'last_name': u.last_name,
                'email': u.email,
                'phone': u.phone,
                'role': u.role,
                'avatar': _avatar(u),
                'title_name': u.title.name if u.title else None,
            }

        members_qs = (
            User.objects
            .filter(daara=daara, is_active=True)
            .select_related('title')
            .order_by('role', 'last_name', 'first_name')
        )
        members_data = [_user_dict(u) for u in members_qs]
        collectors_data = [m for m in members_data if m['role'] == User.Role.COLLECTOR]

        # Chef comes from the Daara.chef FK (authoritative), fallback to role scan
        chef_data = None
        if daara.chef:
            chef_data = _user_dict(daara.chef)
        else:
            chef_data = next((m for m in members_data if m['role'] == User.Role.CHEF_DAARA), None)

        donations_qs = Donation.objects.filter(
            donor__daara=daara,
            payment_status=Donation.PaymentStatus.CONFIRMED,
        )
        total_collected = donations_qs.aggregate(total=Sum('amount'))['total'] or 0
        donation_count = donations_qs.count()

        campaigns_qs = Campaign.objects.filter(daara=daara).select_related('organizer').order_by('-created_at')
        campaigns_data = []
        for c in campaigns_qs:
            c_collected = (
                Donation.objects
                .filter(campaign=c, payment_status='confirmed')
                .aggregate(t=Sum('amount'))['t'] or 0
            )
            goal = float(c.goal_amount) if c.goal_amount else 0
            pct = round(float(c_collected) / goal * 100, 1) if goal > 0 else 0
            campaigns_data.append({
                'id': c.id,
                'name': c.name,
                'goal_amount': str(c.goal_amount) if c.goal_amount else None,
                'collected_amount': str(c_collected),
                'progress_pct': pct,
                'status': c.get_effective_status(),
                'deadline': str(c.deadline),
                'organizer_name': c.organizer.get_full_name() if c.organizer else None,
            })

        ldd = daara.ldd
        return Response({
            'id': daara.id,
            'name': daara.name,
            'is_active': daara.is_active,
            'created_at': daara.created_at.isoformat() if daara.created_at else None,
            'ldd': {'id': ldd.id, 'code': ldd.code, 'name': ldd.name} if ldd else None,
            'chef': chef_data,
            'members_count': members_qs.count(),
            'collectors_count': len(collectors_data),
            'total_collected': str(total_collected),
            'donation_count': donation_count,
            'campaigns_count': campaigns_qs.count(),
            'members': members_data,
            'collectors': collectors_data,
            'campaigns': campaigns_data,
        })

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
            self._notifier_promotion(target, request.user)
            return Response(DirectoryUserSerializer(target, context={'request': request}).data, status=status.HTTP_200_OK)

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
        self._notifier_promotion(target, request.user)
        return Response(DirectoryUserSerializer(target, context={'request': request}).data, status=status.HTTP_200_OK)

    @staticmethod
    def _notifier_promotion(promu, auteur):
        """La promotion se fait par deux chemins — admin, chef de Daara.

        Extrait en méthode pour que les deux disent la même chose : recopié,
        le message aurait divergé au premier ajustement.
        """
        notify(
            promu,
            code='promotion_collecteur',
            titre='Vous êtes désormais collecteur',
            message='Vous pouvez enregistrer les Jëfs remis en espèces.',
            contexte={
                'auteur': nom_de(auteur),
                'daara': promu.daara.name if promu.daara else None,
            },
        )


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

        demande = serializer.save(member=member)

        nom_membre = nom_de(member)
        for admin in administrateurs():
            notify(
                admin,
                code='titre_a_examiner',
                titre='Nouvelle demande de titre',
                message=f'{nom_membre} demande le titre de {demande.title.name}.',
                contexte={
                    'membre': nom_membre,
                    'daara': member.daara.name if member.daara else None,
                    'titre': demande.title.name,
                },
            )

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
        """Prévient les administrateurs, et accuse réception au déposant."""
        membre = nom_de(doc.user)
        type_document = doc.get_doc_type_display()
        daara = doc.user.daara.name if doc.user.daara else None

        for admin in administrateurs():
            notify(
                admin,
                code='document_a_valider',
                titre='Nouveau document soumis',
                message=f"{membre} a soumis le document : {type_document}.",
                contexte={'membre': membre, 'daara': daara,
                          'type_document': type_document, 'document': doc},
            )

        # Accusé au déposant : sans lui, il ne sait pas si son envoi est passé,
        # et redépose — d'où des doublons à trier côté administration.
        send_to_user(doc.user, 'document_recu', {
            'type_document': type_document, 'document': doc,
        })


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

        type_document = doc.get_doc_type_display()
        if doc.status == UserDocument.ValidationStatus.VALIDATED:
            notify(
                doc.user,
                code='document_valide',
                titre='Document validé',
                message=f'Votre {type_document} a été validé.',
                contexte={'type_document': type_document, 'document': doc},
            )
        elif doc.status == UserDocument.ValidationStatus.REJECTED:
            # Le motif est indispensable : sans lui, le membre redépose la
            # même photo et le cycle recommence.
            notify(
                doc.user,
                code='document_a_corriger',
                titre='Document à corriger',
                message=doc.rejection_note or f'Votre {type_document} doit être corrigé.',
                contexte={'type_document': type_document, 'document': doc,
                          'motif': doc.rejection_note},
            )

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

        notify(
            user,
            code='titre_attribue',
            titre='Un titre vous a été attribué',
            message=f'Vous portez désormais le titre de {title.name}.',
            contexte={'titre': title.name, 'auteur': nom_de(request.user)},
        )
        return Response(UserSerializer(user, context={'request': request}).data)


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
        import datetime
        from django.db.models import Q
        from django.db.models.functions import TruncMonth

        user = request.user
        role = user.role
        today = timezone.now().date()

        # Admin can request stats for a specific user
        target_user_id = request.query_params.get('user_id')
        if target_user_id and role == 'admin':
            try:
                user = User.objects.get(pk=target_user_id)
                role = user.role
            except User.DoesNotExist:
                pass

        confirmed_qs = Donation.objects.filter(payment_status='confirmed')
        all_campaigns = Campaign.objects.filter(status='active')

        if role == 'admin':
            all_donations = confirmed_qs
        elif role == 'chef_daara':
            if user.daara:
                all_donations = confirmed_qs.filter(donor__daara=user.daara)
                all_campaigns = all_campaigns.filter(daara=user.daara) | all_campaigns.filter(daara__isnull=True)
            else:
                all_donations = confirmed_qs.none()
        elif role == 'collector':
            all_donations = confirmed_qs.filter(collector=user)
        else:
            all_donations = confirmed_qs.filter(donor=user)

        # ── Current vs previous month for real change values ──────────────
        first_this_month = today.replace(day=1)
        first_last_month = (first_this_month - datetime.timedelta(days=1)).replace(day=1)

        amount_this_month = all_donations.filter(
            created_at__date__gte=first_this_month
        ).aggregate(Sum('amount'))['amount__sum'] or 0
        amount_last_month = all_donations.filter(
            created_at__date__gte=first_last_month,
            created_at__date__lt=first_this_month,
        ).aggregate(Sum('amount'))['amount__sum'] or 0

        if amount_last_month > 0:
            pct = ((amount_this_month - amount_last_month) / amount_last_month) * 100
            change_amount = f"{'+' if pct >= 0 else ''}{pct:.0f}%"
        elif amount_this_month > 0:
            change_amount = "Nouveau"
        else:
            change_amount = "—"

        total_amount = all_donations.aggregate(Sum('amount'))['amount__sum'] or 0
        donation_count = all_donations.count()
        active_campaigns_count = all_campaigns.count()

        count_this_month = all_donations.filter(created_at__date__gte=first_this_month).count()
        count_last_month = all_donations.filter(
            created_at__date__gte=first_last_month,
            created_at__date__lt=first_this_month,
        ).count()
        change_count = f"+{count_this_month - count_last_month}" if count_this_month >= count_last_month else str(count_this_month - count_last_month)

        # ── KPIs ──────────────────────────────────────────────────────────
        kpis = []
        if role == 'admin':
            members_count = User.objects.filter(status='active').count()
            kpis.append({'title': 'Total Collecté', 'value': f'{int(total_amount):,} FCFA', 'change': change_amount, 'icon': 'Wallet', 'href': '/dashboard/donations'})
            kpis.append({'title': 'Membres Actifs', 'value': str(members_count), 'change': f"{User.objects.filter(status='pending').count()} en attente", 'icon': 'Users', 'href': '/dashboard/admin/users'})
            kpis.append({'title': 'Contributions', 'value': str(donation_count), 'change': change_count, 'icon': 'HandCoins', 'href': '/dashboard/donations'})
            kpis.append({'title': 'Jëfs Actifs', 'value': str(active_campaigns_count), 'change': 'En cours', 'icon': 'Landmark', 'href': '/dashboard/campaigns'})
        elif role == 'chef_daara':
            daara_members = User.objects.filter(daara=user.daara, status='active').count() if user.daara else 0
            kpis.append({'title': 'Total Daara', 'value': f'{int(total_amount):,} FCFA', 'change': change_amount, 'icon': 'Wallet'})
            kpis.append({'title': 'Talibés', 'value': str(daara_members), 'change': 'Actifs', 'icon': 'Users'})
            kpis.append({'title': 'Contributions', 'value': str(donation_count), 'change': change_count, 'icon': 'HandCoins'})
            kpis.append({'title': 'Jëfs Actifs', 'value': str(active_campaigns_count), 'change': 'En cours', 'icon': 'Landmark'})
        elif role == 'collector':
            today_total = all_donations.filter(created_at__date=today).aggregate(Sum('amount'))['amount__sum'] or 0
            week_start = today - datetime.timedelta(days=today.weekday())
            week_total = all_donations.filter(created_at__date__gte=week_start).aggregate(Sum('amount'))['amount__sum'] or 0
            kpis.append({'title': 'Total Collecté', 'value': f'{int(total_amount):,} FCFA', 'change': change_amount, 'icon': 'Wallet'})
            kpis.append({'title': "Aujourd'hui", 'value': f'{int(today_total):,} FCFA', 'change': 'Journalier', 'icon': 'TrendingUp'})
            kpis.append({'title': 'Cette semaine', 'value': f'{int(week_total):,} FCFA', 'change': 'Hebdo', 'icon': 'HandCoins'})
            kpis.append({'title': 'Contributions', 'value': str(donation_count), 'change': change_count, 'icon': 'Landmark'})
        else:  # member
            kpis.append({'title': 'Mes Contributions', 'value': f'{int(total_amount):,} FCFA', 'change': change_amount, 'icon': 'Wallet'})
            kpis.append({'title': 'Nombre de dons', 'value': str(donation_count), 'change': change_count, 'icon': 'HandCoins'})
            kpis.append({'title': 'Jëfs Actifs', 'value': str(active_campaigns_count), 'change': 'En cours', 'icon': 'Landmark'})
            kpis.append({'title': 'Tutelles', 'value': str(user.tutelles.count()), 'change': 'Membres', 'icon': 'Users'})

        # ── Charts ────────────────────────────────────────────────────────
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
            if entry['payment_method'] in ['wave', 'orange_money', 'card', 'bictorys', 'visa', 'mastercard']:
                months_map[m_str]['online'] += int(entry['total'])
            else:
                months_map[m_str]['manual'] += int(entry['total'])
        bar_chart = list(months_map.values())

        method_labels = {
            'wave': 'Wave', 'orange_money': 'Orange Money',
            'manual': 'Espèces', 'virement': 'Virement',
            'bictorys': 'Bictorys', 'visa': 'Visa', 'mastercard': 'Mastercard',
            'collector': 'Collecteur',
        }
        # `amount` en plus de `dons` : sur un tableau de bord de collecte, la
        # question « par quel canal l'argent arrive-t-il » compte plus que le
        # nombre de transactions. Le front ventilait les COMPTES sous le
        # libellé « Collecté », ce qui donnait un anneau au centre duquel on
        # lisait « Collecté 43 » — quarante-trois francs, comprenait-on.
        # Les deux sont renvoyés : le montant pour la ventilation, le compte
        # pour le détail au survol.
        pie_chart = []
        for entry in all_donations.values('payment_method').annotate(
            dons=Count('id'), amount=Sum('amount')
        ):
            method = entry['payment_method']
            pie_chart.append({
                'method': method_labels.get(method, method.replace('_', ' ').title()),
                'dons': entry['dons'],
                'amount': int(entry['amount'] or 0),
                'fill': f"var(--chart-{len(pie_chart) + 1})",
            })

        area_chart = []
        for i in range(6, -1, -1):
            d = today - datetime.timedelta(days=i)
            day_total = all_donations.filter(created_at__date=d).aggregate(Sum('amount'))['amount__sum'] or 0
            area_chart.append({'name': d.strftime('%d/%m'), 'total': int(day_total)})

        # ── Donations trend for pie chart ─────────────────────────────────
        donations_trend = change_amount

        # ── Announcements ─────────────────────────────────────────────────
        from comms.models import Announcement
        from comms.serializers import AnnouncementSerializer
        announcements_qs = Announcement.objects.filter(is_published=True).order_by('-created_at')
        if role != 'admin':
            announcements_qs = announcements_qs.filter(Q(target='global') | Q(daara=user.daara))
        latest_announcements = AnnouncementSerializer(announcements_qs[:5], many=True).data

        # ── Collectors performance (chef_daara) ───────────────────────────
        collectors_data = []
        if role == 'chef_daara' and user.daara:
            for c in User.objects.filter(daara=user.daara, role='collector'):
                c_dons = confirmed_qs.filter(collector=c).count()
                collectors_data.append({
                    'id': c.id, 'first_name': c.first_name,
                    'last_name': c.last_name, 'donations_count': c_dons,
                })

        # ── Campaign donations (member view) ──────────────────────────────
        campaign_donations = []
        for c in all_campaigns:
            c_total = all_donations.filter(campaign=c).aggregate(Sum('amount'))['amount__sum'] or 0
            campaign_donations.append({'id': c.id, 'name': c.name, 'total': int(c_total)})

        # ── Recent collects (collector) ───────────────────────────────────
        recent_collects = []
        if role == 'collector':
            recent_qs = all_donations.select_related('donor', 'campaign').order_by('-created_at')[:10]
            for don in recent_qs:
                donor = don.donor
                recent_collects.append({
                    'id': don.id,
                    'donor_name': f"{donor.first_name} {donor.last_name}".strip() if donor else "—",
                    'campaign_name': don.campaign.name if don.campaign else None,
                    'amount': int(don.amount),
                    'created_at': don.created_at.isoformat(),
                    'status': don.payment_status,
                })

        # ── Alerts (admin only) ───────────────────────────────────────────
        alerts = []
        if role == 'admin':
            pending_users = User.objects.filter(status='pending').count()
            if pending_users > 0:
                alerts.append({
                    'id': 'pending_users', 'title': f"{pending_users} inscription(s) en attente",
                    'badge': 'Validation', 'severity': 'warning',
                })
            wire_pending = Donation.objects.filter(payment_status='pending_wire').count()
            if wire_pending > 0:
                alerts.append({
                    'id': 'wire_pending', 'title': f"{wire_pending} virement(s) à confirmer",
                    'badge': 'Virement', 'severity': 'critical',
                })
            ending_soon = Campaign.objects.filter(
                status='active', deadline__lte=today + datetime.timedelta(days=7)
            ).count()
            if ending_soon > 0:
                alerts.append({
                    'id': 'ending_soon', 'title': f"{ending_soon} Jëf(s) se terminent dans 7 jours",
                    'badge': 'Échéance', 'severity': 'info',
                })
            doc_pending = UserDocument.objects.filter(status='pending').count()
            if doc_pending > 0:
                alerts.append({
                    'id': 'doc_pending', 'title': f"{doc_pending} document(s) à valider",
                    'badge': 'Documents', 'severity': 'info',
                })

        # ── Members evolution (admin) ─────────────────────────────────────
        members_evolution = []
        if role == 'admin':
            for i in range(6, -1, -1):
                d = today - datetime.timedelta(days=i)
                count = User.objects.filter(date_joined__date__lte=d, status='active').count()
                members_evolution.append({'name': d.strftime('%d/%m'), 'total': count})

        return Response({
            'role': role,
            'kpis': kpis,
            'bar_chart': bar_chart,
            'pie_chart': pie_chart,
            'area_chart': area_chart,
            'chartData': area_chart,
            'members_evolution': members_evolution if role == 'admin' else area_chart,
            'donations_trend': donations_trend,
            'campaign_donations': campaign_donations,
            'daara': user.daara.name if user.daara else None,
            'announcements': latest_announcements,
            'collectors': collectors_data,
            'recent_collects': recent_collects,
            'alerts': alerts,
        })

    @action(detail=False, methods=['get'], url_path='campaign-metrics', permission_classes=[permissions.IsAdminUser])
    def campaign_metrics(self, request):
        campaigns = Campaign.objects.filter(organizer__isnull=False).select_related('organizer')
        data = []
        for c in campaigns:
            collected = Donation.objects.filter(campaign=c, payment_status='confirmed').aggregate(Sum('amount'))['amount__sum'] or 0
            tasks_total = c.todos.count()
            tasks_completed = c.todos.filter(is_completed=True).count()
            # Trois compteurs, UNE seule convention.
            #
            # `days_total` comptait le jour d'ouverture (+1) quand
            # `days_remaining` ne comptait pas celui de l'échéance : sur un
            # Ndiguel ouvert le jour même, l'écran affichait « 0 / 41 jours,
            # reste 40 ». Zéro plus quarante ne fait pas quarante et un, et un
            # responsable qui lit « jour 0 » se demande si sa mission a démarré.
            #
            # Désormais : le premier jour est le jour 1, l'échéance est comprise
            # dans le décompte restant, et `écoulés + restants == total`.
            today = timezone.now().date()
            anchor_date = c.organizer_assigned_at.date() if c.organizer_assigned_at else c.created_at.date()
            days_total = max(1, (c.deadline - anchor_date).days + 1)
            days_remaining = max(0, (c.deadline - today).days)
            # Borné par le total : une échéance dépassée ne doit pas afficher
            # « jour 58 sur 41 ».
            days_active = min(days_total, max(1, (today - anchor_date).days + 1))

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
                    'days_active': days_active,
                    'days_total': days_total,
                    'days_remaining': days_remaining,
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
    queryset = User.objects.select_related('daara__ldd', 'title').all().order_by('-date_joined')
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAdminUser]
    filter_backends = [filters.SearchFilter]
    search_fields = ['first_name', 'last_name', 'email', 'phone', 'daara__name']

    @action(detail=True, methods=['post'])
    def validate(self, request, pk=None):
        user = self.get_object()
        user.status = User.Status.ACTIVE
        user.save(update_fields=['status'])

        notify(
            user,
            code='compte_valide',
            titre='Votre compte est actif',
            message='Votre compte a été validé. Vous pouvez vous connecter.',
            contexte={'daara': user.daara.name if user.daara else None},
        )
        return Response({'status': 'user validated'})

    @action(detail=True, methods=['post'])
    def block(self, request, pk=None):
        user = self.get_object()
        user.status = User.Status.BLOCKED
        user.save(update_fields=['status'])

        # Le blocage est le plus souvent administratif — doublon, compte de
        # test — rarement une sanction. Le message ne doit pas accuser.
        notify(
            user,
            code='compte_bloque',
            titre='Accès suspendu',
            message=(
                "L'accès à votre compte a été suspendu. Vos Jëfs enregistrés "
                'restent comptabilisés.'
            ),
            contexte={'daara': user.daara.name if user.daara else None,
                      'motif': ''},
        )
        return Response({'status': 'user blocked'})

    @action(detail=True, methods=['get'], url_path='tutelle')
    def tutelle(self, request, pk=None):
        user = self.get_object()
        tutelles = Tutelle.objects.filter(tutor=user).select_related('linked_user')
        return Response(TutelleSerializer(tutelles, many=True, context={'request': request}).data)

    def perform_create(self, serializer):
        serializer.save(status=User.Status.ACTIVE)


class ForgotPasswordView(generics.GenericAPIView):
    """Demande de réinitialisation : envoie un lien à usage unique.

    ═══════════════════════════════════════════════════════════════════════
    La réponse est la MÊME que le compte existe ou non
    ═══════════════════════════════════════════════════════════════════════
    Répondre « aucun compte avec cette adresse » transformerait cet endpoint
    public en oracle : on y teste des adresses jusqu'à savoir lesquelles sont
    inscrites. Sur une plateforme d'appartenance religieuse, cette information
    n'est pas anodine. Le message et le code de retour sont donc invariants,
    et seul l'envoi diffère.

    Le jeton vient de `default_token_generator` : dérivé du mot de passe et de
    `last_login`, il devient caduc dès qu'on s'en sert, sans rien à stocker.
    """

    permission_classes = (permissions.AllowAny,)
    throttle_scope = 'mot_de_passe_demande'

    REPONSE = {'detail': 'Un email de récupération a été envoyé si le compte existe.'}

    def post(self, request):
        email = (request.data.get('email') or '').strip()
        if not email:
            return Response({'detail': 'Email requis.'}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user:
            jeton = default_token_generator.make_token(user)
            identifiant = urlsafe_base64_encode(force_bytes(user.pk))
            lien = f"{settings.BASE_URL}/reset-password?uid={identifiant}&token={jeton}"

            heures = max(1, settings.PASSWORD_RESET_TIMEOUT // 3600)
            send_to_user(user, 'mot_de_passe_oublie', {
                'lien_reinitialisation': lien,
                'duree_validite': f"{heures} heures" if heures > 1 else "1 heure",
            })
        else:
            # Journalisé, jamais renvoyé : utile pour distinguer « personne
            # n'a demandé » de « la demande n'aboutit pas ».
            logger.info("Réinitialisation demandée pour une adresse inconnue.")

        return Response(self.REPONSE)


class ResetPasswordConfirmView(generics.GenericAPIView):
    """Pose le nouveau mot de passe, contre un jeton valide.

    Second temps du parcours : le premier envoie le lien, celui-ci l'échange.
    Sans lui, le courriel menait à une page qui n'avait rien à appeler.
    """

    permission_classes = (permissions.AllowAny,)
    throttle_scope = 'mot_de_passe_reset'

    def post(self, request):
        identifiant = request.data.get('uid') or ''
        jeton = request.data.get('token') or ''
        nouveau = request.data.get('new_password') or ''

        if not identifiant or not jeton or not nouveau:
            return Response(
                {'detail': 'Lien incomplet. Refaites une demande depuis la page de connexion.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            pk = force_str(urlsafe_base64_decode(identifiant))
            user = User.objects.get(pk=pk, is_active=True)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            # Message volontairement identique à celui du jeton invalide : un
            # identifiant mal formé ne doit pas se distinguer d'un jeton périmé.
            return Response(
                {'detail': 'Ce lien n\'est plus valable. Refaites une demande.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not default_token_generator.check_token(user, jeton):
            return Response(
                {'detail': 'Ce lien n\'est plus valable. Refaites une demande.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            validate_password(nouveau, user=user)
        except DjangoValidationError as exc:
            return Response({'detail': ' '.join(exc.messages)},
                            status=status.HTTP_400_BAD_REQUEST)

        user.set_password(nouveau)
        # Le drapeau s'éteint : le mot de passe est désormais choisi par
        # l'intéressé, plus imposé par un tiers.
        user.must_change_password = False
        # Toutes les sessions tombent. On réinitialise souvent parce qu'on
        # soupçonne un accès non désiré : le laisser ouvert viderait la
        # démarche de son sens.
        user.token_version += 1
        user.save(update_fields=['password', 'must_change_password', 'token_version'])

        maintenant = timezone.localtime()
        notify(
            user,
            code='mot_de_passe_modifie',
            titre='Mot de passe modifié',
            message='Votre mot de passe a été réinitialisé. Vos sessions ont été fermées.',
            contexte={
                'date': maintenant.strftime('%d/%m/%Y'),
                'heure': maintenant.strftime('%Hh%M'),
            },
        )

        return Response({'detail': 'Mot de passe réinitialisé. Vous pouvez vous connecter.'})


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

