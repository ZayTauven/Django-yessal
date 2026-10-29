from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from comms.models import FCMToken
from comms.services import ANDROID_CHANNEL_ID, send_fcm_push

User = get_user_model()


class EnvoiFcmTests(TestCase):
    """Ce que Django confie réellement à Firebase.

    Aucun test ne couvrait `send_fcm_push` : un push vérifié sur l'émulateur le
    2026-10-29 arrivait bien, mais dans le canal de repli d'expo-notifications
    — le message ne disait pas quel canal Android employer.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='m@ex.org', password='x')
        FCMToken.objects.create(user=self.user, token='jeton-android', device_type='android')

    def envoyer(self):
        envoyes = []

        def capture(message):
            envoyes.append(message)
            return SimpleNamespace(responses=[SimpleNamespace(success=True, exception=None)])

        # `_apps` non vide : le module croit Firebase déjà initialisé et ne
        # cherche pas de clé de service.
        with mock.patch('firebase_admin._apps', {'[DEFAULT]': object()}), \
                mock.patch('firebase_admin.messaging.send_each_for_multicast', side_effect=capture):
            send_fcm_push(self.user.pk, 'Titre', 'Corps', data={'url': '/dashboard/notifications'})
        self.assertEqual(len(envoyes), 1)
        return envoyes[0]

    def test_le_push_vise_le_canal_android_de_l_application(self):
        message = self.envoyer()
        self.assertEqual(message.android.notification.channel_id, ANDROID_CHANNEL_ID)
        self.assertEqual(ANDROID_CHANNEL_ID, 'default')

    def test_titre_corps_et_donnees_sont_transmis(self):
        message = self.envoyer()
        self.assertEqual(message.notification.title, 'Titre')
        self.assertEqual(message.notification.body, 'Corps')
        self.assertEqual(message.data, {'url': '/dashboard/notifications'})
        self.assertEqual(message.tokens, ['jeton-android'])
