import os

from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import (
    Message,
    Chat,
    ChatMembership,
    Announcement,
    Notification,
    MessageReaction,
    ChatInvitation,
    UserMessagingPreferences,
    MessagingPilotageConfig,
)

User = get_user_model()

class AnnouncementSerializer(serializers.ModelSerializer):
    daara_name = serializers.CharField(source='daara.name', read_only=True)

    class Meta:
        model = Announcement
        fields = ['id', 'title', 'content', 'target', 'daara', 'daara_name', 'urgency', 'target_role', 'is_published', 'created_at', 'expires_at']


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ['id', 'title', 'message', 'is_read', 'created_at']


class UserBriefSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    avatar = serializers.SerializerMethodField()
    daara_name = serializers.CharField(source='daara.name', read_only=True)
    # Le titre du membre (AMIR, DIEUWRIGNE…), affiché en étiquette à côté de
    # son nom dans les fils et les listes de membres.
    title = serializers.CharField(source='title.name', read_only=True, default=None)

    class Meta:
        model = User
        fields = ['id', 'name', 'avatar', 'daara_name', 'role', 'title']

    def get_name(self, obj):
        name = obj.get_full_name()
        return name.strip() if name else (obj.email or obj.phone or f"User {obj.id}")

    def get_avatar(self, obj):
        request = self.context.get('request')
        if obj.avatar:
            url = obj.avatar.url
            return request.build_absolute_uri(url) if request else url
        return obj.avatar_url or None


class MessageReactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageReaction
        fields = ['id', 'emoji', 'user']


#: Extensions affichées en vignette dans le fil plutôt qu'en carte de document.
EXTENSIONS_IMAGE = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
#: Extensions lues par le lecteur de note vocale.
EXTENSIONS_AUDIO = {'.mp3', '.m4a', '.aac', '.ogg', '.opus', '.wav', '.amr', '.webm'}
EXTENSIONS_VIDEO = {'.mp4', '.mov', '.3gp'}


def nature_fichier(fichier):
    """image · audio · video · document — ce que le fil sait afficher."""
    ext = os.path.splitext(nom_de_fichier(fichier))[1].lower()
    if ext in EXTENSIONS_IMAGE:
        return 'image'
    if ext in EXTENSIONS_AUDIO:
        return 'audio'
    if ext in EXTENSIONS_VIDEO:
        return 'video'
    return 'document'


def apercu_message(message):
    """Le texte d'un message tel qu'on le montre HORS de sa bulle.

    Une liste de conversations, une citation de réponse, une notification :
    trois endroits qui n'affichent qu'une ligne, et qui doivent dire la même
    chose d'une pièce jointe ou d'un message retiré.
    """
    if message is None:
        return ''
    if message.is_deleted:
        return 'Message supprimé'
    if message.message_type == Message.MessageType.FILE:
        nature = nature_fichier(message.file)
        if nature == 'audio':
            return 'Note vocale'
        if nature == 'image':
            return message.content or 'Photo'
        if nature == 'video':
            return message.content or 'Vidéo'
        nom = nom_de_fichier(message.file)
        return f'Document : {nom}' if nom else 'Pièce jointe'
    return message.content or ''


def nom_de_fichier(fichier):
    """`chat_files/recu_ab12Cd.pdf` → `recu_ab12Cd.pdf`."""
    if not fichier:
        return ''
    return os.path.basename(fichier.name or '')


class ReplyPreviewSerializer(serializers.ModelSerializer):
    """Ce qu'une réponse cite du message d'origine.

    Le client ne recevait que `reply_to: <id>` et cherchait le texte dans les
    messages déjà chargés : dès que l'original sortait de la page — ou qu'il
    était supprimé — la citation affichait « Message référencé », sans auteur.
    """
    sender_name = serializers.SerializerMethodField()
    content = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ['id', 'sender_name', 'content', 'message_type', 'is_deleted']

    def get_sender_name(self, obj):
        name = (obj.sender.get_full_name() or '').strip()
        return name or obj.sender.email or obj.sender.phone or 'Un membre'

    def get_content(self, obj):
        return apercu_message(obj)[:200]


