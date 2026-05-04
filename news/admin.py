from django.contrib import admin

from .models import NewsPost, NewsGalleryImage


@admin.register(NewsPost)
class NewsPostAdmin(admin.ModelAdmin):
    list_display = ('title', 'slug', 'is_published', 'published_at', 'created_by')
    list_filter = ('is_published',)
    search_fields = ('title', 'slug', 'excerpt')


@admin.register(NewsGalleryImage)
class NewsGalleryImageAdmin(admin.ModelAdmin):
    list_display = ('post', 'order', 'caption')
    search_fields = ('post__title', 'caption')
