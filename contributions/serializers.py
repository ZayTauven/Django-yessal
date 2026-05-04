from rest_framework import serializers
from .models import Donation

class DonationSerializer(serializers.ModelSerializer):
    donor_name = serializers.ReadOnlyField(source='donor.get_full_name')
    donor_daara_name = serializers.CharField(
        source='donor.daara.name', read_only=True, allow_null=True
    )
    campaign_name = serializers.ReadOnlyField(source='campaign.name')
    beneficiary_name = serializers.SerializerMethodField()
    collector_name = serializers.ReadOnlyField(source='collector.get_full_name')

    def get_beneficiary_name(self, obj):
        b = obj.beneficiary
        if not b:
            return None
        return (b.first_name or "").strip() or None

    class Meta:
        model = Donation
        fields = '__all__'
        read_only_fields = ['collector', 'validated_by', 'validated_at', 'created_at', 'updated_at']
