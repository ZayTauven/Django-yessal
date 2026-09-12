"""
Validateurs partagés.

Le plafond de téléversement est appliqué en DEUX endroits, volontairement :

  · côté front, par le composant `FileDrop`, pour refuser tout de suite et
    expliquer pourquoi — c'est ce que voit l'utilisateur ;
  · ici, parce qu'un contrôle côté client n'est qu'un confort. L'API est
    publique : un envoi direct doit être rejeté aussi.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.utils.deconstruct import deconstructible


def _human_size(num_bytes: int) -> str:
    """2411724 → « 2,3 Mo ». Virgule décimale, comme on l'écrit en français."""
    if num_bytes >= 1024 * 1024:
        value = num_bytes / (1024 * 1024)
        return f"{value:.1f}".replace(".", ",") + " Mo"
    return f"{max(1, round(num_bytes / 1024))} Ko"


@deconstructible
class ValidateUploadSize:
    """
    Refuse un fichier dépassant `settings.MAX_UPLOAD_SIZE`.

    Classe et non fonction, avec `@deconstructible` : Django doit pouvoir
    sérialiser le validateur dans les migrations. Une fonction simple y
    parviendrait aussi, mais une classe permet de surcharger le plafond champ
    par champ si un besoin apparaît (une pièce d'identité et une bannière
    d'actualité n'ont pas les mêmes contraintes).
    """

    def __init__(self, max_bytes: int | None = None):
        self.max_bytes = max_bytes

    @property
    def limit(self) -> int:
        return self.max_bytes or getattr(
            settings, "MAX_UPLOAD_SIZE", 15 * 1024 * 1024
        )

    def __call__(self, value):
        # `value` est un FieldFile ; un champ vide n'a pas de taille à vérifier.
        if not value or not getattr(value, "size", None):
            return

        if value.size > self.limit:
            raise ValidationError(
                "Ce fichier fait %(actual)s : la taille maximale autorisée est "
                "de %(limit)s.",
                code="file_too_large",
                params={
                    "actual": _human_size(value.size),
                    "limit": _human_size(self.limit),
                },
            )

    def __eq__(self, other):
        return isinstance(other, ValidateUploadSize) and self.max_bytes == other.max_bytes


# Instance partagée : c'est elle qu'on rattache aux champs de modèle.
validate_upload_size = ValidateUploadSize()


# ═══════════════════════════════════════════════════════════════════════════
# Extensions autorisées en pièce jointe de conversation
# ═══════════════════════════════════════════════════════════════════════════
# Tous les autres champs de fichier du produit sont des `ImageField` : Pillow
# refuse d'ouvrir ce qui n'est pas une image, donc un .html renommé .png est
# rejeté sans qu'on ait rien à écrire.
#
# `comms.Message.file` est le seul `FileField` nu — une pièce jointe de
# conversation peut légitimement être un PDF ou un tableur. Il n'avait donc
# AUCUN contrôle de nature, et seule la taille était vérifiée.
#
# Constaté le 2026-09-12 depuis un compte membre ordinaire : l'envoi d'un
# fichier .html est accepté (201), puis servi par `django.views.static.serve`
# en `Content-Type: text/html` avec `Content-Disposition: inline`, depuis le
# domaine de l'API. C'est une page arbitraire hébergée sur le domaine HTTPS de
# l'organisation, partageable depuis une conversation. `X-Content-Type-Options:
# nosniff` n'y change rien : le type n'est pas deviné, il est déclaré d'après
# l'extension.
#
# ── Pourquoi une liste blanche, et pas une liste noire ─────────────────────
# Une liste noire demande de connaître à l'avance tout ce qu'un navigateur
# accepte d'exécuter. `.html` et `.svg` viennent à l'esprit ; `.xhtml`, `.xht`,
# `.mhtml`, `.shtml`, `.xml` avec une feuille de style XSLT, ou un `.pdf` avec
# du JavaScript embarqué, beaucoup moins. Chaque oubli est une brèche, et la
# liste doit être tenue à jour à chaque nouveauté des navigateurs.
#
# Une liste blanche se trompe dans l'autre sens : au pire un membre ne peut pas
# envoyer un format rare, le dit, et on ajoute l'extension. L'erreur coûte une
# gêne, pas une faille.
#
# ⚠ Ce contrôle porte sur le NOM du fichier, donc il empêche que le serveur
# annonce `text/html` — c'est exactement ce qu'on veut ici. Il ne prétend pas
# vérifier le contenu : un exécutable renommé `.pdf` passe. C'est la seconde
# moitié du correctif qui s'en charge, dans nginx, en forçant le
# téléchargement au lieu de l'affichage sur `/media/chat_files/`.
EXTENSIONS_PIECE_JOINTE = [
    # Images
    "jpg", "jpeg", "png", "gif", "webp", "bmp", "heic", "heif",
    # Documents
    "pdf", "doc", "docx", "odt", "rtf", "txt",
    # Tableurs et présentations
    "xls", "xlsx", "ods", "csv", "ppt", "pptx", "odp",
    # Audio et vidéo — les messages vocaux passent par là
    "mp3", "m4a", "aac", "ogg", "opus", "wav", "amr",
    "mp4", "mov", "3gp", "webm",
    # Archives
    "zip",
]