class MessageSerializer(serializers.ModelSerializer):
    sender = UserBriefSerializer(read_only=True)
    reactions = serializers.SerializerMethodField()
    file_url = serializers.SerializerMethodField()
    file_name = serializers.SerializerMethodField()
    file_size = serializers.SerializerMethodField()
    file_is_image = serializers.SerializerMethodField()
    file_kind = serializers.SerializerMethodField()
    reply_preview = ReplyPreviewSerializer(source='reply_to', read_only=True)
    # Vide autorisé : une pièce jointe peut partir sans légende. La règle
    # « un texte ou un fichier » est posée par `validate`.
    content = serializers.CharField(allow_blank=True, required=False, default='')

    class Meta:
        model = Message
        fields = [
            'id', 'chat', 'sender', 'message_type', 'content',
            'file', 'file_url', 'file_name', 'file_size', 'file_is_image', 'file_kind',
            'reply_to', 'reply_preview', 'reactions', 'is_deleted', 'sent_at',
            'edited_at', 'payload',
        ]
        read_only_fields = ['sender', 'sent_at', 'edited_at', 'payload']
        extra_kwargs = {'file': {'write_only': True}}

    def validate(self, attrs):
        fichier = attrs.get('file')
        texte = (attrs.get('content') or '').strip()
        if attrs.get('message_type') == Message.MessageType.SYSTEM:
            raise serializers.ValidationError({'message_type': "Les messages système sont réservés au serveur."})
        if fichier:
            attrs['message_type'] = Message.MessageType.FILE
        elif not texte:
            raise serializers.ValidationError({'content': 'Le message est vide.'})
        else:
            attrs['message_type'] = Message.MessageType.TEXT
        attrs['content'] = texte

        reponse = attrs.get('reply_to')
        chat = attrs.get('chat')
        if reponse and chat and reponse.chat_id != chat.id:
            raise serializers.ValidationError({'reply_to': "Ce message n'appartient pas à cette conversation."})
        return attrs

    def get_file_url(self, obj):
        return obj.file.url if obj.file and not obj.is_deleted else None

    def get_file_name(self, obj):
        return nom_de_fichier(obj.file) or None

    def get_file_size(self, obj):
        if not obj.file:
            return None
        try:
            return obj.file.size
        except (OSError, ValueError):
            # Fichier référencé mais absent du disque — le volume média a déjà
            # été perdu une fois : le fil doit s'afficher quand même.
            return None

    def get_file_is_image(self, obj):
        return os.path.splitext(nom_de_fichier(obj.file))[1].lower() in EXTENSIONS_IMAGE

    def get_file_kind(self, obj):
        return nature_fichier(obj.file) if obj.file else None

    def get_reactions(self, obj):
        # Regrouper les réactions par emoji
        user = self.context.get('request').user if self.context.get('request') else None
        reactions_qs = obj.reactions.all()
        aggregated = {}
        for r in reactions_qs:
            emoji = r.emoji
            if emoji not in aggregated:
                aggregated[emoji] = {
                    'emoji': emoji,
                    'count': 0,
                    'reacted_by_me': False
                }
            aggregated[emoji]['count'] += 1
            if user and r.user_id == user.id:
                aggregated[emoji]['reacted_by_me'] = True
        return list(aggregated.values())


class ChatSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()
    avatar = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    members_count = serializers.SerializerMethodField()
    is_muted = serializers.SerializerMethodField()
    peer_last_read_at = serializers.SerializerMethodField()
    peer_id = serializers.SerializerMethodField()
    last_activity_at = serializers.SerializerMethodField()
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    can_post = serializers.SerializerMethodField()

    class Meta:
        model = Chat
        fields = [
            'id', 'chat_type', 'display_name', 'avatar', 'last_message',
            'unread_count', 'members_count', 'is_muted', 'peer_id',
            'peer_last_read_at', 'last_activity_at', 'daara', 'campaign',
            'created_by', 'created_at', 'channel_kind', 'description', 'can_post',
        ]

    def get_can_post(self, obj):
        """Peut-on écrire ici ? Partout oui, sauf dans un canal sans être auteur."""
        if obj.chat_type != Chat.ChatType.CHANNEL:
            return True
        membership = self._my_membership(obj)
        return bool(membership and membership.can_post)

    def _my_membership(self, obj):
        request = self.context.get('request')
        if not request or not request.user:
            return None
        prefetched = getattr(obj, 'memberships', None)
        if hasattr(prefetched, 'all'):
            return next((m for m in prefetched.all() if m.user_id == request.user.id), None)
        return None

    def _other_membership(self, obj):
        request = self.context.get('request')
        if not request or not request.user:
            return None
        return next((m for m in obj.memberships.all() if m.user_id != request.user.id), None)

    def get_is_muted(self, obj):
        membership = self._my_membership(obj)
        return bool(membership and membership.is_muted)

    def get_peer_id(self, obj):
        """L'interlocuteur d'un tête-à-tête — sert à la présence en ligne."""
        if obj.chat_type != Chat.ChatType.DIRECT:
            return None
        other = self._get_other_member(obj)
        return other.id if other else None

    def get_peer_last_read_at(self, obj):
        """Jusqu'où l'autre a lu, en tête-à-tête seulement : c'est le « Vu ».

        En salon, un « Vu par 37 » ne dit rien d'utile et coûterait une
        requête par message ; on s'en tient au tête-à-tête.
        """
        if obj.chat_type != Chat.ChatType.DIRECT:
            return None
        other = self._other_membership(obj)
        if not other or not other.last_read_at:
            return None
        return other.last_read_at.isoformat()

    def get_last_activity_at(self, obj):
        sent = getattr(obj, 'last_message_sent_at', None)
        return (sent or obj.created_at).isoformat() if (sent or obj.created_at) else None

    def _get_other_member(self, obj):
        request = self.context.get('request')
        if not request or not request.user:
            return None
        # Cache the member lookup
        if not hasattr(self, '_other_member_cache'):
            self._other_member_cache = {}
        if obj.id not in self._other_member_cache:
            prefetched_memberships = getattr(obj, 'memberships', None)
            if hasattr(prefetched_memberships, 'all'):
                membership = next(
                    (m for m in prefetched_memberships.all() if m.user_id != request.user.id),
                    None
                )
            else:
                membership = obj.memberships.exclude(user=request.user).select_related('user').first()
            self._other_member_cache[obj.id] = membership.user if membership else None
        return self._other_member_cache[obj.id]

    def get_display_name(self, obj):
        if obj.chat_type == Chat.ChatType.DIRECT:
            other = self._get_other_member(obj)
            if other:
                return other.get_full_name() or other.email or other.phone or f"Membres {other.id}"
            return "Contact inconnu"
        return obj.name or f"Salon #{obj.id}"

    def get_avatar(self, obj):
        request = self.context.get('request')
        if obj.chat_type == Chat.ChatType.DIRECT:
            other = self._get_other_member(obj)
            if other:
                if other.avatar:
                    url = other.avatar.url
                    return request.build_absolute_uri(url) if request else url
                return other.avatar_url or None
        return None

    def get_last_message(self, obj):
        if getattr(obj, 'last_message_id', None) is None:
            return None

        sender_full_name = (
            f"{(obj.last_message_sender_first_name or '').strip()} {(obj.last_message_sender_last_name or '').strip()}"
        ).strip()
        sender_name = sender_full_name or obj.last_message_sender_email or "Un membre"

        # Même rédaction que la citation d'une réponse : `apercu_message`
        # travaille sur un objet, on lui en donne un reconstitué.
        apercu = apercu_message(Message(
            message_type=obj.last_message_type,
            content=obj.last_message_content or '',
            is_deleted=bool(obj.last_message_deleted),
            file=getattr(obj, 'last_message_file', None) or None,
        ))
        request = self.context.get('request')
        sender_id = getattr(obj, 'last_message_sender_id', None)

        return {
            'content': apercu,
            'message_type': obj.last_message_type,
            'sent_at': obj.last_message_sent_at.isoformat() if obj.last_message_sent_at else None,
            'sender_name': sender_name,
            'sender_id': sender_id,
            'is_mine': bool(request and sender_id == request.user.id),
        }

    def get_unread_count(self, obj):
        request = self.context.get('request')
        if not request or not request.user:
            return 0
        if hasattr(obj, 'unread_count_annotated'):
            return obj.unread_count_annotated
        membership = obj.memberships.filter(user=request.user).first()
        if not membership:
            return 0
        # Mêmes exclusions que l'annotation de `ChatViewSet.get_queryset`.
        qs = obj.messages.exclude(sender=request.user).exclude(
            message_type=Message.MessageType.SYSTEM
        ).filter(is_deleted=False)
        if membership.last_read_at:
            qs = qs.filter(sent_at__gt=membership.last_read_at)
        return qs.count()

    def get_members_count(self, obj):
        if hasattr(obj, 'members_count_annotated'):
            return obj.members_count_annotated
        return obj.memberships.count()


