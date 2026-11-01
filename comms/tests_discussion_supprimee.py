"""« Supprimer la discussion » d'un tête-à-tête, comme WhatsApp (2026-11-01).

Chez celui qui supprime seulement : son historique disparaît et le fil sort de
sa liste ; l'autre garde tout. Le prochain message fait revenir le fil, à
partir de ce message. Réécrire à la même personne le rouvre aussi.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from comms.models import Chat, ChatMembership, Message

User = get_user_model()


class DiscussionSupprimeeTests(APITestCase):
    def setUp(self):
        mock.patch('comms.services.trigger_pusher').start()
        mock.patch('comms.services.send_fcm_push').start()
        mock.patch('comms.services.lancer_en_arriere_plan', side_effect=lambda fn: fn()).start()
        self.addCleanup(mock.patch.stopall)

        def compte(email):
            return User.objects.create_user(email=email, password='x', status=User.Status.ACTIVE,
                                            first_name=email.split('@')[0].capitalize())

        self.awa, self.modou = compte('awa@ex.org'), compte('modou@ex.org')
        self.fil = Chat.objects.create(chat_type=Chat.ChatType.DIRECT, created_by=self.awa)
        for u in (self.awa, self.modou):
            ChatMembership.objects.create(chat=self.fil, user=u)
        self.ancien = Message.objects.create(chat=self.fil, sender=self.modou, content='Ancien message')

    def fils(self, user):
        self.client.force_authenticate(user)
        data = self.client.get('/api/comms/').json()
        return [c['id'] for c in (data['results'] if isinstance(data, dict) else data)]

    def messages(self, user):
        self.client.force_authenticate(user)
        data = self.client.get(f'/api/comms/messages/?chat={self.fil.id}').json()
        return [m['content'] for m in (data['results'] if isinstance(data, dict) else data)]

    def supprimer(self, user):
        self.client.force_authenticate(user)
        return self.client.post(f'/api/comms/{self.fil.id}/clear/')

    def test_supprimer_ne_touche_que_soi(self):
        self.assertEqual(self.supprimer(self.awa).status_code, status.HTTP_204_NO_CONTENT)
        self.assertNotIn(self.fil.id, self.fils(self.awa))
        self.assertEqual(self.messages(self.awa), [])
        # L'autre garde le fil et tout l'historique.
        self.assertIn(self.fil.id, self.fils(self.modou))
        self.assertIn('Ancien message', self.messages(self.modou))

    def test_un_nouveau_message_fait_revenir_le_fil_a_zero(self):
        self.supprimer(self.awa)
        Message.objects.create(chat=self.fil, sender=self.modou, content='Salam')
        self.assertIn(self.fil.id, self.fils(self.awa))
        self.assertEqual(self.messages(self.awa), ['Salam'])
        # L'aperçu de la liste ne montre pas non plus l'ancien message.
        self.client.force_authenticate(self.awa)
        data = self.client.get('/api/comms/').json()
        rangee = next(c for c in (data['results'] if isinstance(data, dict) else data) if c['id'] == self.fil.id)
        self.assertNotIn('Ancien', str(rangee.get('last_message')))

    def test_reecrire_rouvre_la_discussion_au_lieu_de_la_refuser(self):
        self.supprimer(self.awa)
        self.client.force_authenticate(self.awa)
        r = self.client.post('/api/comms/invitations/', {'recipient': self.modou.id}, format='json')
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.content)
        self.assertTrue(r.json()['direct'])
        self.assertEqual(r.json()['chat']['id'], self.fil.id)
        self.assertIn(self.fil.id, self.fils(self.awa))
        self.assertEqual(self.messages(self.awa), [])

    def test_seul_un_tete_a_tete_se_supprime_ainsi(self):
        salon = Chat.objects.create(chat_type=Chat.ChatType.GROUP, name='Salon', created_by=self.awa)
        ChatMembership.objects.create(chat=salon, user=self.awa)
        self.client.force_authenticate(self.awa)
        self.assertEqual(self.client.post(f'/api/comms/{salon.id}/clear/').status_code, status.HTTP_400_BAD_REQUEST)
