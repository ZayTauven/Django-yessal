from rest_framework import serializers

from .models import NewsPost, NewsGalleryImage


class NewsGalleryImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = NewsGalleryImage
        fields = ['id', 'post', 'image', 'caption', 'order']


class NewsPostSerializer(serializers.ModelSerializer):
    gallery = NewsGalleryImageSerializer(many=True, read_only=True)

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
            'created_at',
            'updated_at',
            'gallery',
        ]
        read_only_fields = ['created_by', 'created_at', 'updated_at']
