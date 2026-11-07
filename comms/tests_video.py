"""Les vidéos de la messagerie : leur plafond, leur aperçu, leur lecture par portions."""

import base64
import io
import os
import shutil
import tempfile
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, override_settings
from PIL import Image
from rest_framework.test import APITestCase

from accounts.models import User
from comms.models import Chat, ChatMembership, Message
from comms.serializers import vignette_depuis_base64
from core.media import servir_media, signer
from core.validators import validate_piece_jointe_size

UN_MO = 1024 * 1024


def jpeg_base64(cote=1000):
    tampon = io.BytesIO()
    Image.new('RGB', (cote, cote), 'purple').save(tampon, format='JPEG')
    return base64.b64encode(tampon.getvalue()).decode()


class PlafondTests(SimpleTestCase):
    def fichier(self, nom, taille):
        return SimpleNamespace(name=nom, size=taille)

    def test_une_video_passe_au_dela_du_plafond_ordinaire(self):
        validate_piece_jointe_size(self.fichier('film.mp4', 80 * UN_MO))
        validate_piece_jointe_size(self.fichier('FILM.MOV', 80 * UN_MO))

    def test_une_video_a_son_propre_plafond(self):
        with self.assertRaises(ValidationError) as erreur:
            validate_piece_jointe_size(self.fichier('film.mp4', 101 * UN_MO))
        self.assertIn('100,0 Mo', str(erreur.exception))

    def test_les_autres_fichiers_restent_a_15_mo(self):
        validate_piece_jointe_size(self.fichier('recu.pdf', 15 * UN_MO))
        for nom in ('recu.pdf', 'photo.jpg', 'note.webm'):
            with self.assertRaises(ValidationError):
                validate_piece_jointe_size(self.fichier(nom, 16 * UN_MO))

    @override_settings(MAX_VIDEO_UPLOAD_SIZE=200 * UN_MO)
    def test_le_plafond_se_regle_sans_toucher_au_code(self):
        validate_piece_jointe_size(self.fichier('film.mp4', 150 * UN_MO))


class VignetteTests(SimpleTestCase):
    def test_une_vignette_est_reencodee_et_reduite(self):
        vignette = vignette_depuis_base64(jpeg_base64(1000))
        image = Image.open(vignette)
        self.assertEqual(image.format, 'JPEG')
        self.assertEqual(max(image.size), 640)

    def test_une_vignette_inutilisable_est_ignoree_sans_erreur(self):
        faux = base64.b64encode(b'<script>alert(1)</script>').decode()
        for valeur in ('', None, 'pas du base64 !', faux):
            self.assertIsNone(vignette_depuis_base64(valeur))

    def test_une_vignette_trop_lourde_est_ignoree(self):
        lourde = base64.b64encode(b'\xff' * (600 * 1024)).decode()
        self.assertIsNone(vignette_depuis_base64(lourde))


