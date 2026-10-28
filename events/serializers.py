from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers

from .models import Campaign, CampaignTodo, Fete


class FeteSerializer(serializers.ModelSerializer):
    # Jours d'ici l'événement, comptés par le SERVEUR sur le jour de Dakar
    # (négatif s'il est passé). Le web les recalculait avec l'horloge de
    # l'appareil, et lisait « 2026-10-09 » en UTC puis en heure locale : un
    # jour d'écart dans les Amériques, et n'importe quoi sur un poste mal réglé.
    days_until = serializers.SerializerMethodField()

    class Meta:
        model = Fete
        fields = ['id', 'name', 'description', 'date', 'recurrence', 'is_active', 'created_by', 'created_at',
                  'days_until']
        read_only_fields = ['created_by', 'created_at']

    def get_days_until(self, obj):
        return (obj.date - timezone.localdate()).days if obj.date else None


class CampaignTodoSerializer(serializers.ModelSerializer):
    class Meta:
        model = CampaignTodo
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at']


class CampaignSerializer(serializers.ModelSerializer):
    collected_amount = serializers.SerializerMethodField()
    # Tous statuts confondus : c'est ce que compte `destroy` pour refuser la
    # suppression. La fenêtre de confirmation le dit AVANT le clic, au lieu
    # d'annoncer des Jëfs « supprimés » que le serveur protège.
    jefs_count = serializers.SerializerMethodField()
    todos = serializers.SerializerMethodField()
    organizer_name = serializers.SerializerMethodField()
    organizer_role = serializers.CharField(source='organizer.role', read_only=True)
    # Le NOM du Daara et celui de la fête, en plus de leurs clés.
    #
    # Le mobile les lisait depuis toujours — sur-titre de la photographie d'un
    # Ndiguel, sous-titre de son organisateur — et recevait `null` à chaque
    # fois : `Meta.fields` ne servait que `daara` et `fete`, deux entiers. Un
    # écran ne peut pas afficher « Daara de 3 ».
    #
    # `allow_null` parce que les deux relations sont facultatives sur le modèle
    # (`SET_NULL`, `blank=True`) : sans lui, DRF refuserait un Ndiguel sans
    # Daara. Ajout PUREMENT additif — aucun champ retiré, aucun renommé, donc
    # rien à reprendre côté `front-web`.
    daara_name = serializers.CharField(source='daara.name', read_only=True, allow_null=True)
    fete_name = serializers.CharField(source='fete.name', read_only=True, allow_null=True)

    # ── Le Daara de l'ORGANISATEUR, qui n'est pas celui du Ndiguel ──────────
    #
    # 🔴 Ajouté le 2026-09-06 pour lever une confusion qui se voyait à l'écran.
    #
    # `Campaign.daara` est un CIBLAGE facultatif — « Ciblage par Daara
    # (optionnel) » dans `AGENTS/tools/03_modeles_donnees.md`. Un Ndiguel
    # n'appartient pas à un Daara : il peut viser toute la confrérie, et son
    # organisateur est choisi pour mener l'opération, d'où qu'il vienne.
    #
    # Faute de ce champ, le mobile affichait `daara_name` SOUS LE NOM de
    # l'organisateur, libellé « Daara de X ». Quand l'organisateur venait d'un
    # autre Daara que celui visé — le cas normal — l'écran attribuait donc à
    # une personne réelle un rattachement qui n'était pas le sien.
    #
    # Additif, comme les deux au-dessus : rien à reprendre côté `front-web`.
    organizer_daara_name = serializers.CharField(
        source='organizer.daara.name', read_only=True, allow_null=True,
    )
    effective_status = serializers.SerializerMethodField()
    is_manageable = serializers.SerializerMethodField()
    days_remaining = serializers.SerializerMethodField()
    is_overdue = serializers.SerializerMethodField()
    days_until_open = serializers.SerializerMethodField()

    class Meta:
        model = Campaign
        fields = [
            'id',
            'name',
            'description',
            'goal_amount',
            'objective',
            'deadline',
            'opens_on',
            'days_until_open',
            'status',
            'effective_status',
            'fete',
            'fete_name',
            'daara',
            'daara_name',
            'organizer',
            'organizer_name',
            'organizer_daara_name',
            'organizer_role',
            'organizer_assigned_at',
            'created_by',
            'created_at',
            'updated_at',
            'collected_amount',
            'jefs_count',
            'todos',
            'is_manageable',
            'days_remaining',
            'is_overdue',
            'illustrative_photo',
        ]
        read_only_fields = ['created_by', 'created_at', 'updated_at', 'organizer_assigned_at']

    def get_collected_amount(self, obj):
        return obj.donations.filter(payment_status='confirmed').aggregate(total=Sum('amount'))['total'] or 0

    def get_jefs_count(self, obj):
        return obj.donations.count()

    def get_todos(self, obj):
        request = self.context.get('request')
        if not request or not obj.can_be_managed_by(request.user):
            return []
        todos = obj.todos.all().order_by('is_completed', '-created_at')
        return CampaignTodoSerializer(todos, many=True).data

    def get_organizer_name(self, obj):
        if not obj.organizer_id:
            return None
        name = obj.organizer.get_full_name()
        return name.strip() if name else (obj.organizer.email or obj.organizer.phone)

    def get_effective_status(self, obj):
        return obj.get_effective_status()

    def get_is_manageable(self, obj):
        request = self.context.get('request')
        if not request:
            return False
        return obj.can_be_managed_by(request.user)

    def get_days_remaining(self, obj):
        return max(0, (obj.deadline - timezone.localdate()).days)

    def get_is_overdue(self, obj):
        return obj.deadline < timezone.localdate()

    def get_days_until_open(self, obj):
        """Jours avant l'ouverture, 0 s'il est ouvert, `None` sans date d'ouverture."""
        if not obj.opens_on:
            return None
        return max(0, (obj.opens_on - timezone.localdate()).days)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        opens_on = attrs.get('opens_on', getattr(self.instance, 'opens_on', None))
        deadline = attrs.get('deadline', getattr(self.instance, 'deadline', None))
        if opens_on and deadline and opens_on > deadline:
            raise serializers.ValidationError(
                {'opens_on': "La date d'ouverture doit précéder la date de clôture."}
            )
        return attrs

    def validate_organizer(self, value):
        if value is None:
            return value
        if value.role not in ['member', 'collector', 'chef_daara']:
            raise serializers.ValidationError('Le responsable doit être un membre, un collecteur ou un coordinateur du Daara.')
        return value