validate_extension_piece_jointe = FileExtensionValidator(
    allowed_extensions=EXTENSIONS_PIECE_JOINTE,
    message=(
        "Ce type de fichier n'est pas accepté en pièce jointe. "
        "Formats autorisés : images, PDF, documents bureautiques, audio, "
        "vidéo et archives ZIP."
    ),
)


# ═══════════════════════════════════════════════════════════════════════════
# Réduction des images d'affichage
# ═══════════════════════════════════════════════════════════════════════════
# Le plafond de 15 Mo dit ce qu'on ACCEPTE, pas ce qu'on doit RESERVIR. Une
# bannière de Ndiguel de 9,4 Mo — constatée en base — part telle quelle vers
# chaque navigateur qui ouvre la liste des Ndiguels. Sur une connexion mobile
# sénégalaise, cette image ne « charge mal » pas par accident : elle pèse
# quarante fois ce qu'il faudrait pour un bandeau de 1600 px.
#
# Le téléphone qui l'a prise produit du 4000×3000 ; l'écran qui l'affiche en
# montre 1600 de large au plus. Réduire à la source coûte une passe Pillow au
# téléversement et rend les pages utilisables.
#
# Ce qui n'est PAS traité ici : les pièces d'identité. Un document d'identité
# sert de preuve à un administrateur qui valide une inscription — il n'a pas à
# être ré-encodé, et sa lisibilité prime sur son poids.

import io
import logging

from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)

# Côté le plus long, en pixels. 1600 couvre un bandeau pleine largeur sur un
# écran 2× sans excès.
MAX_IMAGE_SIDE = 1600

# Qualité JPEG/WebP. 82 est le point où l'œil ne distingue plus la
# recompression sur une photo, pour environ un tiers du poids de 95.
IMAGE_QUALITY = 82

# Formats ré-encodables. Un GIF peut être animé — le réduire le figerait — et
# un SVG n'est pas matriciel : on les laisse passer intacts.
RESIZABLE_FORMATS = {"JPEG", "PNG", "WEBP"}


def downscale_image(field_file, max_side: int = MAX_IMAGE_SIDE) -> bool:
    """Réduit une image trop grande, sur place. Renvoie True si elle a changé.

    Ne lève jamais : une image qu'on ne sait pas traiter est conservée telle
    quelle. Perdre un téléversement vaudrait bien pire que le servir gros.
    """
    if not field_file:
        return False

    try:
        from PIL import Image

        field_file.open()
        image = Image.open(field_file)
        image_format = (image.format or "").upper()

        if image_format not in RESIZABLE_FORMATS:
            return False
        if max(image.size) <= max_side:
            return False

        original_size = image.size
        image = image.copy()
        image.thumbnail((max_side, max_side), Image.LANCZOS)

        buffer = io.BytesIO()
        if image_format == "PNG":
            # Un PNG photographique pèse trois fois son équivalent JPEG, mais
            # il porte peut-être une transparence : on garde le format et on
            # se contente de la réduction de dimensions.
            image.save(buffer, format="PNG", optimize=True)
        else:
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            image.save(
                buffer, format=image_format, quality=IMAGE_QUALITY, optimize=True
            )

        # Un fichier DÉJÀ en stockage sera remplacé par une copie sous un
        # nouveau nom : FileSystemStorage n'écrase jamais, il suffixe
        # (`photo.jpg` -> `photo_U5Agq6h.jpg`). Sans suppression de l'ancien,
        # réduire une photo de 9,4 Mo laisse les 9,4 Mo sur le disque et y
        # ajoute la version réduite — on occupe plus de place qu'avant.
        #
        # Pour un téléversement neuf, `_committed` est faux : rien n'est encore
        # écrit, il n'y a donc rien à supprimer.
        ancien_nom = field_file.name if getattr(field_file, "_committed", False) else None

        # `save=False` : on est appelé DEPUIS le save() du modèle. Sauvegarder
        # ici relancerait celui-ci, donc cette fonction, indéfiniment.
        field_file.save(
            field_file.name.rsplit("/", 1)[-1],
            ContentFile(buffer.getvalue()),
            save=False,
        )

        if ancien_nom and ancien_nom != field_file.name:
            try:
                field_file.storage.delete(ancien_nom)
            except OSError:
                # Le fichier réduit est en place : ne pas faire échouer
                # l'opération parce que l'original résiste à la suppression.
                logger.warning("Original non supprimé : %s", ancien_nom)
        logger.info(
            "Image réduite : %s (%sx%s -> %sx%s)",
            field_file.name, *original_size, *image.size,
        )
        return True

    except Exception:
        logger.warning(
            "Réduction impossible pour %s : le fichier est conservé tel quel.",
            getattr(field_file, "name", "?"),
            exc_info=True,
        )
        return False
