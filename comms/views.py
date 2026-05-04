from django.contrib.auth import get_user_model
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from .models import Announcement, Chat, ChatMembership, Message, Notification
from .serializers import (
    AnnouncementSerializer,
    ChatSerializer,
    CreateChatSerializer,
    MessageSerializer,
    NotificationSerializer,
)

User = get_user_model()

COMMUNITY_ROLES = (
    User.Role.MEMBER,
    User.Role.CHEF_DAARA,
    User.Role.COLLECTOR,
)


def _resolve_chat_invitee_ids(creator: User, data: dict) -> set[int]:
    mode = data.get('invite_mode') or 'manual'
    preset_daara = data.get('preset_daara_id')
    manual_ids = list(data.get('manual_user_ids') or [])
    campaign = data.get('campaign')
    ids: set[int] = set()

    if creator.role == User.Role.CHEF_DAARA:
        d = creator.daara_id
        if not d:
            return set()
        if mode == 'daara_all':
            ids.update(
                User.objects.filter(daara_id=d, role__in=COMMUNITY_ROLES).values_list(
                    'id', flat=True
                )
            )
        elif mode == 'daara_members':
            ids.update(
                User.objects.filter(daara_id=d, role=User.Role.MEMBER).values_list(
                    'id', flat=True
                )
            )
        elif mode == 'daara_collectors':
            ids.update(
                User.objects.filter(daara_id=d, role=User.Role.COLLECTOR).values_list(
                    'id', flat=True
                )
            )
        elif mode == 'daara_chefs':
            ids.update(
                User.objects.filter(daara_id=d, role=User.Role.CHEF_DAARA).values_list(
                    'id', flat=True
                )
            )
        for uid in manual_ids:
            if User.objects.filter(pk=uid, daara_id=d).exclude(role=User.Role.ADMIN).exists():
                ids.add(uid)
        return ids

    if creator.role == User.Role.ADMIN:
        if mode == 'global_chefs':
            ids.update(
                User.objects.filter(role=User.Role.CHEF_DAARA).values_list('id', flat=True)
            )
        elif mode == 'global_collectors':
            ids.update(
                User.objects.filter(role=User.Role.COLLECTOR).values_list('id', flat=True)
            )
        elif preset_daara and mode == 'daara_all':
            ids.update(
                User.objects.filter(
                    daara_id=preset_daara, role__in=COMMUNITY_ROLES
                ).values_list('id', flat=True)
            )
        elif preset_daara and mode == 'daara_members':
            ids.update(
                User.objects.filter(
                    daara_id=preset_daara, role=User.Role.MEMBER
                ).values_list('id', flat=True)
            )
        elif preset_daara and mode == 'daara_collectors':
            ids.update(
                User.objects.filter(
                    daara_id=preset_daara, role=User.Role.COLLECTOR
                ).values_list('id', flat=True)
            )
        elif preset_daara and mode == 'daara_chefs':
            ids.update(
                User.objects.filter(
                    daara_id=preset_daara, role=User.Role.CHEF_DAARA
                ).values_list('id', flat=True)
            )
        for uid in manual_ids:
            if User.objects.filter(pk=uid).exclude(role=User.Role.ADMIN).exists():
                ids.add(uid)
        return ids

    if campaign and campaign.can_be_managed_by(creator):
        qs = User.objects.filter(role__in=COMMUNITY_ROLES)
        scoped_daara_id = campaign.daara_id or getattr(campaign.organizer, 'daara_id', None)
        if scoped_daara_id:
            qs = qs.filter(daara_id=scoped_daara_id)
        allowed_ids = set(qs.values_list('id', flat=True))
        for uid in manual_ids:
            if uid in allowed_ids:
                ids.add(uid)
        return ids

    return set()


