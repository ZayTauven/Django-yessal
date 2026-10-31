"""Supprimer un salon : l'administration seule, et rien d'autre qu'un salon.

Demande du 2026-10-31 : un salon que tous ses membres ont quitté n'est plus
visible que de l'admin (section « Archivés ») ; elle doit pouvoir le supprimer
au lieu de le « quitter ».
"""

import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from comms.models import Chat, ChatMembership, Message

User = get_user_model()
MEDIA_TEMP = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_TEMP)
class SuppressionSalonTests(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TEMP, ignore_errors=True)

    def setUp(self):
        def compte(email, role=User.Role.MEMBER):
            return User.objects.create_user(email=email, password='Motdepasse123!', role=role, status=User.Status.ACTIVE)

        self.admin = compte('admin@test.sn', User.Role.ADMIN)
        self.awa = compte('awa@test.sn')
        self.modou = compte('modou@test.sn')
        self.salon = Chat.objects.create(chat_type=Chat.ChatType.GROUP, name='Salon', created_by=self.awa)

    def url(self, chat):
        return f'/api/comms/{chat.id}/'

    def test_l_admin_supprime_un_salon_orphelin_et_ses_fichiers(self):
        message = Message.objects.create(chat=self.salon, sender=self.awa, content='Photo')
        message.file.save('photo.jpg', ContentFile(b'jpeg'), save=True)
        chemin = message.file.name
        self.assertTrue(default_storage.exists(chemin))

        self.client.force_authenticate(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.delete(self.url(self.salon))
        self.assertEqual(r.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Chat.objects.filter(pk=self.salon.pk).exists())
        self.assertFalse(Message.objects.filter(pk=message.pk).exists())
        self.assertFalse(default_storage.exists(chemin))

    def test_un_moderateur_ne_supprime_pas_le_salon(self):
        ChatMembership.objects.create(chat=self.salon, user=self.awa, is_moderator=True)
        self.client.force_authenticate(self.awa)
        self.assertEqual(self.client.delete(self.url(self.salon)).status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertTrue(Chat.objects.filter(pk=self.salon.pk).exists())

    def test_ni_canal_ni_tete_a_tete_meme_pour_l_admin(self):
        for chat_type in (Chat.ChatType.CHANNEL, Chat.ChatType.DIRECT):
            fil = Chat.objects.create(chat_type=chat_type, name='Fil')
            ChatMembership.objects.create(chat=fil, user=self.admin)
            self.client.force_authenticate(self.admin)
            self.assertEqual(self.client.delete(self.url(fil)).status_code, status.HTTP_405_METHOD_NOT_ALLOWED, chat_type)
            self.assertTrue(Chat.objects.filter(pk=fil.pk).exists())
