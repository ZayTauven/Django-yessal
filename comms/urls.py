from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ChatViewSet, MessageViewSet, AnnouncementViewSet, NotificationViewSet

router = DefaultRouter()
router.register(r'notifications', NotificationViewSet, basename='notifications')
router.register(r'announcements', AnnouncementViewSet, basename='announcements')
router.register(r'messages', MessageViewSet, basename='messages')
router.register(r'', ChatViewSet, basename='chats')

urlpatterns = [
    path('', include(router.urls)),
]
