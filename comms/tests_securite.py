"""Les écritures que la messagerie offrait à n'importe quel membre.

Relevés par l'audit de sécurité du 2026-10-31 : le `ModelViewSet` des fils et
celui des annonces laissaient PATCH et DELETE à tout compte qui pouvait LIRE.
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import LDD, Daara
from comms.models import Announcement, Chat, Message
from comms.tests_messagerie import SansReseau, creer, salon

User = get_user_model()


class FilsEnLectureSeuleTests(SansReseau):
    def setUp(self):
        super().setUp()
        self.admin = creer('admin@ex.org', role=User.Role.ADMIN)
        self.talube = creer('talube@ex.org')
        self.canal = salon(self.admin, self.talube, chat_type=Chat.ChatType.CHANNEL, name='Les Jëfs du Kourel')
        Message.objects.create(chat=self.canal, sender=self.admin, content='Bienvenue')
        self.client.force_authenticate(self.talube)

    def test_un_membre_ne_supprime_pas_un_canal(self):
        r = self.client.delete(f'/api/comms/{self.canal.id}/')
        self.assertEqual(r.status_code, 405)
        self.assertTrue(Chat.objects.filter(pk=self.canal.pk).exists())
        self.assertEqual(self.canal.messages.count(), 1)

    def test_un_membre_ne_change_pas_un_canal_en_salon(self):
        for methode in (self.client.patch, self.client.put):
            r = methode(f'/api/comms/{self.canal.id}/', {'chat_type': 'group', 'show_amounts': True})
            self.assertEqual(r.status_code, 405)
        self.canal.refresh_from_db()
        self.assertEqual(self.canal.chat_type, Chat.ChatType.CHANNEL)

    def test_meme_l_administrateur_passe_par_les_actions(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.delete(f'/api/comms/{self.canal.id}/').status_code, 405)


class AnnoncesTests(APITestCase):
    def setUp(self):
        ldd = LDD.objects.create(code='DS S17', name='KAOLACK1')
        self.kande = Daara.objects.create(name='KANDE', ldd=ldd)
        self.autre = Daara.objects.create(name='FAWZEYNI', ldd=ldd)
        self.admin = creer('admin@ex.org', role=User.Role.ADMIN)
        self.talube = creer('talube@ex.org', daara=self.kande)
        self.coordo = creer('coordo@ex.org', role=User.Role.CHEF_DAARA, daara=self.kande)
        self.globale = Announcement.objects.create(title='Magal', content='Wave : 77 000 00 00')
        self.locale = Announcement.objects.create(
            title='Réunion', content='Samedi', target=Announcement.Target.DAARA_ONLY, daara=self.kande,
        )

    def url(self, annonce):
        return f'/api/comms/announcements/{annonce.id}/'

    def test_un_talube_ne_reecrit_ni_ne_supprime(self):
        self.client.force_authenticate(self.talube)
        r = self.client.patch(self.url(self.globale), {'content': 'Wave : 70 666 66 66'})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.delete(self.url(self.globale)).status_code, 403)
        self.globale.refresh_from_db()
        self.assertEqual(self.globale.content, 'Wave : 77 000 00 00')

    def test_un_coordinateur_ne_touche_que_son_daara(self):
        self.client.force_authenticate(self.coordo)
        self.assertEqual(self.client.patch(self.url(self.globale), {'title': 'x'}).status_code, 403)
        self.assertEqual(self.client.patch(self.url(self.locale), {'title': 'Réunion déplacée'}).status_code, 200)

    def test_un_coordinateur_n_elargit_pas_la_portee(self):
        self.client.force_authenticate(self.coordo)
        r = self.client.patch(self.url(self.locale), {'target': 'global'})
        self.assertEqual(r.status_code, 403)
        self.locale.refresh_from_db()
        self.assertEqual(self.locale.target, Announcement.Target.DAARA_ONLY)

    def test_l_administrateur_retire_une_annonce(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.delete(self.url(self.globale)).status_code, 204)
