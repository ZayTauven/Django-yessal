"""Les écritures que la messagerie offrait à n'importe quel membre.

Relevés par l'audit de sécurité du 2026-10-31 : le `ModelViewSet` des fils et
celui des annonces laissaient PATCH et DELETE à tout compte qui pouvait LIRE.
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import LDD, Daara
from comms.models import Announcement, Chat, Message, UserMessagingPreferences
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


class CanalCommunTests(SansReseau):
    """`private-kourel` est ouvert à tout membre : un canal ciblé n'y passe pas."""

    def setUp(self):
        super().setUp()
        self.admin = creer('admin@ex.org', role=User.Role.ADMIN)
        self.membre = creer('m@ex.org')

    def publier(self, canal):
        with self.captureOnCommitCallbacks(execute=True):
            Message.objects.create(chat=canal, sender=self.admin, content='Montants du titre')
            from comms.services import diffuser_message
            diffuser_message(canal.messages.latest('id'))
        return [appel.args[0] for appel in self.pusher.call_args_list]

    def test_un_canal_pour_tous_part_sur_le_canal_commun(self):
        canal = salon(self.admin, self.membre, chat_type=Chat.ChatType.CHANNEL, name='Les Jëfs du Kourel')
        self.assertIn('private-kourel', self.publier(canal))

    def test_un_canal_cible_ne_part_qu_a_ses_membres(self):
        canal = salon(self.admin, self.membre, chat_type=Chat.ChatType.CHANNEL, name='Diwan')
        canal.audience = {'titres': [1]}
        canal.save(update_fields=['audience'])
        canaux = self.publier(canal)
        self.assertNotIn('private-kourel', canaux)
        self.assertIn([f'private-user.{self.admin.id}', f'private-user.{self.membre.id}'], canaux)


class AnnonceDuCoordinateurTests(APITestCase):
    def setUp(self):
        ldd = LDD.objects.create(code='DS S17', name='KAOLACK1')
        self.kande = Daara.objects.create(name='KANDE', ldd=ldd)
        self.coordo = creer('coordo@ex.org', role=User.Role.CHEF_DAARA, daara=self.kande)

    def test_une_annonce_de_coordinateur_reste_dans_son_daara(self):
        self.client.force_authenticate(self.coordo)
        r = self.client.post('/api/comms/announcements/', {
            'title': 'Magal', 'content': 'Départ samedi', 'target': 'global', 'urgency': 'info',
        }, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        annonce = Announcement.objects.get(pk=r.data['id'])
        self.assertEqual(annonce.target, Announcement.Target.DAARA_ONLY)
        self.assertEqual(annonce.daara_id, self.kande.id)


class CreationDeSalonTests(SansReseau):
    def setUp(self):
        super().setUp()
        ldd = LDD.objects.create(code='DS S17', name='KAOLACK1')
        self.kande = Daara.objects.create(name='KANDE', ldd=ldd)
        self.autre = Daara.objects.create(name='FAWZEYNI', ldd=ldd)
        self.awa = creer('awa@ex.org', daara=self.kande, status=User.Status.ACTIVE)
        self.voisin = creer('voisin@ex.org', daara=self.kande, status=User.Status.ACTIVE)
        self.ailleurs = creer('ailleurs@ex.org', daara=self.autre, status=User.Status.ACTIVE)
        creer('coordo@ex.org', role=User.Role.CHEF_DAARA, daara=self.autre, status=User.Status.ACTIVE)
        self.client.force_authenticate(self.awa)

    def creer_salon(self, **donnees):
        return self.client.post('/api/comms/', {'name': 'Salon', **donnees}, format='json')

    def test_un_talube_n_invite_pas_tout_le_kourel(self):
        self.assertEqual(self.creer_salon(invite_mode='global_chefs').status_code, 403)
        self.assertFalse(Chat.objects.filter(name='Salon').exists())

    def test_un_talube_n_invite_pas_le_daara_d_un_autre(self):
        r = self.creer_salon(invite_mode='daara_all', preset_daara_id=self.autre.id)
        self.assertEqual(r.status_code, 403)

    def test_son_propre_daara_reste_permis(self):
        r = self.creer_salon(invite_mode='daara_all')
        self.assertEqual(r.status_code, 201, r.data)
        membres = set(Chat.objects.get(pk=r.data['id']).memberships.values_list('user_id', flat=True))
        self.assertIn(self.voisin.id, membres)
        self.assertNotIn(self.ailleurs.id, membres)

    def test_les_invitations_de_groupe_fermees_sont_respectees(self):
        prefs, _ = UserMessagingPreferences.objects.get_or_create(user=self.voisin)
        prefs.allow_group_invites = False
        prefs.save()
        r = self.creer_salon(invite_mode='manual', member_ids=[self.voisin.id])
        self.assertEqual(r.status_code, 201, r.data)
        membres = set(Chat.objects.get(pk=r.data['id']).memberships.values_list('user_id', flat=True))
        self.assertNotIn(self.voisin.id, membres)
