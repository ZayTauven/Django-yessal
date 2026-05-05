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

User = get_user_model()


class PilotageSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = PilotageSettings
        fields = ['enable_salons']


class LDDSerializer(serializers.ModelSerializer):
    class Meta:
        model = LDD
        fields = ['id', 'code', 'name', 'description', 'location', 'is_active']


class DirectoryDaaraBriefSerializer(serializers.ModelSerializer):
    ldd_code = serializers.CharField(source='ldd.code', read_only=True)

    class Meta:
        model = Daara
        fields = ['id', 'name', 'ldd_code']


class MemberTitleSerializer(serializers.ModelSerializer):
    class Meta:
        model = MemberTitle
        fields = ['id', 'name', 'description', 'is_active', 'created_by', 'created_at']
        read_only_fields = ['created_by', 'created_at']


class DirectoryUserSerializer(serializers.ModelSerializer):
    daara = DirectoryDaaraBriefSerializer(read_only=True)
    daara_name = serializers.CharField(source='daara.name', read_only=True, allow_null=True)
    title_name = serializers.CharField(source='title.name', read_only=True, allow_null=True)

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'phone',
            'role', 'status', 'daara', 'daara_name', 'title_name',
        ]


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
        qs = User.objects.filter(daara=obj, role=User.Role.COLLECTOR).order_by('last_name', 'first_name')
        return [
            {
                'id': u.id,
                'first_name': u.first_name,
                'last_name': u.last_name,
                'email': u.email,
                'phone': u.phone,
                'avatar_url': u.avatar_url,
            }
            for u in qs
        ]


class TutelleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tutelle
        fields = '__all__'
        read_only_fields = ('tutor', 'linked_user')


class UserDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserDocument
        fields = [
            'id',
            'user',
            'doc_type',
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
    daara = DaaraSerializer(read_only=True)
    daara_id = serializers.PrimaryKeyRelatedField(
        queryset=Daara.objects.all(),
        source='daara',
        write_only=True,
        required=False,
        allow_null=True,
    )
    daara_name = serializers.CharField(source='daara.name', read_only=True, allow_null=True)
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

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'phone',
            'role', 'status', 'daara', 'daara_id', 'daara_name',
            'password',
            'avatar_url', 'avatar', 'last_active_at',
            'is_admin',
            'title', 'title_id', 'title_change_count', 'title_changed_at',
            'birth_date', 'gender', 'residence_country',
            'city', 'address', 'state', 'zip_code',
            'marital_status', 'blood_type',
        ]

    def get_is_admin(self, obj):
        return obj.role == User.Role.ADMIN or obj.is_staff

    def create(self, validated_data):
        password = validated_data.pop('password', None)
        user = super().create(validated_data)
        if password:
            user.set_password(password)
            user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        user = super().update(instance, validated_data)
        if password:
            user.set_password(password)
            user.save()
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

