from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import NewsPostViewSet, NewsGalleryImageViewSet

router = DefaultRouter()
router.register(r'posts', NewsPostViewSet, basename='news-posts')
router.register(r'gallery', NewsGalleryImageViewSet, basename='news-gallery')

urlpatterns = [
    path('', include(router.urls)),
]
