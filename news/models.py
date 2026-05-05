from django.conf import settings
from django.db import models
from django.utils.text import slugify
from django.utils import timezone


class NewsPost(models.Model):
    title = models.CharField(max_length=300)
    slug = models.SlugField(unique=True, max_length=350, blank=True)
    content = models.TextField()
    excerpt = models.TextField(blank=True)
    cover_image = models.ImageField(upload_to='news/covers/', null=True, blank=True)
    youtube_url = models.URLField(blank=True)
    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-published_at', '-created_at']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        if self.is_published and not self.published_at:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class NewsGalleryImage(models.Model):
    post = models.ForeignKey(NewsPost, on_delete=models.CASCADE, related_name='gallery')
    image = models.ImageField(upload_to='news/gallery/%Y/%m/')
    caption = models.CharField(max_length=200, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.post.title}#{self.order}"
