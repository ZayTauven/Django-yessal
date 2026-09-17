from rest_framework import serializers

from core.richtext import sanitize_html

from .models import NewsPost, NewsGalleryImage


class NewsGalleryImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = NewsGalleryImage
        fields = ['id', 'post', 'image', 'caption', 'order']
        read_only_fields = ['post']


class NewsPostSerializer(serializers.ModelSerializer):
    gallery = NewsGalleryImageSerializer(many=True, read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)

    class Meta:
        model = NewsPost
        fields = [
            'id',
            'title',
            'slug',
            'content',
            'excerpt',
            'cover_image',
            'youtube_url',
            'is_published',
            'published_at',
            'created_by',
            'created_by_name',
            'created_at',
            'updated_at',
            'gallery',
        ]
        read_only_fields = ['slug', 'created_by', 'created_at', 'updated_at']

    def validate_content(self, value):
        """
        Le corps d'un article est du HTML depuis que l'éditeur riche existe, et
        il est rendu tel quel par le web comme par le mobile. Il ne franchit
        donc la frontière de l'API qu'assaini — ici, et pas seulement dans
        l'éditeur, parce que l'API se joint sans éditeur.

        Les articles antérieurs, saisis en texte brut, traversent inchangés :
        sans balise à retirer, `sanitize_html` n'a rien à faire.
        """
        return sanitize_html(value)
