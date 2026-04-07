from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from .models import Event, EventMedia
from .serializers import EventSerializer, EventMediaSerializer

class IsAdminOrChefDaara(permissions.BasePermission):
    """
    Custom permission to only allow admins or chefs to edit/create events.
    """
    def has_permission(self, request, view):
        # Allow read-only access to any authenticated user
        if request.method in permissions.SAFE_METHODS:
            return request.user.is_authenticated
            
        # Write access only for Admin and Chef Daara
        return request.user.is_authenticated and (
            request.user.role in ['admin', 'chef_daara']
        )

class EventViewSet(viewsets.ModelViewSet):
    queryset = Event.objects.all().order_by('-created_at')
    serializer_class = EventSerializer
    permission_classes = [IsAdminOrChefDaara]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

class EventMediaViewSet(viewsets.ModelViewSet):
    queryset = EventMedia.objects.all()
    serializer_class = EventMediaSerializer
    permission_classes = [IsAdminOrChefDaara]
