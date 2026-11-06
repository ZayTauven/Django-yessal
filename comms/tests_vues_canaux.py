"""Le compteur de vues des publications de canal."""

from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from comms.models import Chat, ChatMembership, Message


class VuesTests(APITestCase):
    def setUp(self):
        mock.patch('comms.services.trigger_pusher').start()
        mock.patch('comms.services.send_fcm_push').start()
        mock.patch('comms.services.lancer_en_arriere_plan', side_effect=lambda fn: fn()).start()
        self.addCleanup(mock.patch.stopall)

        def compte(nom):
            return User.objects.create_user(
                email=f'{nom}@test.sn', password='Motdepasse123!', status=User.Status.ACTIVE, first_name=nom,
            )

        self.auteur, self.awa, self.modou, self.fatou = (compte(n) for n in ('auteur', 'awa', 'modou', 'fatou'))
        self.canal = Chat.objects.create(chat_type=Chat.ChatType.CHANNEL, name='Annonces', created_by=self.auteur)
        ChatMembership.objects.create(chat=self.canal, user=self.auteur, can_post=True)
        for membre in (self.awa, self.modou, self.fatou):
            ChatMembership.objects.create(chat=self.canal, user=membre)
        self.client.force_authenticate(self.awa)

    def publier(self, il_y_a=0, chat=None):
        message = Message.objects.create(chat=chat or self.canal, sender=self.auteur, content='Annonce')
        if il_y_a:
            Message.objects.filter(pk=message.pk).update(sent_at=timezone.now() - timedelta(minutes=il_y_a))
            message.refresh_from_db()
        return message

    def lire(self, membre, il_y_a=0):
        ChatMembership.objects.filter(chat=self.canal, user=membre).update(
            last_read_at=timezone.now() - timedelta(minutes=il_y_a),
        )

    def fil(self, chat=None):
        reponse = self.client.get(f'/api/comms/messages/?chat={(chat or self.canal).id}')
        self.assertEqual(reponse.status_code, 200, reponse.content)
        corps = reponse.json()
        return corps['results'] if isinstance(corps, dict) else corps

    def test_une_publication_que_personne_n_a_vue(self):
        self.publier()

        self.assertEqual(self.fil()[0]['view_count'], 0)

    def test_chaque_membre_qui_ouvre_le_canal_compte_une_fois(self):
        self.publier(il_y_a=10)
        self.lire(self.awa)
        self.lire(self.modou)

        self.assertEqual(self.fil()[0]['view_count'], 2)

        self.client.post(f'/api/comms/{self.canal.id}/read/')
        self.client.post(f'/api/comms/{self.canal.id}/read/')
        self.assertEqual(self.fil()[0]['view_count'], 2)

    def test_une_lecture_anterieure_ne_compte_pas(self):
        self.lire(self.awa, il_y_a=30)
        self.publier(il_y_a=10)

        self.assertEqual(self.fil()[0]['view_count'], 0)

    def test_chaque_publication_a_son_propre_compte(self):
        self.publier(il_y_a=60)
        self.publier(il_y_a=5)
        self.lire(self.awa, il_y_a=30)
        self.lire(self.modou)

        ancienne, recente = self.fil()
        self.assertEqual(ancienne['view_count'], 2)
        self.assertEqual(recente['view_count'], 1)

    def test_l_auteur_ne_se_compte_pas(self):
        self.publier(il_y_a=10)
        self.lire(self.auteur)

        self.assertEqual(self.fil()[0]['view_count'], 0)

    def test_publier_par_l_api_ne_se_compte_pas(self):
        self.client.force_authenticate(self.auteur)
        reponse = self.client.post(
            '/api/comms/messages/', {'chat': self.canal.id, 'content': 'Annonce'}, format='json',
        )

        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertEqual(reponse.json()['view_count'], 0)

    def test_pas_de_compteur_hors_des_canaux(self):
        salon = Chat.objects.create(chat_type=Chat.ChatType.GROUP, name='Salon', created_by=self.auteur)
        ChatMembership.objects.create(chat=salon, user=self.awa)
        self.publier(chat=salon)

        self.assertIsNone(self.fil(salon)[0]['view_count'])

    def test_pas_de_compteur_sur_un_message_supprime(self):
        Message.objects.filter(pk=self.publier().pk).update(is_deleted=True)

        self.assertIsNone(self.fil()[0]['view_count'])

    def test_le_nombre_de_requetes_ne_depend_pas_du_nombre_de_messages(self):
        self.publier()
        with CaptureQueriesContext(connection) as peu:
            self.fil()

        for _ in range(15):
            self.publier()
        with CaptureQueriesContext(connection) as beaucoup:
            self.fil()

        self.assertEqual(len(peu), len(beaucoup))
