from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import EventViewSet, EventMediaViewSet

router = DefaultRouter()
router.register(r'', EventViewSet, basename='events') # Path: /api/events/
router.register(r'media', EventMediaViewSet, basename='event-media') # Path: /api/events/media/

urlpatterns = [
    path('', include(router.urls)),
]
