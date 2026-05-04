from rest_framework import filters, permissions, viewsets

from .models import NewsPost, NewsGalleryImage
from .serializers import NewsPostSerializer, NewsGalleryImageSerializer


class NewsPostViewSet(viewsets.ModelViewSet):
    serializer_class = NewsPostSerializer
    queryset = NewsPost.objects.all()
    filter_backends = [filters.SearchFilter]
    search_fields = ['title', 'excerpt', 'content']

    def get_permissions(self):
        if self.action in {'list', 'retrieve'}:
            return [permissions.IsAuthenticated()]
        return [permissions.IsAdminUser()]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_staff or getattr(self.request.user, 'role', None) == 'admin':
            return qs
        return qs.filter(is_published=True)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class NewsGalleryImageViewSet(viewsets.ModelViewSet):
    serializer_class = NewsGalleryImageSerializer
    queryset = NewsGalleryImage.objects.all()

    def get_permissions(self):
        if self.action in {'list', 'retrieve'}:
            return [permissions.IsAuthenticated()]
        return [permissions.IsAdminUser()]
