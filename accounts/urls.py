from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    RegisterView,
    CustomTokenObtainPairView,
    ProfileView,
    LDDViewSet,
    DaaraViewSet,
    TutelleViewSet,
    AnalyticsViewSet,
    AuditLogViewSet,
    UserManagementViewSet,
    ForgotPasswordView,
    ChangePasswordView,
    DirectoryUserViewSet,
    PilotageSettingsViewSet,
    MemberTitleViewSet,
    TitleRequestViewSet,
    UserDocumentViewSet,
    PendingDocumentValidationViewSet,
    DocumentValidationView,
    MemberAssignTitleView,
)

router = DefaultRouter()
router.register(r'ldd', LDDViewSet, basename='ldd')
router.register(r'daara', DaaraViewSet, basename='daara')
router.register(r'tutelles', TutelleViewSet, basename='tutelle')
router.register(r'analytics', AnalyticsViewSet, basename='analytics')
router.register(r'audit', AuditLogViewSet, basename='audit')
router.register(r'users', UserManagementViewSet, basename='users')
router.register(r'directory/users', DirectoryUserViewSet, basename='directory-users')
router.register(r'settings/pilotage', PilotageSettingsViewSet, basename='pilotage-settings')
router.register(r'titles', MemberTitleViewSet, basename='titles')
router.register(r'title-requests', TitleRequestViewSet, basename='title-requests')
router.register(r'admin/documents/pending', PendingDocumentValidationViewSet, basename='admin-documents-pending')

user_documents = UserDocumentViewSet.as_view(
    {
        'get': 'list',
        'post': 'create',
    }
)
user_document_detail = UserDocumentViewSet.as_view(
    {
        'patch': 'partial_update',
        'get': 'retrieve',
    }
)

from .token_views import VersionedTokenRefreshView

urlpatterns = [
    path('auth/register/', RegisterView.as_view(), name='register'),
    path('auth/login/', CustomTokenObtainPairView.as_view(), name='login'),
    # Vue versionnée : refuse de rafraîchir un jeton d'une génération
    # périmée. Avec la vue standard, une session révoquée se serait
    # renouvelée toute seule pendant les 24 h de vie du jeton.
    path('auth/refresh/', VersionedTokenRefreshView.as_view(), name='token_refresh'),
    path('auth/forgot-password/', ForgotPasswordView.as_view(), name='forgot_password'),
    path('auth/change-password/', ChangePasswordView.as_view(), name='change_password'),
    path('profile/', ProfileView.as_view(), name='profile'),

    path('users/<int:user_id>/documents/', user_documents, name='user-documents'),
    path('users/<int:user_id>/documents/<int:pk>/', user_document_detail, name='user-document-detail'),
    path('admin/documents/<int:pk>/validate/', DocumentValidationView.as_view(), name='admin-document-validate'),
    path('members/<int:pk>/assign-title/', MemberAssignTitleView.as_view(), name='member-assign-title'),

    path('', include(router.urls)),
]
