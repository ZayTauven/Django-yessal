from rest_framework import serializers
from .models import Event, EventMedia

class EventMediaSerializer(serializers.ModelSerializer):
    class Meta:
        model = EventMedia
        fields = ['id', 'media_type', 'url', 'content', 'created_at']

class EventSerializer(serializers.ModelSerializer):
    media = EventMediaSerializer(many=True, read_only=True)
    created_by_name = serializers.ReadOnlyField(source='created_by.get_full_name')

    class Meta:
        model = Event
        fields = [
            'id', 'name', 'description', 'event_date', 
            'recurrence', 'is_date_fixed', 'created_by', 
            'created_by_name', 'media', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_by', 'created_at', 'updated_at']
