from rest_framework import serializers
from .models import Donation

class DonationSerializer(serializers.ModelSerializer):
    donor_name = serializers.ReadOnlyField(source='donor.get_full_name')
    donor_daara_name = serializers.CharField(
        source='donor.daara.name', read_only=True, allow_null=True
    )
    # Zone (LDD) du Daara du donateur : l'export des contributions la réclame.
    # `default=None` : un donateur sans Daara ne fait pas tomber la ligne.
    donor_ldd_name = serializers.CharField(
        source='donor.daara.ldd.name', read_only=True, default=None
    )
    campaign_name = serializers.ReadOnlyField(source='campaign.name')
    beneficiary_name = serializers.SerializerMethodField()
    collector_name = serializers.ReadOnlyField(source='collector.get_full_name')

    def get_beneficiary_name(self, obj):
        """Nom COMPLET de la tutelle : le prénom seul ne départage personne
        dans un export (« Tutelle - Aïda » ne dit pas laquelle)."""
        b = obj.beneficiary
        if not b:
            return None
        full = f"{b.first_name or ''} {b.last_name or ''}".strip()
        return full or None

    class Meta:
        model = Donation
        fields = '__all__'
        read_only_fields = ['collector', 'validated_by', 'validated_at', 'created_at', 'updated_at']
