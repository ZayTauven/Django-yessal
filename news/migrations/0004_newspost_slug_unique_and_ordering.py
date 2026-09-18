"""
Deux réparations du module actualités, l'une de schéma, l'autre de données.

1. L'ORDRE PAR DÉFAUT plaçait les brouillons en tête (`-published_at` remonte
   les NULL en premier sous PostgreSQL). Passage en `nulls_last`.

2. LES SLUGS DÉJÀ CASSÉS. La correction de `NewsPost.save()` empêche d'en créer
   de nouveaux, mais ne répare pas ceux qui sont en base. Deux formes :

     · le slug VIDE, produit par un titre non latin — « مرحبا بكم » donnait
       `''`, et l'article restait injoignable ;
     · le slug en DOUBLE, si une base a été peuplée hors du chemin `save()`
       (import, fixture, `bulk_create`), où la contrainte d'unicité ne s'est
       pas exprimée de la même manière.

   La réparation est idempotente : une base saine la traverse sans une écriture.
"""

from django.db import migrations, models
from django.db.models.expressions import F, OrderBy
from django.utils.text import slugify

SLUG_FALLBACK = 'article'
SLUG_MAX = 350


def _base_slug(title):
    """Même cascade que `NewsPost._unique_slug` — ASCII, puis unicode, puis repli."""
    base = slugify(title or '') or slugify(title or '', allow_unicode=True)
    return base.strip('-')[:300] or SLUG_FALLBACK


def repair_slugs(apps, schema_editor):
    NewsPost = apps.get_model('news', 'NewsPost')

    # Les slugs déjà pris, pour ne pas en fabriquer un qui collisionne à son
    # tour. On travaille en mémoire : le corpus d'actualités d'une confrérie se
    # compte en centaines, pas en millions.
    taken = set(
        NewsPost.objects.exclude(slug='').values_list('slug', flat=True)
    )

    broken = []
    seen = set()
    for post in NewsPost.objects.all().order_by('pk'):
        if not post.slug:
            broken.append(post)
        elif post.slug in seen:
            # Doublon : le premier arrivé garde le slug, les suivants sont
            # renumérotés. L'ordre par clé primaire rend la réparation
            # reproductible.
            broken.append(post)
        else:
            seen.add(post.slug)

    for post in broken:
        base = _base_slug(post.title)
        candidate, counter = base, 2
        while candidate in taken:
            suffix = '-%d' % counter
            candidate = '%s%s' % (base[:SLUG_MAX - len(suffix)], suffix)
            counter += 1
        post.slug = candidate
        taken.add(candidate)
        post.save(update_fields=['slug'])


def noop(apps, schema_editor):
    """
    Irréversible par choix : on ne sait pas quel slug vide restaurer, et on ne
    voudrait pas le restaurer. Le sens inverse ne fait donc rien plutôt que
    d'échouer et de bloquer un retour arrière sur le reste.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('news', '0003_alter_newsgalleryimage_image_and_more'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='newspost',
            options={
                'ordering': [
                    OrderBy(F('published_at'), descending=True, nulls_last=True),
                    '-created_at',
                ]
            },
        ),
        migrations.RunPython(repair_slugs, noop),
    ]
