from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db.models import Q
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

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
from .authentication import TOKEN_VERSION_CLAIM

from core.mail import send_to_user

User = get_user_model()

def _absolute_or_raw_url(request, url: str | None):
    if not url:
        return None
    if url.startswith('http://') or url.startswith('https://'):
        return url
    if request:
        return request.build_absolute_uri(url)
    return url


def _auteur_de(serializer) -> str:
    """Qui a posé ce mot de passe — pour que le membre sache qui appeler.

    Le sérialiseur n'a pas toujours accès à la requête (import Excel, commande
    de gestion). On retombe alors sur une formule neutre plutôt que d'omettre
    la phrase, qui perdrait son sens.
    """
    requete = serializer.context.get('request')
    auteur = getattr(requete, 'user', None)
    if auteur is None or not getattr(auteur, 'is_authenticated', False):
        return "Un administrateur"
    nom = (auteur.get_full_name() or '').strip()
    return nom or auteur.email or "Un administrateur"


class PilotageSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = PilotageSettings
        fields = ['enable_salons']


class LDDSerializer(serializers.ModelSerializer):
    class Meta:
        model = LDD
        fields = ['id', 'code', 'name', 'description', 'location', 'is_active']


class LDDBriefSerializer(serializers.ModelSerializer):
    """Zone territoriale réduite à ce qui sert à grouper la liste."""

    class Meta:
        model = LDD
        fields = ['id', 'code', 'name']


class PublicDaaraSerializer(serializers.ModelSerializer):
    """
    Vue publique d'un Daara — celle que sert le formulaire d'inscription.

    L'inscription doit rester ouverte : un nouveau membre choisit son Daara
    avant d'avoir un compte, donc `/api/daara/` répond sans authentification.
    Mais elle répondait avec le sérialiseur complet, c'est-à-dire le nom du
    chef, la liste nominative des collecteurs et l'effectif de chaque Daara —
    un annuaire interne de 376 entrées, aspirable par un simple curl.

    Ne restent ici que les trois champs dont la liste déroulante a besoin :
    de quoi choisir son Daara, rien de plus.
    """

    ldd = LDDBriefSerializer(read_only=True)

    class Meta:
        model = Daara
        fields = ['id', 'name', 'ldd']


class DirectoryDaaraBriefSerializer(serializers.ModelSerializer):
    ldd_code = serializers.CharField(source='ldd.code', read_only=True)
    # Le NOM de la zone en plus du code : « CASAMANCE MIDADI » se lit,
    # « DS S15 » se déchiffre. L'annuaire n'exposait que le code, si bien que
    # la ligne « LDD » des fiches membres n'avait rien à afficher et se
    # rabattait sur « Inconnue » — y compris pour des Daaras parfaitement
    # rattachés.
    ldd_name = serializers.CharField(source='ldd.name', read_only=True)

    class Meta:
        model = Daara
        fields = ['id', 'name', 'ldd_code', 'ldd_name']


class MemberTitleSerializer(serializers.ModelSerializer):
    class Meta:
        model = MemberTitle
        fields = ['id', 'name', 'description', 'is_active', 'created_by', 'created_at']
        read_only_fields = ['created_by', 'created_at']


class DirectoryUserSerializer(serializers.ModelSerializer):
    daara = DirectoryDaaraBriefSerializer(read_only=True)
    daara_name = serializers.CharField(source='daara.name', read_only=True, allow_null=True)
    title_name = serializers.CharField(source='title.name', read_only=True, allow_null=True)
    avatar = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'phone',
            'role', 'status', 'daara', 'daara_name', 'title_name',
            'avatar', 'avatar_url',
        ]

    def get_avatar(self, obj):
        if obj.avatar:
            request = self.context.get('request')
            return _absolute_or_raw_url(request, obj.avatar.url)
        return None

    def get_avatar_url(self, obj):
        request = self.context.get('request')
        return _absolute_or_raw_url(request, obj.avatar_url)


class DaaraSerializer(serializers.ModelSerializer):
    members_count = serializers.SerializerMethodField()
    chef_full_name = serializers.SerializerMethodField()
    collectors = serializers.SerializerMethodField()
    ldd = LDDSerializer(read_only=True)
    ldd_id = serializers.PrimaryKeyRelatedField(
        queryset=LDD.objects.all(),
        source='ldd',
        write_only=True,
        required=True,
    )

    class Meta:
        model = Daara
        fields = [
            'id',
            'ldd',
            'ldd_id',
            'name',
            'chef',
            'is_active',
            'created_at',
            'updated_at',
            'members_count',
            'chef_full_name',
            'collectors',
        ]

    def get_members_count(self, obj):
        annotated = getattr(obj, 'members_count', None)
        if annotated is not None:
            return annotated
        return User.objects.filter(daara=obj).count()

    def get_chef_full_name(self, obj):
        if obj.chef_id:
            c = obj.chef
            name = c.get_full_name()
            return name.strip() if name else (c.email or c.phone)

        chef_user = User.objects.filter(daara=obj, role=User.Role.CHEF_DAARA).first()
        if chef_user:
            name = chef_user.get_full_name()
            return name.strip() if name else (chef_user.email or chef_user.phone)

        return None

    def get_collectors(self, obj):
        request = self.context.get('request')
        qs = User.objects.filter(daara=obj, role=User.Role.COLLECTOR).order_by('last_name', 'first_name')
        return [
            {
                'id': u.id,
                'first_name': u.first_name,
                'last_name': u.last_name,
                'email': u.email,
                'phone': u.phone,
                'avatar': _absolute_or_raw_url(request, u.avatar.url if u.avatar else None),
                'avatar_url': _absolute_or_raw_url(request, u.avatar_url),
            }
            for u in qs
        ]


