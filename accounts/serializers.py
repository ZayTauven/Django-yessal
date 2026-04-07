from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Daara, Tutelle

User = get_user_model()

class DaaraSerializer(serializers.ModelSerializer):
    class Meta:
        model = Daara
        fields = '__all__'

class TutelleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tutelle
        fields = '__all__'
        read_only_fields = ('tutor', 'linked_user')

class UserSerializer(serializers.ModelSerializer):
    daara = DaaraSerializer(read_only=True)
    
    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'phone', 'role', 'status', 'daara', 'avatar_url', 'last_active_at']

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True, style={'input_type': 'password'})
    daara_id = serializers.PrimaryKeyRelatedField(
        queryset=Daara.objects.filter(is_active=True), 
        source='daara', 
        required=True
    )

    class Meta:
        model = User
        fields = ['email', 'password', 'first_name', 'last_name', 'phone', 'daara_id']

    def create(self, validated_data):
        # We explicitly set default status and roles in model, but we can ensure it here too
        user = User.objects.create_user(
            email=validated_data['email'],
            password=validated_data['password'],
            first_name=validated_data.get('first_name', ''),
            last_name=validated_data.get('last_name', ''),
            phone=validated_data.get('phone', ''),
            daara=validated_data['daara'],
            status=User.Status.PENDING,
            role=User.Role.MEMBER
        )
        return user
