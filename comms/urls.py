from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ChatViewSet,
    MessageViewSet,
    AnnouncementViewSet,
    NotificationViewSet,
    ChatInvitationViewSet,
    FCMTokenView,
    pusher_auth,
    UserPreferencesView,
    MemberSearchView,
    PilotageConfigView,
)

router = DefaultRouter()
router.register(r'notifications', NotificationViewSet, basename='notifications')
router.register(r'announcements', AnnouncementViewSet, basename='announcements')
router.register(r'messages', MessageViewSet, basename='messages')
router.register(r'invitations', ChatInvitationViewSet, basename='invitations')
router.register(r'', ChatViewSet, basename='chats')

urlpatterns = [
    path('pusher/auth/', pusher_auth, name='pusher_auth'),
    path('preferences/', UserPreferencesView.as_view(), name='user_preferences'),
    path('search-members/', MemberSearchView.as_view(), name='search_members'),
    path('pilotage/', PilotageConfigView.as_view(), name='pilotage_config'),
    path('fcm-token/', FCMTokenView.as_view(), name='fcm_token'),
    path('', include(router.urls)),
]