class EnvoiVideoTests(APITestCase):
    def setUp(self):
        self.dossier = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dossier, ignore_errors=True)
        reglage = override_settings(MEDIA_ROOT=self.dossier)
        reglage.enable()
        self.addCleanup(reglage.disable)

        self.awa = User.objects.create_user(
            email='awa@test.sn', password='Motdepasse123!', status=User.Status.ACTIVE,
            first_name='Awa', last_name='Sall',
        )
        self.chat = Chat.objects.create(chat_type=Chat.ChatType.GROUP, name='Salon', created_by=self.awa)
        ChatMembership.objects.create(chat=self.chat, user=self.awa)
        self.client.force_authenticate(self.awa)

    def envoyer(self, nom='film.mp4', **champs):
        fichier = SimpleUploadedFile(nom, b'\x00' * 2048, content_type='video/mp4')
        return self.client.post(
            '/api/comms/messages/',
            {'chat': self.chat.id, 'file': fichier, **champs},
            format='multipart',
        )

    def test_une_video_garde_son_apercu_et_sa_duree(self):
        r = self.envoyer(thumbnail_b64=jpeg_base64(), file_duration=45)

        self.assertEqual(r.status_code, 201, r.content)
        corps = r.json()
        self.assertEqual(corps['file_kind'], 'video')
        self.assertEqual(corps['file_duration'], 45)
        self.assertIn('/chat_files/vignettes/', corps['file_thumbnail_url'])
        self.assertNotIn('thumbnail_b64', corps)

    def test_l_apercu_est_derriere_un_lien_signe(self):
        corps = self.envoyer(thumbnail_b64=jpeg_base64()).json()

        self.assertIn('?e=', corps['file_thumbnail_url'])

    def test_une_video_part_sans_apercu(self):
        r = self.envoyer(thumbnail_b64='illisible')

        self.assertEqual(r.status_code, 201, r.content)
        self.assertIsNone(r.json()['file_thumbnail_url'])

    def test_un_document_n_a_ni_apercu_ni_duree(self):
        r = self.envoyer(nom='recu.pdf', thumbnail_b64=jpeg_base64(), file_duration=45)

        self.assertEqual(r.status_code, 201, r.content)
        self.assertIsNone(r.json()['file_thumbnail_url'])
        self.assertIsNone(r.json()['file_duration'])

    def test_un_livre_epub_est_accepte_comme_document(self):
        fichier = SimpleUploadedFile('livre.epub', b'PK\x03\x04', content_type='application/epub+zip')
        r = self.client.post(
            '/api/comms/messages/', {'chat': self.chat.id, 'file': fichier}, format='multipart',
        )

        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['file_kind'], 'document')

    def test_un_format_hors_liste_reste_refuse(self):
        fichier = SimpleUploadedFile('page.html', b'<html>', content_type='text/html')
        r = self.client.post(
            '/api/comms/messages/', {'chat': self.chat.id, 'file': fichier}, format='multipart',
        )

        self.assertEqual(r.status_code, 400, r.content)

    def test_supprimer_le_message_efface_la_video_et_son_apercu(self):
        corps = self.envoyer(thumbnail_b64=jpeg_base64()).json()
        message = Message.objects.get(pk=corps['id'])
        chemins = [message.file.path, message.file_thumbnail.path]

        r = self.client.delete(f"/api/comms/messages/{corps['id']}/")

        self.assertEqual(r.status_code, 200, r.content)
        for chemin in chemins:
            self.assertFalse(os.path.exists(chemin))
        message.refresh_from_db()
        self.assertFalse(message.file_thumbnail)


class LecturePartielleTests(SimpleTestCase):
    CONTENU = bytes(range(256)) * 4

    def setUp(self):
        self.dossier = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dossier, ignore_errors=True)
        os.makedirs(os.path.join(self.dossier, 'chat_files'))
        self.chemin = 'chat_files/film.mp4'
        with open(os.path.join(self.dossier, self.chemin), 'wb') as sortie:
            sortie.write(self.CONTENU)

    def lire(self, plage=None, signature=True):
        requete = f'?{signer(self.chemin)}' if signature else ''
        en_tetes = {'HTTP_RANGE': plage} if plage else {}
        return servir_media(
            RequestFactory().get(f'/media/{self.chemin}{requete}', **en_tetes),
            self.chemin,
            document_root=self.dossier,
        )

    def corps(self, reponse):
        return b''.join(reponse.streaming_content)

    def test_sans_plage_le_fichier_entier_annonce_les_portions(self):
        r = self.lire()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Accept-Ranges'], 'bytes')
        self.assertEqual(self.corps(r), self.CONTENU)

    def test_une_plage_bornee(self):
        r = self.lire('bytes=0-99')
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r['Content-Range'], f'bytes 0-99/{len(self.CONTENU)}')
        self.assertEqual(r['Content-Length'], '100')
        self.assertEqual(r['Content-Type'], 'video/mp4')
        self.assertEqual(self.corps(r), self.CONTENU[:100])

    def test_une_plage_ouverte_et_une_fin_de_fichier(self):
        self.assertEqual(self.corps(self.lire('bytes=1000-')), self.CONTENU[1000:])
        self.assertEqual(self.corps(self.lire('bytes=-24')), self.CONTENU[-24:])

    def test_une_fin_trop_loin_est_ramenee_au_fichier(self):
        r = self.lire('bytes=1000-999999')
        self.assertEqual(r.status_code, 206)
        self.assertEqual(self.corps(r), self.CONTENU[1000:])

    def test_une_plage_hors_du_fichier_est_refusee(self):
        r = self.lire('bytes=999999-')
        self.assertEqual(r.status_code, 416)
        self.assertEqual(r['Content-Range'], f'bytes */{len(self.CONTENU)}')

    def test_plusieurs_plages_recoivent_le_fichier_entier(self):
        self.assertEqual(self.lire('bytes=0-1,5-6').status_code, 200)

    def test_une_plage_n_ouvre_pas_un_fichier_sans_signature(self):
        with self.assertRaises(Http404):
            self.lire('bytes=0-99', signature=False)
