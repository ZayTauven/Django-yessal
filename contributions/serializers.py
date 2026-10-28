from django.contrib.auth import get_user_model
from rest_framework import serializers
from .models import CashDeposit, Donation

class DonationSerializer(serializers.ModelSerializer):
    # 🔴 Champ OBLIGATOIRE depuis mai 2026, par défaut de `ModelSerializer`
    # sur une clé étrangère non nulle. Or ni le mobile (champ absent) ni le web
    # (`donor: null`) ne l'envoient pour un Jëf personnel : un membre ne
    # pouvait faire un Jëf depuis AUCUNE des deux applications — 400 « Ce champ
    # est obligatoire » / « ne peut être nul ». Seule la collecte, qui nomme le
    # membre, passait. Absent, c'est l'appelant (`perform_create`).
    donor = serializers.PrimaryKeyRelatedField(
        queryset=get_user_model().objects.all(), required=False, allow_null=True,
    )

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
    validated_by_name = serializers.ReadOnlyField(source='validated_by.get_full_name')

    def get_beneficiary_name(self, obj):
        """Nom COMPLET de la tutelle : le prénom seul ne départage personne
        dans un export (« Tutelle - Aïda » ne dit pas laquelle)."""
        b = obj.beneficiary
        if not b:
            return None
        full = f"{b.first_name or ''} {b.last_name or ''}".strip()
        return full or None

    def validate_amount(self, value):
        # Rien ne l'empêchait : un Jëf de -5 000 FCFA s'enregistrait et
        # retranchait son montant du total d'un Ndiguel une fois confirmé.
        if value <= 0:
            raise serializers.ValidationError("Le montant doit être supérieur à zéro.")
        return value

    def validate_payment_method(self, value):
        if value not in Donation.CURRENT_METHODS:
            raise serializers.ValidationError("Ce moyen de paiement n'est plus proposé.")
        return value

    class Meta:
        model = Donation
        fields = '__all__'
        # ═══════════════════════════════════════════════════════════════════
        # 🔴 LE STATUT D'UN JËF ÉTAIT ÉCRIT PAR LE CLIENT
        # ═══════════════════════════════════════════════════════════════════
        # Seuls `collector`, `validated_*` et les dates étaient protégés. Un
        # membre pouvait donc créer son Jëf directement `confirmed`, ou le
        # passer `confirmed` après coup, et le web le faisait réellement pour
        # tout Jëf en espèces : la demande de collecte comptait dans le total
        # du Ndiguel avant que l'argent ait changé de mains.
        #
        # Tout ce qui décrit le sort de l'argent est désormais décidé par le
        # serveur : statut, références de paiement, archive, remise, annulation.
        # ═══════════════════════════════════════════════════════════════════
        read_only_fields = [
            'payment_status', 'collector', 'target_daara',
            'validated_by', 'validated_at', 'collected_at', 'cash_deposit',
            'cancelled_by', 'cancelled_at', 'cancellation_reason',
            'archive_id', 'external_ref', 'bictorys_ref', 'checkout_url', 'wire_reference',
            'created_at', 'updated_at',
        ]


class CashDepositSerializer(serializers.ModelSerializer):
    collector_name = serializers.ReadOnlyField(source='collector.get_full_name')
    validated_by_name = serializers.ReadOnlyField(source='validated_by.get_full_name')

    class Meta:
        model = CashDeposit
        fields = [
            'id', 'collector', 'collector_name', 'validated_by', 'validated_by_name',
            'total_amount', 'total_count', 'note', 'created_at',
        ]
        read_only_fields = fields
