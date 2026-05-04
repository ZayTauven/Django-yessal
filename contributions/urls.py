from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import DonationViewSet

from .views_webhooks import bictorys_webhook

router = DefaultRouter()
router.register(r'', DonationViewSet, basename='donations')

urlpatterns = [
    path('webhook/bictorys/', bictorys_webhook, name='bictorys-webhook'),
    path('', include(router.urls)),
]
