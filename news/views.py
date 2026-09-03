from rest_framework import filters, permissions, viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import NewsPost, NewsGalleryImage
from .serializers import NewsPostSerializer, NewsGalleryImageSerializer


class NewsPostViewSet(viewsets.ModelViewSet):
    serializer_class = NewsPostSerializer
    queryset = NewsPost.objects.all()
    lookup_field = 'slug'
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
        post = serializer.save(created_by=self.request.user)
        # Handle multiple gallery images if provided during creation
        gallery_images = self.request.FILES.getlist('gallery_images')
        for image in gallery_images:
            NewsGalleryImage.objects.create(post=post, image=image)

    @action(detail=True, methods=['post'])
    def gallery(self, request, *args, **kwargs):
        """
        Ajoute une image à la galerie d'un article.

        La signature était `gallery(self, request, pk=None)`, alors que ce
        ViewSet a `lookup_field = 'slug'` : le routeur passe `slug='...'`, que
        la méthode n'accepte pas. Tout appel à
        `POST /api/news/posts/<slug>/gallery/` levait donc un TypeError — une
        500, pas la 400 qu'on aurait pu croire. `*args, **kwargs` rend la
        méthode indifférente au nom de la clé de recherche.
        """
        post = self.get_object()
        serializer = NewsGalleryImageSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(post=post)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class NewsGalleryImageViewSet(viewsets.ModelViewSet):
    serializer_class = NewsGalleryImageSerializer
    queryset = NewsGalleryImage.objects.all()

    def get_permissions(self):
        if self.action in {'list', 'retrieve'}:
            return [permissions.IsAuthenticated()]
        return [permissions.IsAdminUser()]
