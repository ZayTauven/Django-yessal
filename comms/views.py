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
from .notify import nom_de, notify, notify_many
from .services import can_invite, get_effective_config, note_systeme, publier, pusher_client
from core.phone import phone_digits

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

    # 0. Le canal commun du Kourel : tout membre connecté l'écoute.
    if channel_name == 'private-kourel':
        pass

    # 1. Canal privé de l'utilisateur
    elif channel_name.startswith('private-user.'):
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
    # `show_online_status` existait dans les réglages sans que rien ne le
    # lise : un membre qui l'avait coupé apparaissait quand même « dans la
    # conversation ». Pusher exige la présence pour relayer la frappe ; on
    # garde donc l'abonnement et l'on dit aux clients de ne pas l'afficher.
    prefs = UserMessagingPreferences.objects.filter(user=request.user).first()
    custom_data = {
        'user_id': str(request.user.id),
        'user_info': {
            'name': request.user.get_full_name() or request.user.email or str(request.user.id),
            'hidden': bool(prefs and not prefs.show_online_status),
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
        memberships_qs = ChatMembership.objects.select_related('user', 'user__daara', 'user__title')

        fils = Chat.objects.filter(id__in=chat_ids)
        # Un fil archivé sort des listes ; ses écrans de gestion (détail,
        # réglages, réactivation) le trouvent toujours. L'administration le
        # retrouve dans la liste avec `?archives=1`.
        if self.action == 'list':
            voir_archives = (
                self.request.query_params.get('archives') == '1'
                and user.role == User.Role.ADMIN
            )
            if not voir_archives:
                fils = fils.filter(archived_at__isnull=True)
        return (
            fils
            .select_related('campaign', 'daara', 'pinned_message', 'pinned_message__sender')
            .prefetch_related(Prefetch('memberships', queryset=memberships_qs))
            .annotate(
                members_count_annotated=Count('memberships', distinct=True),
                # Ni ses propres messages, ni les notes système, ni les messages
                # retirés : un membre qui vient d'écrire voyait sa propre
                # phrase lui revenir en « 1 non lu ».
                unread_count_annotated=Count(
                    'messages',
                    filter=Q(
                        messages__sent_at__gt=Coalesce(Subquery(last_read_subquery), default_last_read)
                    ) & ~Q(messages__sender=user)
                    & ~Q(messages__message_type=Message.MessageType.SYSTEM)
                    & Q(messages__is_deleted=False),
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
                last_message_sender_id=Subquery(last_message_qs.values('sender_id')[:1]),
                last_message_file=Subquery(last_message_qs.values('file')[:1]),
            )
            # La conversation qui a bougé en dernier passe devant — la
            # convention de toute messagerie. Le tri par date de CRÉATION
            # laissait un salon actif sous dix fils muets depuis des mois.
            .annotate(last_activity=Coalesce(F('last_message_sent_at'), F('created_at')))
            .order_by('-last_activity', '-id')
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
        membership.save(update_fields=['last_read_at'])
        # Le « Vu » de l'autre côté se met à jour sans recharger…
        lu = {'chat_id': chat.id, 'user_id': request.user.id, 'read_at': membership.last_read_at.isoformat()}
        publier(f'private-chat.{chat.id}', 'chat-read', lu)
        # … et la pastille s'éteint sur ses AUTRES appareils : lu sur le
        # téléphone, le salon restait marqué non lu sur l'ordinateur.
        publier(f'private-user.{request.user.id}', 'chat-read', lu)
        return Response({'success': True, 'read_at': membership.last_read_at.isoformat()})

    @action(detail=False, methods=['get'], url_path='unread')
    def unread_summary(self, request):
        """Le total des non-lus, pour la pastille du menu et de l'onglet.

        Les fils en sourdine n'y comptent pas : la sourdine, c'est justement
        ne pas être appelé.
        """
        muets = set(
            ChatMembership.objects.filter(user=request.user, is_muted=True).values_list('chat_id', flat=True)
        )
        total = conversations = 0
        for chat_id, non_lus in self.get_queryset().values_list('id', 'unread_count_annotated'):
            if chat_id in muets or not non_lus:
                continue
            total += non_lus
            conversations += 1
        return Response({'total': total, 'chats': conversations})

    @action(detail=True, methods=['post'], url_path='mute')
    def toggle_mute(self, request, pk=None):
        """Active/désactive le mode sourdine."""
        chat = self.get_object()
        membership = ChatMembership.objects.filter(chat=chat, user=request.user).first()
        if not membership:
            return Response({'detail': 'Non membre.'}, status=status.HTTP_403_FORBIDDEN)
        membership.is_muted = not membership.is_muted
        membership.save(update_fields=['is_muted'])
        return Response({'is_muted': membership.is_muted})

    @action(detail=True, methods=['post'], url_path='leave')
    @transaction.atomic
    def leave(self, request, pk=None):
        """Quitter un salon. Un tête-à-tête ne se quitte pas : on le met en sourdine."""
        chat = self.get_object()
        if chat.chat_type == Chat.ChatType.DIRECT:
            raise ValidationError("Une discussion directe ne se quitte pas ; mettez-la en sourdine.")
        if chat.chat_type == Chat.ChatType.CHANNEL:
            raise ValidationError("Un canal du Kourel ne se quitte pas ; mettez-le en sourdine.")
        deleted, _ = ChatMembership.objects.filter(chat=chat, user=request.user).delete()
        if deleted:
            note_systeme(chat, request.user, f"{nom_de(request.user)} a quitté le salon.")
        return Response({'success': True})

    @action(detail=True, methods=['post'], url_path='add-members')
    @transaction.atomic
    def add_members(self, request, pk=None):
        """Ajouter des personnes à un salon existant.

        Mêmes droits que la création : l'administrateur, le coordinateur de
        Daara — et le créateur du salon.
        """
        chat = self.get_object()
        if chat.chat_type != Chat.ChatType.GROUP:
            raise ValidationError("On n'ajoute des membres qu'à un salon.")
        role = request.user.role
        if role not in (User.Role.ADMIN, User.Role.CHEF_DAARA) and chat.created_by_id != request.user.id:
            raise PermissionDenied(
                "Seuls l'administrateur, le coordinateur ou le créateur du salon ajoutent des membres."
            )

        ids = request.data.get('user_ids') or []
        if not isinstance(ids, list) or not ids:
            raise ValidationError({'user_ids': 'Indiquez au moins une personne.'})
        deja = set(chat.memberships.values_list('user_id', flat=True))
        nouveaux = list(User.objects.filter(pk__in=ids, is_active=True).exclude(pk__in=deja))
        ChatMembership.objects.bulk_create(
            [ChatMembership(chat=chat, user=u, added_by=request.user) for u in nouveaux],
            ignore_conflicts=True,
        )
        if nouveaux:
            noms = ', '.join(nom_de(u) for u in nouveaux[:3])
            reste = len(nouveaux) - 3
            if reste > 0:
                noms += f" et {reste} autre{'s' if reste > 1 else ''}"
            note_systeme(chat, request.user, f"{nom_de(request.user)} a ajouté {noms}.")
        return Response({'added': len(nouveaux)})

    @action(detail=True, methods=['get'], url_path='members')
    def list_members(self, request, pk=None):
        chat = self.get_object()
        memberships = chat.memberships.select_related('user', 'user__daara', 'user__title')
        if chat.chat_type == Chat.ChatType.CHANNEL:
            # Tout le Kourel : on ne renvoie que les auteurs, pas des milliers de lignes.
            memberships = memberships.filter(can_post=True)
        members = [m.user for m in memberships]
        return Response(UserBriefSerializer(members, many=True, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='authors')
    def set_author(self, request, pk=None):
        """Désigner (ou retirer) un auteur d'un canal. Administrateurs seulement."""
        if request.user.role != User.Role.ADMIN:
            raise PermissionDenied("Seul un administrateur désigne les auteurs d'un canal.")
        chat = self.get_object()
        if chat.chat_type != Chat.ChatType.CHANNEL or chat.channel_kind == Chat.ChannelKind.JEFS:
            raise ValidationError("Ce fil n'accepte pas d'auteurs désignés.")
        user_id = request.data.get('user_id')
        can_post = bool(request.data.get('can_post', True))
        membership, _ = ChatMembership.objects.get_or_create(chat=chat, user_id=user_id)
        membership.can_post = can_post
        membership.save(update_fields=['can_post'])
        return Response({'user_id': membership.user_id, 'can_post': membership.can_post})

    # ── Gestion des canaux par l'administration (2026-10-30) ───────────────

    def _admin(self, request):
        if request.user.role != User.Role.ADMIN:
            raise PermissionDenied("Réservé à l'administration.")

    def _annoncer_changement(self, chat):
        """Les listes ouvertes se rafraîchissent : nom, icône, archivage…"""
        canal = 'private-kourel' if chat.chat_type == Chat.ChatType.CHANNEL else f'private-chat.{chat.pk}'
        publier(canal, 'chat-settings', {'chat_id': chat.pk})

    @action(detail=False, methods=['post'], url_path='channels')
    @transaction.atomic
    def create_channel(self, request):
        """Créer un canal du Kourel : pour tous, ou pour une audience ciblée.

        Corps : `name`, `description`, `icon`, `push_enabled`, `audience`.
        Le créateur en est le premier auteur ; d'autres se désignent ensuite.
        """
        from .audience import ICONES, normaliser, synchroniser
        self._admin(request)
        nom = (request.data.get('name') or '').strip()
        if not nom:
            raise ValidationError({'name': "Donnez un nom au canal."})
        icone = (request.data.get('icon') or '').strip()
        if icone and icone not in ICONES:
            raise ValidationError({'icon': "Icône inconnue."})
        chat = Chat.objects.create(
            chat_type=Chat.ChatType.CHANNEL,
            name=nom[:200],
            description=(request.data.get('description') or '').strip()[:255],
            icon=icone,
            push_enabled=bool(request.data.get('push_enabled', True)),
            audience=normaliser(request.data.get('audience')),
            created_by=request.user,
        )
        ChatMembership.objects.create(chat=chat, user=request.user, added_by=request.user, can_post=True)
        synchroniser(chat)
        transaction.on_commit(lambda: self._annoncer_changement(chat))
        return Response(ChatSerializer(chat, context={'request': request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['patch'], url_path='settings')
    def update_settings(self, request, pk=None):
        """Réglages d'un canal (ou d'un salon par titre). Administration seulement.

        `name`, `description`, `icon`, `push_enabled` ; `show_amounts` sur
        « Les Jëfs du Kourel » ; `audience` sur un canal créé par
        l'administration ou un salon par titre — jamais sur un canal fondateur.
        """
        from .audience import ICONES, normaliser, synchroniser
        self._admin(request)
        chat = self.get_object()
        if chat.chat_type == Chat.ChatType.DIRECT:
            raise ValidationError("Un tête-à-tête n'a pas de réglages.")
        champs = []
        data = request.data
        if 'name' in data:
            nom = (data.get('name') or '').strip()
            if not nom:
                raise ValidationError({'name': "Le nom ne peut pas être vide."})
            chat.name = nom[:200]
            champs.append('name')
        if 'description' in data:
            chat.description = (data.get('description') or '').strip()[:255]
            champs.append('description')
        if 'icon' in data:
            icone = (data.get('icon') or '').strip()
            if icone and icone not in ICONES:
                raise ValidationError({'icon': "Icône inconnue."})
            chat.icon = icone
            champs.append('icon')
        if 'push_enabled' in data:
            if chat.chat_type != Chat.ChatType.CHANNEL:
                raise ValidationError({'push_enabled': "Réglage réservé aux canaux."})
            chat.push_enabled = bool(data.get('push_enabled'))
            champs.append('push_enabled')
        if 'show_amounts' in data:
            if chat.channel_kind != Chat.ChannelKind.JEFS:
                raise ValidationError({'show_amounts': "Réglage propre à « Les Jëfs du Kourel »."})
            chat.show_amounts = bool(data.get('show_amounts'))
            champs.append('show_amounts')
        resynchroniser = False
        if 'audience' in data:
            if chat.channel_kind:
                raise ValidationError({'audience': "Un canal fondateur s'adresse à tout le Kourel."})
            if chat.chat_type == Chat.ChatType.GROUP and not chat.audience:
                raise ValidationError({'audience': "Ce salon est composé à la main."})
            nouvelle = normaliser(data.get('audience'))
            if chat.chat_type == Chat.ChatType.GROUP and not nouvelle:
                raise ValidationError({'audience': "Un salon par titre garde au moins un titre."})
            chat.audience = nouvelle
            champs.append('audience')
            resynchroniser = True
        if champs:
            chat.save(update_fields=champs)
        if resynchroniser:
            synchroniser(chat)
        self._annoncer_changement(chat)
        return Response(ChatSerializer(self.get_queryset().get(pk=chat.pk), context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='archive')
    def archive(self, request, pk=None):
        """Archiver (`archived: true`) ou réactiver un fil. Historique conservé."""
        self._admin(request)
        chat = self.get_object()
        if chat.channel_kind:
            raise ValidationError("Un canal fondateur ne s'archive pas.")
        if chat.chat_type == Chat.ChatType.DIRECT:
            raise ValidationError("Un tête-à-tête ne s'archive pas.")
        chat.archived_at = timezone.now() if request.data.get('archived', True) else None
        chat.save(update_fields=['archived_at'])
        self._annoncer_changement(chat)
        return Response({'id': chat.pk, 'archived_at': chat.archived_at})

    @action(detail=True, methods=['post'], url_path='pin')
    def pin(self, request, pk=None):
        """Épingler un message en tête du fil (`message_id`), ou désépingler (vide).

        L'administration partout ; dans un canal, ses auteurs ; dans un salon,
        son créateur.
        """
        chat = self.get_object()
        membre = ChatMembership.objects.filter(chat=chat, user=request.user).first()
        permis = request.user.role == User.Role.ADMIN or (
            chat.chat_type == Chat.ChatType.CHANNEL and membre is not None and membre.can_post
        ) or (chat.chat_type == Chat.ChatType.GROUP and chat.created_by_id == request.user.id)
        if not permis:
            raise PermissionDenied("Vous ne pouvez pas épingler dans ce fil.")
        message_id = request.data.get('message_id')
        if message_id:
            message = Message.objects.filter(pk=message_id, chat=chat, is_deleted=False).first()
            if not message:
                raise ValidationError({'message_id': "Ce message n'est pas dans ce fil."})
            chat.pinned_message = message
        else:
            chat.pinned_message = None
        chat.save(update_fields=['pinned_message'])
        payload = ChatSerializer(chat, context={'request': request}).data['pinned_message']
        publier(f'private-chat.{chat.pk}', 'chat-pinned', {'chat_id': chat.pk, 'pinned_message': payload})
        return Response({'pinned_message': payload})


class MessageViewSet(viewsets.ModelViewSet):
    serializer_class = MessageSerializer
    permission_classes = [permissions.IsAuthenticated]

    #: Taille maximale d'une page de fil (`?limit=`).
    PAGE_MAX = 100

    def get_queryset(self):
        user = self.request.user
        chat_ids = ChatMembership.objects.filter(user=user).values_list('chat_id', flat=True)
        qs = (
            Message.objects.filter(chat_id__in=chat_ids)
            # `UserBriefSerializer` lit `daara.name`, `MessageSerializer` les
            # réactions et la citation : sans ces jointures, une requête par
            # message et par champ.
            .select_related('chat', 'sender', 'sender__daara', 'sender__title', 'reply_to', 'reply_to__sender')
            .prefetch_related('reactions')
        )
        chat_id = self.request.query_params.get('chat')
        if chat_id:
            qs = qs.filter(chat_id=chat_id)
        # Recherche dans une conversation — sur le texte des messages encore visibles.
        q = (self.request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(content__icontains=q, is_deleted=False).exclude(
                message_type=Message.MessageType.SYSTEM
            )
        return qs.order_by('sent_at')

    def update(self, request, *args, **kwargs):
        """Modifier son propre message texte. Rien d'autre ne se modifie.

        Un `ModelViewSet` accepte PUT et PATCH d'office : jusqu'ici, tout membre
        d'un fil pouvait réécrire le message d'un autre. Seul le texte de
        l'auteur, d'un message ni supprimé ni système, se corrige désormais.
        """
        message = self.get_object()
        if message.sender_id != request.user.id:
            raise PermissionDenied("Vous ne pouvez modifier que vos propres messages.")
        if message.is_deleted or message.message_type != Message.MessageType.TEXT:
            raise ValidationError("Ce message ne peut pas être modifié.")
        texte = (request.data.get('content') or '').strip()
        if not texte:
            raise ValidationError({'content': 'Le message est vide.'})
        message.content = texte
        message.edited_at = timezone.now()
        message.save(update_fields=['content', 'edited_at'])
        return Response(self.get_serializer(message).data)

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        """Le fil, entier — ou par pages en remontant, si `limit` est donné.

        `?chat=12&limit=40` rend les 40 DERNIERS messages, dans l'ordre de
        lecture ; `&before=<id>` rend les 40 qui précèdent ce message. La
        réponse porte alors `has_more`. Sans `limit`, l'ancien contrat (une
        liste nue, tout l'historique) reste servi aux applications déjà
        installées.
        """
        limit = request.query_params.get('limit')
        if not limit:
            return super().list(request, *args, **kwargs)
        try:
            limit = max(1, min(int(limit), self.PAGE_MAX))
        except ValueError:
            raise ValidationError({'limit': 'Nombre attendu.'})

        qs = self.get_queryset()
        before = request.query_params.get('before')
        if before:
            pivot = qs.filter(pk=before).values_list('sent_at', flat=True).first()
            if pivot is None:
                raise ValidationError({'before': 'Message introuvable dans vos conversations.'})
            qs = qs.filter(Q(sent_at__lt=pivot) | Q(sent_at=pivot, pk__lt=before))

        page = list(qs.order_by('-sent_at', '-id')[:limit + 1])
        has_more = len(page) > limit
        page = list(reversed(page[:limit]))
        data = self.get_serializer(page, many=True).data
        return Response({'results': data, 'has_more': has_more})

    def perform_create(self, serializer):
        chat = serializer.validated_data.get('chat')
        if not chat:
            raise ValidationError("Le champ chat est requis.")

        # Vérifier l'appartenance au chat
        membership = ChatMembership.objects.filter(chat=chat, user=self.request.user).first()
        if not membership:
            raise PermissionDenied("Vous n'êtes pas membre de cette discussion.")
        if chat.archived_at:
            raise PermissionDenied("Ce fil est archivé : on ne peut plus y écrire.")
        if chat.chat_type == Chat.ChatType.CHANNEL:
            if not membership.can_post:
                raise PermissionDenied("Dans ce canal, on réagit ; seuls les auteurs désignés publient.")
            if serializer.validated_data.get('reply_to'):
                raise ValidationError({'reply_to': "On ne répond pas dans un canal."})

        # Vérifier si partage de fichier est autorisé
        config = get_effective_config(self.request.user)
        if serializer.validated_data.get('file') and not config.allow_file_sharing:
            raise PermissionDenied("Le partage de fichiers est désactivé.")

        serializer.save(sender=self.request.user)
        # Écrire, c'est avoir lu jusque-là : sans quoi le fil que l'on vient
        # d'alimenter repasserait en « non lu » au prochain chargement.
        ChatMembership.objects.filter(chat=chat, user=self.request.user).update(
            last_read_at=serializer.instance.sent_at
        )

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
        emoji = (request.data.get('emoji') or '').strip()
        if not emoji or len(emoji) > 20:
            raise ValidationError("Le champ emoji est requis.")
        if message.is_deleted or message.message_type == Message.MessageType.SYSTEM:
            raise ValidationError("On ne réagit pas à ce message.")

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

        invitation = serializer.save(sender=self.request.user, recipient=recipient)

        salon = getattr(invitation.chat, 'name', '') or 'une discussion'
        notify(
            recipient,
            code='invitation_salon',
            titre='Invitation à un salon',
            message=f'{nom_de(self.request.user)} vous invite à rejoindre « {salon} ».',
            contexte={'auteur': nom_de(self.request.user), 'salon': salon},
        )

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
        note_systeme(chat, invitation.sender, "Discussion directe démarrée.")

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

        # Les membres actifs. Les comptes d'administration restent hors de
        # portée des membres — mais pas d'un autre administrateur : exclus
        # pour tout le monde, deux admins ne se trouvaient jamais, ni pour
        # un tête-à-tête ni pour un salon (constaté le 2026-10-30).
        users_qs = User.objects.filter(is_active=True).exclude(id=request.user.id)
        if request.user.role != User.Role.ADMIN:
            users_qs = users_qs.exclude(role=User.Role.ADMIN)

        # ═══════════════════════════════════════════════════════════════════
        # La recherche porte sur le nom COMPLET, mot par mot
        # ═══════════════════════════════════════════════════════════════════
        # La docstring l'annonçait déjà ; le code ne le faisait pas. Il
        # comparait la saisie ENTIÈRE à chaque champ pris isolément, si bien
        # que « Papice Cisse » ne rencontrait ni `first_name` ni `last_name` —
        # aucun des deux ne contient l'espace. Chercher quelqu'un par son nom
        # complet, le geste le plus naturel qui soit, ne rendait RIEN, sans le
        # moindre indice : l'écran affichait « Personne ne correspond », et le
        # membre en concluait que l'autre n'est pas inscrit.
        #
        # Chaque mot doit désormais toucher AU MOINS UN champ, et TOUS les
        # mots doivent toucher : « Papice » au prénom, « Cisse » au nom. Une
        # recherche à un seul mot se comporte exactement comme avant.
        def filtre_mot(mot):
            f = (
                Q(first_name__icontains=mot) |
                Q(last_name__icontains=mot) |
                Q(email__icontains=mot) |
                Q(phone__icontains=mot)
            )
            chiffres_mot = phone_digits(mot)
            if chiffres_mot:
                f |= Q(phone__icontains=chiffres_mot)
            return f

        mots = query.split()
        recherche = filtre_mot(mots[0])
        for mot in mots[1:]:
            recherche &= filtre_mot(mot)

        # Les numéros sont stockés en E.164, sans séparateur : « 77-000-00-00 »
        # ne rencontrait jamais « +221770000000 ». Le fragment débarrassé de
        # ses espaces et de ses tirets reste testé sur la saisie ENTIÈRE, en
        # PLUS du découpage ci-dessus — sinon un numéro saisi avec des espaces
        # se retrouverait éclaté en morceaux trop courts pour discriminer.
        chiffres = phone_digits(query)
        if chiffres:
            recherche |= Q(phone__icontains=chiffres)

        users_qs = users_qs.filter(recherche)

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
            raise PermissionDenied("Seuls les administrateurs et les coordinateurs du Daara peuvent modifier la configuration.")

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
        # `AnnouncementSerializer` expose `daara_name` (source='daara.name') :
        # sans jointure, c'est une requete par annonce. Invisible aujourd'hui —
        # il n'y en a qu'une en base — et lineaire des qu'il y en aura cent.
        # Les DEUX branches en ont besoin : celle de l'admin lit le meme champ.
        queryset = Announcement.objects.filter(is_published=True).select_related('daara')

        if user.role == 'admin':
            return (
                Announcement.objects.select_related('daara').order_by('-created_at')
            )

        return queryset.filter(
            (Q(target='global') | Q(daara=user.daara))
            & (Q(target_role='all') | Q(target_role=user.role))
        ).order_by('-created_at')

    def perform_create(self, serializer):
        if self.request.user.role not in ['admin', 'chef_daara']:
            raise PermissionDenied(
                "Vous n'avez pas les droits nécessaires pour publier des annonces."
            )
        annonce = serializer.save()

        # ═══════════════════════════════════════════════════════════════════
        # Seules les annonces CRITIQUES partent par courriel
        # ═══════════════════════════════════════════════════════════════════
        # Les annonces `info` sont nombreuses et déjà visibles dans le Hub. Les
        # envoyer toutes est le plus court chemin vers le dossier
        # « indésirables » — et vers un désabonnement de masse qui emporterait
        # aussi les messages qui comptent vraiment.
        if annonce.urgency != Announcement.Urgency.CRITICAL:
            return

        destinataires = self._destinataires_de(annonce)
        if destinataires:
            notify_many(
                destinataires,
                code='annonce',
                titre=annonce.title,
                message=annonce.content,
                contexte={
                    'titre': annonce.title,
                    'contenu': annonce.content,
                    'urgence': annonce.get_urgency_display(),
                },
            )

    @staticmethod
    def _destinataires_de(annonce):
        """Le public d'une annonce, selon sa portée et son rôle cible.

        La même règle que `get_queryset` côté lecture, appliquée à l'endroit :
        là on filtre les annonces pour un membre, ici on cherche les membres
        pour une annonce.
        """
        qs = User.objects.filter(is_active=True)
        if annonce.target == Announcement.Target.DAARA_ONLY and annonce.daara_id:
            qs = qs.filter(daara_id=annonce.daara_id)
        if annonce.target_role != Announcement.TargetRole.ALL:
            qs = qs.filter(role=annonce.target_role)
        return list(qs.only('id', 'email', 'first_name'))
