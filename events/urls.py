from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import CampaignViewSet, CampaignTodoViewSet, FeteViewSet

router = DefaultRouter()
router.register(r'campaigns', CampaignViewSet, basename='campaigns')
router.register(r'campaign-todos', CampaignTodoViewSet, basename='campaign-todos')
router.register(r'fetes', FeteViewSet, basename='fetes')

urlpatterns = [
    path('', include(router.urls)),
]
