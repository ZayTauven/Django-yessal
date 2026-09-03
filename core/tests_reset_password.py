"""Parcours « mot de passe oublié » — les deux endpoints, et leurs refus.

La vue de demande était une coquille vide : elle répondait « un email a été
envoyé » sans rien envoyer, et l'endpoint qui aurait reçu le lien n'existait
pas. Ces tests couvrent le parcours reconstruit, et surtout ses refus — un
parcours de réinitialisation se juge à ce qu'il REFUSE.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail as django_mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()

SANS_LIMITE = override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_ENABLED=True,
    # Les quotas de débit fausseraient les tests : plusieurs appels successifs
    # au même endpoint sont ici la norme, pas un abus.
    REST_FRAMEWORK={
        'DEFAULT_AUTHENTICATION_CLASSES': (
            'accounts.authentication.VersionedJWTAuthentication',
        ),
        'DEFAULT_PERMISSION_CLASSES': (
            'rest_framework.permissions.IsAuthenticated',
        ),
    },
)


@SANS_LIMITE
class DemandeDeReinitialisationTests(APITestCase):
    def setUp(self):
        django_mail.outbox = []
        self.url = reverse('forgot_password')
        self.membre = User.objects.create_user(
            email='bineta@example.com',
            password='AncienMotDePasse2026!',
            first_name='Bineta',
            role=User.Role.MEMBER,
            status=User.Status.ACTIVE,
        )

    def test_un_lien_part_quand_le_compte_existe(self):
        res = self.client.post(self.url, {'email': 'bineta@example.com'}, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertIn('reset-password', django_mail.outbox[0].body)

    def test_la_reponse_ne_trahit_pas_l_existence_du_compte(self):
        """Sinon l'endpoint devient un oracle : on y teste des adresses.

        Sur une plateforme d'appartenance religieuse, savoir qui est inscrit
        n'est pas une information anodine.
        """
        connu = self.client.post(self.url, {'email': 'bineta@example.com'}, format='json')
        inconnu = self.client.post(self.url, {'email': 'personne@example.com'}, format='json')

        self.assertEqual(connu.status_code, inconnu.status_code)
        self.assertEqual(connu.json(), inconnu.json())
        # Un seul courriel : celui du compte qui existe.
        self.assertEqual(len(django_mail.outbox), 1)

    def test_aucun_courriel_pour_un_compte_desactive(self):
        self.membre.is_active = False
        self.membre.save(update_fields=['is_active'])

        res = self.client.post(self.url, {'email': 'bineta@example.com'}, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(django_mail.outbox), 0)

    def test_adresse_absente_refusee(self):
        res = self.client.post(self.url, {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


@SANS_LIMITE
class ReinitialisationTests(APITestCase):
    NOUVEAU = 'YessalGui2027!'

    def setUp(self):
        django_mail.outbox = []
        self.url = reverse('reset_password')
        self.membre = User.objects.create_user(
            email='bineta@example.com',
            password='AncienMotDePasse2026!',
            first_name='Bineta',
            role=User.Role.MEMBER,
            status=User.Status.ACTIVE,
        )
        self.uid = urlsafe_base64_encode(force_bytes(self.membre.pk))
        self.jeton = default_token_generator.make_token(self.membre)

    def _poser(self, **surcharges):
        corps = {'uid': self.uid, 'token': self.jeton, 'new_password': self.NOUVEAU}
        corps.update(surcharges)
        return self.client.post(self.url, corps, format='json')

    def test_le_mot_de_passe_est_remplace(self):
        res = self._poser()

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.membre.refresh_from_db()
        self.assertTrue(self.membre.check_password(self.NOUVEAU))

    def test_toutes_les_sessions_sont_fermees(self):
        """On réinitialise souvent parce qu'on soupçonne un accès non désiré.

        Laisser les sessions ouvertes viderait la démarche de son sens.
        """
        version_avant = self.membre.token_version

        self._poser()

        self.membre.refresh_from_db()
        self.assertEqual(self.membre.token_version, version_avant + 1)

    def test_le_drapeau_de_mot_de_passe_impose_s_eteint(self):
        self.membre.must_change_password = True
        self.membre.save(update_fields=['must_change_password'])

        self._poser()

        self.membre.refresh_from_db()
        self.assertFalse(self.membre.must_change_password)

    def test_un_jeton_ne_sert_qu_une_fois(self):
        self.assertEqual(self._poser().status_code, status.HTTP_200_OK)

        deuxieme = self._poser(new_password='EncoreUnAutre2027!')

        self.assertEqual(deuxieme.status_code, status.HTTP_400_BAD_REQUEST)
        self.membre.refresh_from_db()
        self.assertTrue(self.membre.check_password(self.NOUVEAU))

    def test_jeton_invalide_refuse(self):
        res = self._poser(token='jeton-fabrique')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.membre.refresh_from_db()
        self.assertFalse(self.membre.check_password(self.NOUVEAU))

    def test_identifiant_malforme_indistinguable_d_un_jeton_perime(self):
        """Un `uid` illisible ne doit pas se distinguer d'un jeton expiré."""
        malforme = self._poser(uid='pas-du-base64!!')
        perime = self._poser(token='jeton-fabrique')

        self.assertEqual(malforme.status_code, perime.status_code)
        self.assertEqual(malforme.json(), perime.json())

    def test_mot_de_passe_faible_refuse_avec_son_motif(self):
        res = self._poser(new_password='1234')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        # Le motif de Django, traduit — pas un « invalide » sec.
        self.assertIn('caractères', res.json()['detail'])

    def test_champs_manquants_refuses(self):
        res = self.client.post(self.url, {'uid': self.uid}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_un_courriel_de_securite_confirme_le_changement(self):
        self._poser()

        self.assertEqual(len(django_mail.outbox), 1)
        self.assertIn('mot de passe', django_mail.outbox[0].subject.lower())