class ChatViewSet(viewsets.ModelViewSet):
    serializer_class = ChatSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        chat_ids = ChatMembership.objects.filter(user=user).values_list(
            'chat_id', flat=True
        )
        return Chat.objects.filter(id__in=chat_ids).select_related('campaign', 'daara')

    def create(self, request, *args, **kwargs):
        ser = CreateChatSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        campaign = None
        campaign_id = data.get('campaign_id')
        if campaign_id is not None:
            from events.models import Campaign

            campaign = Campaign.objects.filter(pk=campaign_id).select_related(
                'organizer', 'daara'
            ).first()
            if not campaign:
                return Response(
                    {'detail': 'Campagne introuvable.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

        can_create = request.user.role in (User.Role.ADMIN, User.Role.CHEF_DAARA)
        if campaign and campaign.can_be_managed_by(request.user):
            can_create = True
        if not can_create:
            raise PermissionDenied(
                "Vous n'avez pas les privilèges nécessaires pour créer ce salon."
            )

        daara_id = data.get('daara_id')
        daara = None
        if daara_id is not None:
            from accounts.models import Daara

            daara = Daara.objects.filter(pk=daara_id).first()

        if (
            request.user.role == User.Role.CHEF_DAARA
            and daara
            and daara.id != request.user.daara_id
        ):
            return Response(
                {'detail': "Vous ne pouvez rattacher le salon qu'à votre Daara."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        invite_data = dict(data)
        invite_data['campaign'] = campaign
        invitee_ids = _resolve_chat_invitee_ids(request.user, invite_data)
        invitee_ids.discard(request.user.id)

        chat = Chat.objects.create(
            name=data['name'],
            daara=daara,
            campaign=campaign,
            created_by=request.user,
        )

        memberships = [ChatMembership(chat=chat, user=request.user, added_by=request.user)]
        if campaign and campaign.organizer_id and campaign.organizer_id != request.user.id:
            memberships.append(
                ChatMembership(chat=chat, user=campaign.organizer, added_by=request.user)
            )

        for uid in invitee_ids:
            u = User.objects.filter(pk=uid).first()
            if u:
                memberships.append(ChatMembership(chat=chat, user=u, added_by=request.user))

        ChatMembership.objects.bulk_create(memberships, ignore_conflicts=True)

        if campaign:
            notified_users = User.objects.filter(
                id__in=[m.user_id for m in memberships if m.user_id != request.user.id]
            )
            for user in notified_users:
                Notification.objects.create(
                    user=user,
                    title="Invitation à un salon de campagne",
                    message=(
                        f"Vous avez été invité(e) dans le salon '{chat.name}' pour aider à "
                        f"organiser la campagne '{campaign.name}'."
                    ),
                )

        return Response(ChatSerializer(chat).data, status=status.HTTP_201_CREATED)


class MessageViewSet(viewsets.ModelViewSet):
    serializer_class = MessageSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        chat_ids = ChatMembership.objects.filter(user=user).values_list(
            'chat_id', flat=True
        )
        qs = Message.objects.filter(chat_id__in=chat_ids).select_related('sender')
        chat_id = self.request.query_params.get('chat')
        if chat_id is not None and chat_id != '':
            qs = qs.filter(chat_id=chat_id)
        return qs.order_by('sent_at')

    def perform_create(self, serializer):
        serializer.save(sender=self.request.user)


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


class AnnouncementViewSet(viewsets.ModelViewSet):
    serializer_class = AnnouncementSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = Announcement.objects.filter(is_published=True)

        if user.role == 'admin':
            return Announcement.objects.all().order_by('-created_at')

        from django.db.models import Q

        return queryset.filter(
            (Q(target='global') | Q(daara=user.daara))
            & (Q(target_role='all') | Q(target_role=user.role))
        ).order_by('-created_at')

    def perform_create(self, serializer):
        if self.request.user.role not in ['admin', 'chef_daara']:
            raise PermissionDenied(
                'You do not have permission to create announcements.'
            )
        serializer.save()
