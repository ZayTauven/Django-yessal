from django.conf import settings
from django.db import models
from django.utils.text import slugify
from django.utils import timezone
from core.validators import downscale_image, validate_upload_size


class NewsPost(models.Model):
    title = models.CharField(max_length=300)
    slug = models.SlugField(unique=True, max_length=350, blank=True)
    content = models.TextField()
    excerpt = models.TextField(blank=True)
    cover_image = models.ImageField(upload_to='news/covers/', null=True, blank=True, validators=[validate_upload_size])
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
        # Réduction à la source : le plafond de 15 Mo dit ce qu'on accepte,
        # pas ce qu'on doit réservir à chaque visiteur. Voir
        # core.validators.downscale_image — sans effet si l'image tient déjà
        # dans les bornes, et silencieuse en cas d'échec.
        downscale_image(self.cover_image)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class NewsGalleryImage(models.Model):
    post = models.ForeignKey(NewsPost, on_delete=models.CASCADE, related_name='gallery')
    image = models.ImageField(upload_to='news/gallery/%Y/%m/', validators=[validate_upload_size])
    caption = models.CharField(max_length=200, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']

    def save(self, *args, **kwargs):
        # Réduction à la source : le plafond de 15 Mo dit ce qu'on accepte,
        # pas ce qu'on doit réservir à chaque visiteur. Voir
        # core.validators.downscale_image — sans effet si l'image tient déjà
        # dans les bornes, et silencieuse en cas d'échec.
        downscale_image(self.image)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.post.title}#{self.order}"
