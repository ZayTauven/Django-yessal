"""Réduit les images d'affichage déjà en base.

La réduction au téléversement (voir `core.validators.downscale_image`, appelée
depuis le `save()` des modèles concernés) ne vaut que pour l'avenir. Les images
envoyées avant elle restent telles quelles — dont une bannière de Ndiguel de
9,4 Mo, servie intégralement à chaque ouverture de la liste des Ndiguels.

Cette commande rattrape l'existant. Elle est idempotente : une image déjà dans
les bornes est ignorée, donc la relancer ne dégrade rien.

    python manage.py optimize_media --dry-run   # ce qui serait réduit
    python manage.py optimize_media             # réduit pour de bon

Les pièces d'identité (`accounts.UserDocument`) sont volontairement exclues :
elles servent de preuve à l'administrateur qui valide une inscription, et leur
lisibilité prime sur leur poids.
"""

from django.apps import apps
from django.core.management.base import BaseCommand

from core.validators import MAX_IMAGE_SIDE, downscale_image

# (label du modèle, champs à traiter)
CIBLES = [
    ('accounts.User', ['avatar']),
    ('events.Campaign', ['illustrative_photo']),
    ('news.NewsPost', ['cover_image']),
    ('news.NewsGalleryImage', ['image']),
]


class Command(BaseCommand):
    help = "Réduit les images d'affichage déjà enregistrées (hors pièces d'identité)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Montre ce qui serait réduit, sans rien écrire.",
        )
        parser.add_argument(
            '--max-side',
            type=int,
            default=MAX_IMAGE_SIDE,
            help=f"Côté le plus long, en pixels (défaut : {MAX_IMAGE_SIDE}).",
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        max_side = options['max_side']

        traites = 0
        gagnes = 0

        for label, champs in CIBLES:
            model = apps.get_model(label)
            for obj in model.objects.all().iterator():
                for champ in champs:
                    fichier = getattr(obj, champ, None)
                    if not fichier:
                        continue

                    try:
                        avant = fichier.size
                    except (OSError, ValueError):
                        # Fichier absent du disque : c'est l'affaire de
                        # `check_media`, pas de celle-ci.
                        continue

                    if dry_run:
                        # On mesure sans écrire : Pillow lit les dimensions
                        # depuis l'en-tête, sans décoder l'image entière.
                        try:
                            from PIL import Image

                            fichier.open()
                            with Image.open(fichier) as im:
                                cote = max(im.size)
                                dims = im.size
                        except Exception:
                            continue
                        if cote > max_side:
                            self.stdout.write(
                                f"  {label}.{champ} #{obj.pk} : "
                                f"{dims[0]}x{dims[1]}, {avant / 1048576:.1f} Mo"
                            )
                            traites += 1
                        continue

                    if downscale_image(fichier, max_side=max_side):
                        # `update` plutôt que `obj.save()` : le save() du modèle
                        # rappellerait downscale_image, cette fois sans effet,
                        # mais surtout il rejouerait toute la logique métier
                        # (slug, dates de publication) sans raison.
                        model.objects.filter(pk=obj.pk).update(**{champ: fichier.name})
                        apres = fichier.size
                        gagnes += avant - apres
                        traites += 1
                        self.stdout.write(
                            f"  {label}.{champ} #{obj.pk} : "
                            f"{avant / 1048576:.1f} Mo -> {apres / 1048576:.1f} Mo"
                        )

        if not traites:
            self.stdout.write(self.style.SUCCESS(
                "Aucune image à réduire : tout tient déjà dans les bornes."
            ))
            return

        if dry_run:
            self.stdout.write("")
            self.stdout.write(
                f"{traites} image(s) dépassent {max_side} px. "
                "Relancer sans --dry-run pour les réduire."
            )
            return

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"{traites} image(s) réduite(s), {gagnes / 1048576:.1f} Mo économisés."
        ))
