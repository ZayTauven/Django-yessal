from django.conf import settings
from django.db import models, transaction
from django.db.models import F
from django.db.utils import IntegrityError
from django.utils.text import slugify
from django.utils import timezone
from core.validators import downscale_image, validate_upload_size

#: Repli quand un titre ne produit aucun caractère utilisable en URL — un titre
#: uniquement composé d'émojis ou de ponctuation. Le compteur d'unicité prend le
#: relais derrière (« article », « article-2 »…).
SLUG_FALLBACK = 'article'


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
        # ── `nulls_last`, et ce n'est pas un détail de tri ──────────────────
        # `'-published_at'` seul plaçait les BROUILLONS EN TÊTE de liste.
        # PostgreSQL considère NULL comme la plus grande valeur, donc un tri
        # décroissant les remonte tous avant le moindre article publié — et un
        # brouillon n'a pas de date de parution, par définition.
        #
        # L'administrateur ouvrait donc « Actualités » sur ses brouillons, le
        # journal réel repoussé dessous. Le web s'en tirait en retriant côté
        # client ; l'API, elle, servait bien cet ordre-là.
        ordering = [F('published_at').desc(nulls_last=True), '-created_at']

    def _unique_slug(self) -> str:
        """
        Fabrique un slug non vide et libre.

        `slugify(self.title)` seul produisait deux pannes distinctes, toutes
        deux vues en production :

          · DEUX TITRES IDENTIQUES → 500. `slug` est `unique=True` : le second
            article portant le même titre levait une IntegrityError remontée
            telle quelle au client. On la lit dans les journaux du 2026-09-02 —
            « duplicate key value violates unique constraint
            news_newspost_slug_key ». Republier la version corrigée d'une
            annonce était donc impossible.

          · UN TITRE NON LATIN → slug VIDE. `slugify` retire tout ce qui n'est
            pas ASCII : « مرحبا بكم » devenait `''`. L'article s'enregistrait
            sans bruit, mais son adresse était `/news//` — injoignable, à
            jamais. Et comme `''` est une valeur comme une autre pour la
            contrainte d'unicité, le SECOND titre non latin levait la 500
            ci-dessus. Sur une application de confrérie, un titre en arabe
            n'est pas un cas tordu.

        La cascade : ASCII d'abord — c'est ce qui donne les plus belles
        adresses pour le français et le wolof — puis unicode, qui préserve
        l'arabe, puis un repli fixe.
        """
        base = slugify(self.title) or slugify(self.title, allow_unicode=True)
        # `strip('-')` : un titre comme « — Appel — » produit des tirets nus.
        base = base.strip('-')[:300] or SLUG_FALLBACK

        candidate, counter = base, 2
        limit = self._meta.get_field('slug').max_length
        while (
            NewsPost.objects.filter(slug=candidate)
            .exclude(pk=self.pk)
            .exists()
        ):
            suffix = f'-{counter}'
            candidate = f'{base[:limit - len(suffix)]}{suffix}'
            counter += 1
        return candidate

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug()
        if self.is_published and not self.published_at:
            self.published_at = timezone.now()
        # Réduction à la source : le plafond de 15 Mo dit ce qu'on accepte,
        # pas ce qu'on doit réservir à chaque visiteur. Voir
        # core.validators.downscale_image — sans effet si l'image tient déjà
        # dans les bornes, et silencieuse en cas d'échec.
        downscale_image(self.cover_image)

        # ── Le cas de course qui reste ─────────────────────────────────────
        # `_unique_slug` interroge la base, puis on écrit : entre les deux, un
        # second administrateur peut avoir pris le slug. La fenêtre est étroite
        # mais son issue est une 500, c'est-à-dire exactement ce qu'on répare.
        #
        # On ne réessaie QUE si le slug a été fabriqué ici : un slug fourni
        # explicitement appartient à l'appelant, et le lui changer en douce
        # serait pire que l'erreur.
        for attempt in range(3):
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError as exc:
                if 'slug' not in str(exc).lower() or attempt == 2:
                    raise
                self.slug = self._unique_slug()
                # Un `force_insert` rejoué tel quel retenterait un INSERT sur
                # une ligne qui, elle, n'a pas été écrite — c'est bien ce qu'on
                # veut ici, donc kwargs reste intact.

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
