"""Tests sur le traitement des images téléversées.

Deux garanties à tenir, opposées l'une à l'autre :

  · une image d'AFFICHAGE (bannière, avatar) doit être réduite, sans quoi une
    photo de téléphone de 9 Mo part telle quelle vers chaque visiteur ;
  · une PIÈCE D'IDENTITÉ ne doit PAS l'être : elle sert de preuve à
    l'administrateur qui valide une inscription, et sa lisibilité prime.
"""

import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from accounts.models import UserDocument
from core.validators import MAX_IMAGE_SIDE
from events.models import Campaign, Fete
from news.models import NewsGalleryImage, NewsPost

User = get_user_model()


def image_fichier(nom='photo.jpg', taille=(4000, 3000), fmt='JPEG'):
    """Fabrique une image en mémoire, sans dépendre d'un fichier du dépôt."""
    tampon = io.BytesIO()
    # Un dégradé plutôt qu'un aplat : un aplat se compresse à quelques octets
    # et ne dirait rien du gain réel.
    image = Image.new('RGB', taille)
    image.putdata([
        ((x * 7) % 256, (y * 5) % 256, ((x + y) * 3) % 256)
        for y in range(taille[1])
        for x in range(taille[0])
    ])
    image.save(tampon, format=fmt)
    return SimpleUploadedFile(nom, tampon.getvalue(), content_type=f'image/{fmt.lower()}')


def dimensions(field_file):
    field_file.open()
    with Image.open(field_file) as im:
        return im.size


class ReductionImagesAffichageTests(TestCase):
    def setUp(self):
        self.auteur = User.objects.create_user(
            email='auteur@test.com', password='Auteur123!', role=User.Role.ADMIN
        )

    def test_banniere_actualite_est_reduite(self):
        post = NewsPost.objects.create(
            title='Grand Magal',
            content='…',
            cover_image=image_fichier(taille=(4000, 3000)),
        )

        largeur, hauteur = dimensions(post.cover_image)
        self.assertLessEqual(max(largeur, hauteur), MAX_IMAGE_SIDE)
        # Le rapport d'aspect doit tenir : 4000×3000 → 1600×1200.
        self.assertAlmostEqual(largeur / hauteur, 4 / 3, places=2)

    def test_image_deja_petite_reste_intacte(self):
        post = NewsPost.objects.create(
            title='Petite',
            content='…',
            cover_image=image_fichier(taille=(800, 600)),
        )

        self.assertEqual(dimensions(post.cover_image), (800, 600))

    def test_photo_de_ndiguel_est_reduite(self):
        fete = Fete.objects.create(name='Magal', date='2026-01-01')
        campagne = Campaign.objects.create(
            name='Ndiguel Magal',
            fete=fete,
            goal_amount=100000,
            deadline='2026-12-31',
            illustrative_photo=image_fichier(taille=(3000, 2000)),
        )

        self.assertLessEqual(max(dimensions(campagne.illustrative_photo)), MAX_IMAGE_SIDE)

    def test_avatar_est_reduit(self):
        membre = User.objects.create_user(
            email='avatar@test.com',
            password='Avatar123!',
            avatar=image_fichier('moi.jpg', taille=(2400, 2400)),
        )

        self.assertLessEqual(max(dimensions(membre.avatar)), MAX_IMAGE_SIDE)

    def test_image_de_galerie_est_reduite(self):
        post = NewsPost.objects.create(title='Avec galerie', content='…')
        img = NewsGalleryImage.objects.create(
            post=post, image=image_fichier(taille=(3200, 1800))
        )

        self.assertLessEqual(max(dimensions(img.image)), MAX_IMAGE_SIDE)

    def test_le_poids_baisse_reellement(self):
        """La réduction doit se voir sur l'octet, pas seulement sur le pixel."""
        source = image_fichier(taille=(4000, 3000))
        poids_initial = source.size

        post = NewsPost.objects.create(
            title='Poids', content='…', cover_image=source
        )

        self.assertLess(post.cover_image.size, poids_initial / 2)


class DocumentsNonAlteresTests(TestCase):
    """Une pièce d'identité n'est jamais ré-encodée.

    C'est la contrepartie du test précédent : la réduction est appliquée
    champ par champ, explicitement, et surtout PAS ici.
    """

    def test_piece_identite_conserve_ses_dimensions(self):
        membre = User.objects.create_user(
            email='doc@test.com', password='Doc123!', role=User.Role.MEMBER
        )

        doc = UserDocument.objects.create(
            user=membre,
            image=image_fichier('cni.jpg', taille=(3000, 2000)),
        )

        self.assertEqual(dimensions(doc.image), (3000, 2000))
