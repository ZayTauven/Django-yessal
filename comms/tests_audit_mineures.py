"""Audit des modules du 2026-10-31, incohérences mineures.

  · le pilotage « accepter / refuser les invitations » se réglait sans effet ;
  · les photos de galerie envoyées à la création d'un article sautaient tout
    contrôle de taille et de format ;
  · dans un salon, seul le créateur épinglait, et un modérateur ne retirait
    pas un message déplacé ;
  · un fil archivé restait modifiable (édition, réactions, suppression).
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from comms.models import Chat, ChatInvitation, ChatMembership, Message, MessagingPilotageConfig
from news.models import NewsPost

User = get_user_model()


class MineuresBase(APITestCase):
    def setUp(self):
        def compte(email, role=User.Role.MEMBER):
            return User.objects.create_user(
                email=email, password='Motdepasse123!', role=role, status=User.Status.ACTIVE,
                first_name=email.split('@')[0].capitalize(), last_name='Test',
            )

        self.admin = compte('admin@test.sn', User.Role.ADMIN)
        self.awa = compte('awa@test.sn')
        self.modou = compte('modou@test.sn')
        self.binta = compte('binta@test.sn')

    def en(self, user):
        self.client.force_authenticate(user)


class InvitationAutomatiqueTests(MineuresBase):
    def test_sans_acceptation_la_discussion_s_ouvre_d_office(self):
        MessagingPilotageConfig.objects.create(daara=None, allow_invite_accept_decline=False)
        self.en(self.awa)
        r = self.client.post('/api/comms/invitations/', {'recipient': self.modou.id}, format='json')
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        invitation = ChatInvitation.objects.get()
        self.assertEqual(invitation.status, ChatInvitation.Status.ACCEPTED)
        self.assertTrue(ChatMembership.objects.filter(chat=invitation.chat, user=self.modou).exists())
        # Les clients ouvrent le fil sur `direct` : sans lui, ils annonçaient
        # « demande envoyée » pour une discussion déjà ouverte.
        self.assertTrue(r.data['direct'])
        self.assertEqual(r.data['chat']['id'], invitation.chat_id)

    def test_par_defaut_l_invitation_attend_une_reponse(self):
        self.en(self.awa)
        r = self.client.post('/api/comms/invitations/', {'recipient': self.modou.id}, format='json')
        self.assertEqual(ChatInvitation.objects.get().status, ChatInvitation.Status.PENDING)
        self.assertNotIn('direct', r.data)


class GalerieTests(MineuresBase):
    def test_un_fichier_qui_n_est_pas_une_image_est_refuse(self):
        self.en(self.admin)
        faux = SimpleUploadedFile('page.html', b'<script>alert(1)</script>', content_type='text/html')
        r = self.client.post('/api/news/posts/', {
            'title': 'Magal', 'content': 'x', 'gallery_images': [faux],
        }, format='multipart')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        # Rien n'est créé à moitié : l'article est refusé avec sa photo.
        self.assertFalse(NewsPost.objects.exists())


class SalonModereTests(MineuresBase):
    def setUp(self):
        super().setUp()
        self.salon = Chat.objects.create(chat_type=Chat.ChatType.GROUP, name='Salon', created_by=self.awa)
        ChatMembership.objects.create(chat=self.salon, user=self.awa, is_moderator=True)
        ChatMembership.objects.create(chat=self.salon, user=self.modou, is_moderator=True)
        ChatMembership.objects.create(chat=self.salon, user=self.binta)
        self.message = Message.objects.create(chat=self.salon, sender=self.binta, content='Hors sujet')

    def test_un_moderateur_nomme_epingle(self):
        self.en(self.modou)
        r = self.client.post(f'/api/comms/{self.salon.id}/pin/', {'message_id': self.message.id}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    def test_un_moderateur_retire_le_message_d_un_membre(self):
        self.en(self.modou)
        self.assertEqual(self.client.delete(f'/api/comms/messages/{self.message.id}/').status_code, status.HTTP_200_OK)
        self.message.refresh_from_db()
        self.assertTrue(self.message.is_deleted)

    def test_un_simple_membre_ne_retire_pas_le_message_d_autrui(self):
        autre = Message.objects.create(chat=self.salon, sender=self.awa, content='Consigne')
        self.en(self.binta)
        self.assertEqual(self.client.delete(f'/api/comms/messages/{autre.id}/').status_code, status.HTTP_403_FORBIDDEN)


class FilArchiveTests(SalonModereTests):
    def setUp(self):
        super().setUp()
        self.salon.archived_at = timezone.now()
        self.salon.save(update_fields=['archived_at'])

    def test_un_fil_archive_est_en_lecture_seule(self):
        self.en(self.binta)
        url = f'/api/comms/messages/{self.message.id}/'
        self.assertEqual(self.client.patch(url, {'content': 'Réécrit'}, format='json').status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.post(f'{url}react/', {'emoji': '👍'}, format='json').status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_403_FORBIDDEN)

    # Hérités : dans un fil archivé, ces gestes sont refusés à tous sauf l'admin.
    def test_un_moderateur_nomme_epingle(self):
        self.en(self.modou)
        r = self.client.post(f'/api/comms/{self.salon.id}/pin/', {'message_id': self.message.id}, format='json')
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_un_moderateur_retire_le_message_d_un_membre(self):
        self.en(self.modou)
        self.assertEqual(self.client.delete(f'/api/comms/messages/{self.message.id}/').status_code, status.HTTP_403_FORBIDDEN)