class TutelleSerializer(serializers.ModelSerializer):
    avatar_url = serializers.SerializerMethodField()
    phone = serializers.SerializerMethodField()

    class Meta:
        model = Tutelle
        fields = ['id', 'first_name', 'last_name', 'relation', 'tutor', 'linked_user', 'avatar_url', 'phone', 'created_at']
        read_only_fields = ('tutor', 'linked_user')

    def get_avatar_url(self, obj):
        if obj.linked_user:
            request = self.context.get('request')
            return _absolute_or_raw_url(request, obj.linked_user.avatar_url)
        return None

    def get_phone(self, obj):
        if obj.linked_user:
            return obj.linked_user.phone
        return None


class UserDocumentSerializer(serializers.ModelSerializer):
    type_display = serializers.CharField(source='get_doc_type_display', read_only=True)

    class Meta:
        model = UserDocument
        fields = [
            'id',
            'user',
            'doc_type',
            'type_display',
            'image',
            'image_verso',
            'doc_number',
            'status',
            'validated_by',
            'validated_at',
            'rejection_note',
            'submitted_at',
            'updated_at',
        ]
        read_only_fields = ['user', 'status', 'validated_by', 'validated_at', 'rejection_note', 'submitted_at', 'updated_at']


class UserDocumentValidationSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserDocument
        fields = ['status', 'rejection_note']

    def validate_status(self, value):
        if value not in [UserDocument.ValidationStatus.VALIDATED, UserDocument.ValidationStatus.REJECTED]:
            raise serializers.ValidationError("Le statut doit être 'validated' ou 'rejected'.")
        return value


class UserSerializer(serializers.ModelSerializer):
    avatar = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()
    daara = DaaraSerializer(read_only=True)
    daara_id = serializers.PrimaryKeyRelatedField(
        queryset=Daara.objects.all(),
        source='daara',
        write_only=True,
        required=False,
        allow_null=True,
    )
    daara_name = serializers.CharField(source='daara.name', read_only=True, allow_null=True)
    ldd_name = serializers.SerializerMethodField()
    is_admin = serializers.SerializerMethodField()
    password = serializers.CharField(write_only=True, required=False, style={'input_type': 'password'})

    title = MemberTitleSerializer(read_only=True)
    title_id = serializers.PrimaryKeyRelatedField(
        queryset=MemberTitle.objects.filter(is_active=True),
        source='title',
        write_only=True,
        required=False,
        allow_null=True,
    )
    title_name = serializers.CharField(source='title.name', read_only=True, allow_null=True)

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'phone',
            'role', 'status', 'daara', 'daara_id', 'daara_name', 'ldd_name',
            'password',
            'avatar_url', 'avatar', 'last_active_at',
            'is_admin',
            'title', 'title_id', 'title_name', 'title_change_count', 'title_changed_at',
            'birth_date', 'gender', 'residence_country',
            'city', 'address', 'state', 'zip_code',
            'marital_status', 'blood_type',
            'date_joined',
            'must_change_password',
        ]
        # En lecture seule : c'est le changement de mot de passe qui l'éteint,
        # pas une mise à jour de profil.
        read_only_fields = ['must_change_password']

    def get_is_admin(self, obj):
        return obj.role == User.Role.ADMIN or obj.is_staff

    def get_avatar(self, obj):
        if obj.avatar:
            request = self.context.get('request')
            return _absolute_or_raw_url(request, obj.avatar.url)
        return None

    def get_avatar_url(self, obj):
        request = self.context.get('request')
        return _absolute_or_raw_url(request, obj.avatar_url)

    def get_ldd_name(self, obj):
        if obj.daara and obj.daara.ldd:
            return obj.daara.ldd.name
        return None

    def create(self, validated_data):
        password = validated_data.pop('password', None)
        user = super().create(validated_data)
        if password:
            user.set_password(password)
            # Ce sérialiseur est celui de l'ADMINISTRATION : création manuelle,
            # inscription rapide du collecteur, import Excel. Dans les trois
            # cas, le mot de passe est choisi par un tiers et connu de lui.
            # L'auto-inscription publique passe par RegisterSerializer, qui ne
            # lève pas ce drapeau.
            user.must_change_password = True
            user.save()

            # Le mot de passe lui-même n'est JAMAIS dans le courriel : il est
            # dicté de vive voix. L'écrire le rendrait indéfiniment lisible par
            # quiconque accède à la boîte. Le message dit seulement qu'un mot de
            # passe provisoire a été posé, et qu'il faudra en choisir un autre.
            send_to_user(user, 'mot_de_passe_provisoire', {
                'auteur': _auteur_de(self),
            })
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        user = super().update(instance, validated_data)
        if password:
            user.set_password(password)
            # Un administrateur qui réinitialise le mot de passe de quelqu'un
            # le lui impose tout autant : le drapeau reste levé.
            user.must_change_password = True
            # Et surtout, toutes ses sessions ouvertes tombent. C'est le sens
            # même de la demande : on réinitialise parce qu'on soupçonne
            # quelqu'un d'autre d'utiliser le compte. Sans cela, l'intrus
            # gardait la main pendant l'heure de validité du jeton, et pouvait
            # s'y maintenir en le rafraîchissant.
            user.token_version += 1
            user.save()

            # Le mot de passe lui-même n'est JAMAIS dans le courriel : il est
            # dicté de vive voix. L'écrire le rendrait indéfiniment lisible par
            # quiconque accède à la boîte. Le message dit seulement qu'un mot de
            # passe provisoire a été posé, et qu'il faudra en choisir un autre.
            send_to_user(user, 'mot_de_passe_provisoire', {
                'auteur': _auteur_de(self),
            })
        return user


class ProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            'first_name', 'last_name', 'phone', 'avatar',
            'birth_date', 'gender', 'residence_country',
            'city', 'address', 'state', 'zip_code',
            'marital_status', 'blood_type',
        ]


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True, style={'input_type': 'password'})
    daara_id = serializers.PrimaryKeyRelatedField(
        queryset=Daara.objects.filter(is_active=True),
        source='daara',
        required=True,
    )

    class Meta:
        model = User
        fields = ['email', 'phone', 'password', 'first_name', 'last_name', 'daara_id']

    def validate(self, attrs):
        email = (attrs.get('email') or '').strip()
        phone = (attrs.get('phone') or '').strip()
        if not email and not phone:
            raise serializers.ValidationError({
                'identifier': "Un email ou un numéro de téléphone est obligatoire.",
            })
        attrs['email'] = email or None
        attrs['phone'] = phone or None
        return attrs

    def validate_password(self, value):
        validate_password(value)
        return value

    def create(self, validated_data):
        user = User.objects.create_user(
            email=validated_data.get('email'),
            password=validated_data['password'],
            first_name=validated_data.get('first_name', ''),
            last_name=validated_data.get('last_name', ''),
            phone=validated_data.get('phone'),
            daara=validated_data['daara'],
            status=User.Status.PENDING,
            role=User.Role.MEMBER,
        )
        return user


class CustomRefreshToken(RefreshToken):
    @classmethod
    def for_user(cls, user):
        token = super().for_user(user)
        # Génération du jeton — voir accounts/authentication.py. Sans ce claim,
        # rien ne permet de distinguer un jeton émis avant une réinitialisation
        # de mot de passe d'un jeton émis après, et une session compromise
        # survivait à sa propre révocation.
        token[TOKEN_VERSION_CLAIM] = user.token_version
        token['role'] = user.role
        token['first_name'] = user.first_name
        token['last_name'] = user.last_name
        token['email'] = user.email
        token['phone'] = user.phone
        return token


class LoginSerializer(serializers.Serializer):
    identifier = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        identifier = (attrs.get('identifier') or '').strip()
        password = attrs.get('password')
        if not identifier or not password:
            raise serializers.ValidationError("Identifiant et mot de passe requis.")

        user = User.objects.filter(Q(email__iexact=identifier) | Q(phone=identifier)).first()
        if not user or not user.check_password(password):
            raise serializers.ValidationError("Identifiants invalides.")

        if user.status != User.Status.ACTIVE:
            raise serializers.ValidationError("Votre compte n'est pas encore validé par un administrateur.")

        refresh = CustomRefreshToken.for_user(user)
        attrs['refresh'] = str(refresh)
        attrs['access'] = str(refresh.access_token)
        attrs['user'] = user
        return attrs


class TitleRequestSerializer(serializers.ModelSerializer):
    member_name = serializers.CharField(source='member.get_full_name', read_only=True)
    title_name = serializers.CharField(source='title.name', read_only=True)

    class Meta:
        model = TitleRequest
        fields = [
            'id',
            'member',
            'member_name',
            'title',
            'title_name',
            'status',
            'reviewed_by',
            'reviewed_at',
            'note',
            'created_at',
        ]
        read_only_fields = ['member', 'status', 'reviewed_by', 'reviewed_at', 'created_at']


class TitleRequestReviewSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=['approve', 'refuse'])
    note = serializers.CharField(required=False, allow_blank=True)


class AuditLogSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = AuditLog
        fields = ['id', 'user', 'user_email', 'action', 'entity', 'entity_id', 'description', 'metadata', 'created_at']

