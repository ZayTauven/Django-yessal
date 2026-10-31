"""Destitution, médias privés et anciens mots de passe communs.

Relevés par l'audit de sécurité du 2026-10-31.
"""
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APITestCase

from accounts.models import UserDocument
from accounts.services.user_import import ANCIEN_MOT_DE_PASSE_COMMUN
from core import media

User = get_user_model()

# Le plus petit GIF valide : `ImageField` exige une vraie image.
GIF = (
    b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04'
    b'\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
)


class DestitutionTests(TestCase):
    def test_l_administrateur_retrograde_perd_ses_pouvoirs_et_ses_sessions(self):
        admin = User.objects.create_superuser(email='ancien@ex.org', password='x')
        self.assertEqual(admin.role, User.Role.ADMIN)
        version = admin.token_version

        admin.role = User.Role.MEMBER
        admin.save(update_fields=['role'])
        admin.refresh_from_db()
        self.assertFalse(admin.is_staff)
        self.assertFalse(admin.is_superuser)
        self.assertEqual(admin.token_version, version + 1)

    def test_une_ecriture_sans_changement_de_role_ne_touche_a_rien(self):
        technique = User.objects.create_user(email='ops@ex.org', password='x', is_staff=True)
        technique.save(update_fields=['last_login'])
        technique.first_name = 'Ops'
        technique.save()
        technique.refresh_from_db()
        self.assertTrue(technique.is_staff)

    def test_la_nomination_pose_toujours_is_staff(self):
        membre = User.objects.create_user(email='m@ex.org', password='x')
        membre.role = User.Role.ADMIN
        membre.save()
        self.assertTrue(User.objects.get(pk=membre.pk).is_staff)


class MediasPrivesTests(APITestCase):
    def setUp(self):
        self.membre = User.objects.create_user(email='m@ex.org', password='x', status=User.Status.ACTIVE)
        self.doc = UserDocument.objects.create(
            user=self.membre, doc_type='national_id',
            image=SimpleUploadedFile('IMG-20261012-WA0003.gif', GIF, content_type='image/gif'),
        )

    def test_le_nom_d_origine_disparait(self):
        self.assertNotIn('WA0003', self.doc.image.name)
        self.assertRegex(self.doc.image.name, r'^documents/\d{4}/\d{2}/[0-9a-f]{32}\.gif$')

    def test_sans_signature_le_fichier_n_existe_pas(self):
        self.assertEqual(self.client.get(f'/media/{self.doc.image.name}').status_code, 404)
        r = self.client.get(f'/media/{self.doc.image.name}?e=9999999999&s=faux')
        self.assertEqual(r.status_code, 404)

    def test_le_detour_par_un_dossier_public_ne_passe_pas(self):
        r = self.client.get(f'/media/avatars/../{self.doc.image.name}')
        self.assertEqual(r.status_code, 404)

    def test_le_lien_de_l_api_ouvre_le_fichier_en_cache_prive(self):
        url = self.doc.image.url
        self.assertIn('?e=', url)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertIn('private', r['Cache-Control'])

    def test_le_lien_expire(self):
        url = self.doc.image.url
        with mock.patch('core.media.time.time', return_value=media.time.time() + 25 * 3600):
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_le_lien_reste_stable_dans_la_fenetre(self):
        """Une URL neuve à chaque appel ferait recharger l'image à chaque écran."""
        self.assertEqual(media.signer('documents/x.jpg', 1000.0), media.signer('documents/x.jpg', 2000.0))

    def test_les_dossiers_publics_restent_publics(self):
        self.assertNotIn('?', media.StockageMedias().url('avatars/a.jpg'))


class RegenerationTests(TestCase):
    def setUp(self):
        hache = make_password(ANCIEN_MOT_DE_PASSE_COMMUN)
        self.importes = [
            User.objects.create(email=f'i{n}@ex.org', password=hache, must_change_password=True)
            for n in range(3)
        ]
        self.a_change = User.objects.create_user(email='libre@ex.org', password='choisi-par-lui')
        self.a_change.must_change_password = True
        self.a_change.save()

    def test_inventaire_sans_rien_changer(self):
        sortie = StringIO()
        call_command('regenerer_mots_de_passe_import', stdout=sortie)
        self.assertIn('3 compte(s)', sortie.getvalue())
        self.assertTrue(User.objects.get(email='i0@ex.org').check_password(ANCIEN_MOT_DE_PASSE_COMMUN))

    def test_appliquer_remplace_ferme_les_sessions_et_ecrit_le_fichier(self):
        fichier = Path(tempfile.mkdtemp()) / 'provisoires.csv'
        call_command('regenerer_mots_de_passe_import', appliquer=True, sortie=str(fichier), stdout=StringIO())

        lignes = fichier.read_text(encoding='utf-8-sig').splitlines()
        self.assertEqual(len(lignes), 4)
        provisoire = next(l for l in lignes if 'i0@ex.org' in l).split(';')[-1]
        compte = User.objects.get(email='i0@ex.org')
        self.assertFalse(compte.check_password(ANCIEN_MOT_DE_PASSE_COMMUN))
        self.assertTrue(compte.check_password(provisoire))
        self.assertEqual(compte.token_version, 1)
        self.assertTrue(User.objects.get(email='libre@ex.org').check_password('choisi-par-lui'))