class CreateGroupChatSerializer(serializers.ModelSerializer):
    member_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False
    )
    manual_user_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False
    )
    invite_mode = serializers.ChoiceField(
        choices=[
            'manual',
            'daara_all',
            'daara_members',
            'daara_collectors',
            'daara_chefs',
            'global_chefs',
            'global_collectors',
        ],
        write_only=True,
        required=False,
        default='manual'
    )
    daara_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    preset_daara_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    campaign_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = Chat
        fields = [
            'id',
            'chat_type',
            'name',
            'member_ids',
            'manual_user_ids',
            'invite_mode',
            'daara_id',
            'preset_daara_id',
            'campaign_id',
        ]

    def create(self, validated_data):
        request = self.context.get('request')
        invite_mode = validated_data.pop('invite_mode', 'manual')
        member_ids = set(validated_data.pop('member_ids', []))
        member_ids.update(validated_data.pop('manual_user_ids', []))
        daara_id = validated_data.pop('daara_id', None)
        preset_daara_id = validated_data.pop('preset_daara_id', None)
        campaign_id = validated_data.pop('campaign_id', None)
        validated_data['chat_type'] = Chat.ChatType.GROUP
        if daara_id:
            validated_data['daara_id'] = daara_id
        if campaign_id:
            validated_data['campaign_id'] = campaign_id
        chat = Chat.objects.create(**validated_data)

        target_daara_id = preset_daara_id or daara_id or getattr(getattr(request, 'user', None), 'daara_id', None)
        scoped_users = User.objects.filter(is_active=True).exclude(role=User.Role.ADMIN)

        if invite_mode in {'daara_all', 'daara_members', 'daara_collectors', 'daara_chefs'} and target_daara_id:
            scoped_users = scoped_users.filter(daara_id=target_daara_id)
            role_by_mode = {
                'daara_members': User.Role.MEMBER,
                'daara_collectors': User.Role.COLLECTOR,
                'daara_chefs': User.Role.CHEF_DAARA,
            }.get(invite_mode)
            if role_by_mode:
                scoped_users = scoped_users.filter(role=role_by_mode)
            member_ids.update(scoped_users.values_list('id', flat=True))
        elif invite_mode == 'global_chefs':
            member_ids.update(scoped_users.filter(role=User.Role.CHEF_DAARA).values_list('id', flat=True))
        elif invite_mode == 'global_collectors':
            member_ids.update(scoped_users.filter(role=User.Role.COLLECTOR).values_list('id', flat=True))

        # Le créateur est toujours membre
        if request and request.user:
            ChatMembership.objects.create(chat=chat, user=request.user, added_by=request.user)

        # Ajouter les autres membres
        for uid in member_ids:
            if request and request.user and int(uid) == request.user.id:
                continue
            try:
                user = User.objects.get(pk=uid)
                ChatMembership.objects.get_or_create(chat=chat, user=user, defaults={'added_by': request.user if request else None})
            except User.DoesNotExist:
                pass
        return chat


class ChatInvitationSerializer(serializers.ModelSerializer):
    sender = UserBriefSerializer(read_only=True)
    recipient = UserBriefSerializer(read_only=True)
    chat_name = serializers.CharField(source='chat.name', read_only=True)

    class Meta:
        model = ChatInvitation
        fields = ['id', 'sender', 'recipient', 'chat', 'chat_name', 'status', 'created_at', 'expires_at']
        read_only_fields = ['sender', 'status', 'created_at', 'expires_at']


class UserMessagingPreferencesSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserMessagingPreferences
        fields = [
            'visibility',
            'allow_direct_invites',
            'allow_group_invites',
            'show_online_status',
            'notifications_enabled',
            'notify_on_mention_only',
        ]


class MessagingPilotageConfigSerializer(serializers.ModelSerializer):
    daara_name = serializers.CharField(source='daara.name', read_only=True)

    class Meta:
        model = MessagingPilotageConfig
        fields = [
            'id',
            'daara',
            'daara_name',
            'allow_cross_daara_search',
            'allow_member_invite',
            'allow_group_creation',
            'allow_invite_accept_decline',
            'allow_member_visibility_setting',
            'allow_file_sharing',
            'updated_at',
        ]

