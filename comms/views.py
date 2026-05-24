from django.contrib.auth import get_user_model
from django.db import transaction
from datetime import datetime, timezone as dt_timezone
from django.db.models import Count, F, OuterRef, Prefetch, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Announcement,
    Chat,
    ChatInvitation,
    ChatMembership,
    FCMToken,
    Message,
    MessageReaction,
    MessageReadReceipt,
    MessagingPilotageConfig,
    Notification,
    UserMessagingPreferences,
)
from .serializers import (
    AnnouncementSerializer,
    ChatInvitationSerializer,
    ChatSerializer,
    CreateGroupChatSerializer,
    MessageSerializer,
    MessagingPilotageConfigSerializer,
    NotificationSerializer,
    UserBriefSerializer,
    UserMessagingPreferencesSerializer,
)
from .services import can_invite, get_effective_config, pusher_client

User = get_user_model()

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def pusher_auth(request):
    """
    Endpoint pour authentifier les utilisateurs sur Pusher pour les canaux privés et de présence.
    """
    channel_name = request.data.get('channel_name')
    socket_id = request.data.get('socket_id')

    if not channel_name or not socket_id:
        return Response({'detail': 'Paramètres channel_name et socket_id requis.'}, status=status.HTTP_400_BAD_REQUEST)

    # 1. Canal privé de l'utilisateur
    if channel_name.startswith('private-user.'):
        user_id = channel_name.split('.')[-1]
        if str(request.user.id) != user_id:
            raise PermissionDenied("Vous n'êtes pas autorisé à écouter ce canal.")
    
    # 2. Canal privé ou présence d'un chat
    elif channel_name.startswith('private-chat.') or channel_name.startswith('presence-chat.'):
        chat_id = channel_name.split('.')[-1]
        # Vérifier si l'utilisateur est membre du chat
        if not ChatMembership.objects.filter(chat_id=chat_id, user=request.user).exists():
            raise PermissionDenied("Vous n'êtes pas membre de ce chat.")
    
    else:
        raise PermissionDenied("Canal non reconnu ou non autorisé.")

    # Générer la signature d'autorisation
    custom_data = {
        'user_id': str(request.user.id),
        'user_info': {
            'name': request.user.get_full_name() or request.user.email or str(request.user.id),
            'email': request.user.email or "",
        }
    }
    
    try:
        if not pusher_client:
            return Response({'detail': 'Client Pusher indisponible sur le serveur.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        auth = pusher_client.authenticate(
            channel=channel_name,
            socket_id=socket_id,
            custom_data=custom_data
        )
        return Response(auth)
    except Exception as e:
        return Response({'detail': f'Erreur Pusher: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ChatViewSet(viewsets.ModelViewSet):
    serializer_class = ChatSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        chat_ids = ChatMembership.objects.filter(user=user).values_list('chat_id', flat=True)
        last_read_subquery = ChatMembership.objects.filter(
            chat_id=OuterRef('pk'),
            user=user
        ).values('last_read_at')[:1]
        default_last_read = Value(datetime(1970, 1, 1, tzinfo=dt_timezone.utc))

        last_message_qs = Message.objects.filter(chat_id=OuterRef('pk')).order_by('-sent_at')
        memberships_qs = ChatMembership.objects.select_related('user', 'user__daara')

        return (
            Chat.objects.filter(id__in=chat_ids)
            .select_related('campaign', 'daara')
            .prefetch_related(Prefetch('memberships', queryset=memberships_qs))
            .annotate(
                members_count_annotated=Count('memberships', distinct=True),
                unread_count_annotated=Count(
                    'messages',
                    filter=Q(
                        messages__sent_at__gt=Coalesce(Subquery(last_read_subquery), default_last_read)
                    ),
                    distinct=True,
                ),
                last_message_id=Subquery(last_message_qs.values('id')[:1]),
                last_message_content=Subquery(last_message_qs.values('content')[:1]),
                last_message_sent_at=Subquery(last_message_qs.values('sent_at')[:1]),
                last_message_type=Subquery(last_message_qs.values('message_type')[:1]),
                last_message_deleted=Subquery(last_message_qs.values('is_deleted')[:1]),
                last_message_sender_first_name=Subquery(last_message_qs.values('sender__first_name')[:1]),
                last_message_sender_last_name=Subquery(last_message_qs.values('sender__last_name')[:1]),
                last_message_sender_email=Subquery(last_message_qs.values('sender__email')[:1]),
            )
            .order_by('-created_at')
        )

    def create(self, request, *args, **kwargs):
        # Création de groupe de discussion
        config = get_effective_config(request.user)
        if not config.allow_group_creation:
            raise PermissionDenied("La création de groupe est désactivée par la configuration de pilotage.")

        serializer = CreateGroupChatSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        chat = serializer.save()
        return Response(ChatSerializer(chat, context={'request': request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='read')
    def mark_read(self, request, pk=None):
        """Met à jour le timestamp de lecture de l'utilisateur."""
        chat = self.get_object()
        membership = ChatMembership.objects.filter(chat=chat, user=request.user).first()
        if not membership:
            return Response({'detail': 'Non membre de ce chat.'}, status=status.HTTP_403_FORBIDDEN)
        
        membership.last_read_at = timezone.now()
        membership.save()
        return Response({'success': True})

    @action(detail=True, methods=['post'], url_path='mute')
    def toggle_mute(self, request, pk=None):
        """Active/désactive le mode sourdine."""
        chat = self.get_object()
        membership = ChatMembership.objects.filter(chat=chat, user=request.user).first()
        if not membership:
            return Response({'detail': 'Non membre.'}, status=status.HTTP_403_FORBIDDEN)
        membership.is_muted = not membership.is_muted
        membership.save()
        return Response({'is_muted': membership.is_muted})

    @action(detail=True, methods=['get'], url_path='members')
    def list_members(self, request, pk=None):
        chat = self.get_object()
        memberships = chat.memberships.select_related('user', 'user__daara')
        members = [m.user for m in memberships]
        return Response(UserBriefSerializer(members, many=True, context={'request': request}).data)


class MessageViewSet(viewsets.ModelViewSet):
    serializer_class = MessageSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        chat_ids = ChatMembership.objects.filter(user=user).values_list('chat_id', flat=True)
        qs = Message.objects.filter(chat_id__in=chat_ids).select_related('sender')
        chat_id = self.request.query_params.get('chat')
        if chat_id:
            qs = qs.filter(chat_id=chat_id)
        return qs.order_by('sent_at')

    def perform_create(self, serializer):
        chat_id = self.request.data.get('chat')
        if not chat_id:
            raise ValidationError("Le champ chat est requis.")
        
        # Vérifier l'appartenance au chat
        if not ChatMembership.objects.filter(chat_id=chat_id, user=self.request.user).exists():
            raise PermissionDenied("Vous n'êtes pas membre de cette discussion.")

        # Vérifier si partage de fichier est autorisé
        config = get_effective_config(self.request.user)
        message_type = self.request.data.get('message_type', Message.MessageType.TEXT)
        if message_type == Message.MessageType.FILE and not config.allow_file_sharing:
            raise PermissionDenied("Le partage de fichiers est désactivé.")

        serializer.save(sender=self.request.user)

    def destroy(self, request, *args, **kwargs):
        """Soft delete d'un message."""
        message = self.get_object()
        if message.sender != request.user and request.user.role != User.Role.ADMIN:
            raise PermissionDenied("Vous ne pouvez pas supprimer ce message.")
        
        message.is_deleted = True
        message.content = "Message supprimé"
        # supprimer le fichier associé
        if message.file:
            message.file.delete(save=False)
        message.save()
        return Response({'success': True, 'id': message.id})

    @action(detail=True, methods=['post'], url_path='react')
    def toggle_reaction(self, request, pk=None):
        message = self.get_object()
        emoji = request.data.get('emoji')
        if not emoji:
            raise ValidationError("Le champ emoji est requis.")

        reaction, created = MessageReaction.objects.get_or_create(
            message=message,
            user=request.user,
            emoji=emoji
        )
        if not created:
            reaction.delete()
            return Response({'status': 'removed', 'emoji': emoji})
        return Response({'status': 'added', 'emoji': emoji})


class ChatInvitationViewSet(viewsets.ModelViewSet):
    serializer_class = ChatInvitationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        return ChatInvitation.objects.filter(
            Q(sender=user) | Q(recipient=user)
        ).select_related('sender', 'recipient', 'chat').order_by('-created_at')

    def perform_create(self, serializer):
        recipient_id = self.request.data.get('recipient')
        if not recipient_id:
            raise ValidationError("Le destinataire est requis.")
        
        try:
            recipient = User.objects.get(pk=recipient_id)
        except User.DoesNotExist:
            raise ValidationError("Utilisateur introuvable.")

        # Vérifier si l'expéditeur a les droits d'invitation
        if not can_invite(self.request.user, recipient):
            raise PermissionDenied("Vous n'êtes pas autorisé à inviter cet utilisateur.")

        # Si un DM existe déjà, inutile de ré-inviter
        existing_direct = Chat.objects.filter(
            chat_type=Chat.ChatType.DIRECT,
            memberships__user=self.request.user
        ).filter(
            memberships__user=recipient
        ).distinct()

        if existing_direct.exists():
            raise ValidationError("Une discussion directe existe déjà avec ce membre.")

        # Vérifier s'il y a déjà une invitation en attente
        invitations = ChatInvitation.objects.filter(
            sender=self.request.user,
            recipient=recipient,
            status=ChatInvitation.Status.PENDING
        )
        if invitations.exists():
            raise ValidationError("Une invitation est déjà en attente pour ce membre.")

        serializer.save(sender=self.request.user, recipient=recipient)

    @action(detail=True, methods=['post'], url_path='accept')
    @transaction.atomic
    def accept_invitation(self, request, pk=None):
        invitation = self.get_object()
        if invitation.recipient != request.user:
            raise PermissionDenied("Vous ne pouvez pas accepter cette invitation.")

        if invitation.status != ChatInvitation.Status.PENDING:
            raise ValidationError("Cette invitation n'est plus en attente.")

        # Marquer l'invitation comme acceptée
        invitation.status = ChatInvitation.Status.ACCEPTED
        invitation.save()

        # Créer le Chat DIRECT
        chat = Chat.objects.create(
            chat_type=Chat.ChatType.DIRECT,
            created_by=invitation.sender
        )
        invitation.chat = chat
        invitation.save(update_fields=['chat'])

        # Ajouter les deux membres
        ChatMembership.objects.create(chat=chat, user=invitation.sender, added_by=invitation.sender)
        ChatMembership.objects.create(chat=chat, user=invitation.recipient, added_by=invitation.sender)

        # Créer un message système d'initialisation
        Message.objects.create(
            chat=chat,
            sender=invitation.sender,
            message_type=Message.MessageType.SYSTEM,
            content="Discussion directe démarrée."
        )

        return Response({
            'detail': 'Invitation acceptée.',
            'chat': ChatSerializer(chat, context={'request': request}).data
        })

    @action(detail=True, methods=['post'], url_path='decline')
    def decline_invitation(self, request, pk=None):
        invitation = self.get_object()
        if invitation.recipient != request.user:
            raise PermissionDenied("Vous ne pouvez pas refuser cette invitation.")

        if invitation.status != ChatInvitation.Status.PENDING:
            raise ValidationError("Cette invitation n'est plus en attente.")

        invitation.status = ChatInvitation.Status.DECLINED
        invitation.save()
        return Response({'detail': 'Invitation refusée.'})


class UserPreferencesView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        prefs, _ = UserMessagingPreferences.objects.get_or_create(user=request.user)
        return Response(UserMessagingPreferencesSerializer(prefs).data)

    def patch(self, request):
        prefs, _ = UserMessagingPreferences.objects.get_or_create(user=request.user)
        
        # Vérification du pilotage : est-ce que l'utilisateur peut modifier sa visibilité ?
        config = get_effective_config(request.user)
        if 'visibility' in request.data and not config.allow_member_visibility_setting:
            raise PermissionDenied("La modification de visibilité est bloquée par l'administrateur.")

        serializer = UserMessagingPreferencesSerializer(prefs, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class MemberSearchView(APIView):
    """
    Permet de rechercher des membres du système pour démarrer une discussion directe,
    en respectant le pilotage d'invitation et les préférences de visibilité des destinataires.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        query = request.query_params.get('q', '').strip()
        if len(query) < 2:
            return Response([])

        # Récupérer la configuration de pilotage du demandeur
        config = get_effective_config(request.user)
        
        # Commencer par filtrer les membres actifs (hors admin)
        users_qs = User.objects.filter(is_active=True).exclude(role=User.Role.ADMIN).exclude(id=request.user.id)

        # Recherche textuelle sur le nom complet, email, ou téléphone
        users_qs = users_qs.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(email__icontains=query) |
            Q(phone__icontains=query)
        )

        user_daara = getattr(request.user, 'daara', None)

        # Filtrer selon le pilotage de recherche hors-daara
        if not config.allow_cross_daara_search and user_daara:
            users_qs = users_qs.filter(daara=user_daara)

        admissible_users = []
        
        # Récupérer les préférences des candidats pour filtrage fin
        # Pour optimiser, on select_related les préférences
        users_qs = users_qs.select_related('messaging_preferences', 'daara')

        for u in users_qs:
            # Vérifier si l'utilisateur accepte d'être invité par request.user
            if can_invite(request.user, u):
                admissible_users.append(u)

        return Response(UserBriefSerializer(admissible_users[:20], many=True, context={'request': request}).data)


class PilotageConfigView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        # Les chefs de daara voient la config de leur Daara, les admins voient tout,
        # les membres normaux voient la config globale.
        daara_id = request.query_params.get('daara_id')
        
        if request.user.role == User.Role.ADMIN:
            if daara_id:
                cfg, _ = MessagingPilotageConfig.objects.get_or_create(daara_id=daara_id)
            else:
                cfg, _ = MessagingPilotageConfig.objects.get_or_create(daara=None)
        elif request.user.role == User.Role.CHEF_DAARA:
            cfg, _ = MessagingPilotageConfig.objects.get_or_create(daara=request.user.daara)
        else:
            # Simple membre : voit sa config applicable
            cfg = get_effective_config(request.user)

        return Response(MessagingPilotageConfigSerializer(cfg).data)

    def patch(self, request):
        # Modification restreinte aux admins et chefs de daara
        daara_id = request.data.get('daara')
        
        if request.user.role == User.Role.ADMIN:
            if daara_id:
                cfg, _ = MessagingPilotageConfig.objects.get_or_create(daara_id=daara_id)
            else:
                cfg, _ = MessagingPilotageConfig.objects.get_or_create(daara=None)
        elif request.user.role == User.Role.CHEF_DAARA:
            # Un chef ne peut modifier que la config de son daara
            if daara_id and int(daara_id) != request.user.daara_id:
                raise PermissionDenied("Vous ne pouvez modifier que la configuration de votre propre Daara.")
            if not request.user.daara:
                raise ValidationError("Vous n'êtes rattaché à aucune Daara.")
            cfg, _ = MessagingPilotageConfig.objects.get_or_create(daara=request.user.daara)
        else:
            raise PermissionDenied("Seuls les administrateurs et chefs de Daara peuvent modifier la configuration.")

        serializer = MessagingPilotageConfigSerializer(cfg, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        return Response(serializer.data)


class NotificationViewSet(viewsets.ModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user).order_by('-created_at')

    def partial_update(self, request, *args, **kwargs):
        notification = self.get_object()
        if notification.user_id != request.user.id:
            raise PermissionDenied("Vous n'avez pas accès à cette notification.")
        serializer = self.get_serializer(
            notification,
            data={'is_read': request.data.get('is_read', True)},
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @action(detail=False, methods=['post'], url_path='mark-all-read')
    def mark_all_read(self, request):
        updated = self.get_queryset().filter(is_read=False).update(is_read=True)
        return Response({'updated': updated}, status=status.HTTP_200_OK)


class FCMTokenView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        token = (request.data.get('token') or '').strip()
        device_type = request.data.get('device_type', FCMToken.DeviceType.WEB)
        if not token:
            return Response({'error': 'token requis'}, status=status.HTTP_400_BAD_REQUEST)
        FCMToken.objects.update_or_create(
            token=token,
            defaults={'user': request.user, 'device_type': device_type},
        )
        return Response({'status': 'registered'}, status=status.HTTP_200_OK)

    def delete(self, request):
        token = (request.data.get('token') or '').strip()
        if token:
            FCMToken.objects.filter(user=request.user, token=token).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AnnouncementViewSet(viewsets.ModelViewSet):
    serializer_class = AnnouncementSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = Announcement.objects.filter(is_published=True)

        if user.role == 'admin':
            return Announcement.objects.all().order_by('-created_at')

        return queryset.filter(
            (Q(target='global') | Q(daara=user.daara))
            & (Q(target_role='all') | Q(target_role=user.role))
        ).order_by('-created_at')

    def perform_create(self, serializer):
        if self.request.user.role not in ['admin', 'chef_daara']:
            raise PermissionDenied('Vous n\'avez pas les droits nécessaires pour publier des annonces.')
        serializer.save()
