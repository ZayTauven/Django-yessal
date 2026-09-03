"""Tests du module d'envoi de courriels.

Ce qui est vérifié ici n'est pas « le courriel part » — c'est surtout
« l'application survit quand il ne part pas ». Dans ce produit, l'absence de
destinataire est le cas COURANT, pas l'exception : une partie des membres est
inscrite par téléphone, sans adresse.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail as django_mail
from django.test import TestCase, override_settings

from core.mail import SUJETS, send_to_user, send_to_users

User = get_user_model()

# Le backend « locmem » range les messages dans django_mail.outbox au lieu de
# les envoyer. EMAIL_ENABLED doit être vrai, sinon le module coupe en amont.
COURRIEL_ACTIF = override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_ENABLED=True,
)


@COURRIEL_ACTIF
class EnvoiUnitaireTests(TestCase):
    def setUp(self):
        django_mail.outbox = []
        self.membre = User.objects.create_user(
            email='bineta@example.com',
            password='Test1234!',
            first_name='Bineta',
            role=User.Role.MEMBER,
        )

    def test_envoi_nominal(self):
        envoye = send_to_user(self.membre, 'mot_de_passe_oublie', {
            'lien_reinitialisation': 'https://exemple/reset?token=abc',
            'duree_validite': '24 heures',
        })

        self.assertTrue(envoye)
        self.assertEqual(len(django_mail.outbox), 1)
        message = django_mail.outbox[0]
        self.assertEqual(message.to, ['bineta@example.com'])
        self.assertEqual(message.subject, SUJETS['mot_de_passe_oublie'])

    def test_le_message_porte_une_version_html_et_une_version_texte(self):
        """Un courriel sans version texte est un signal de pourriel."""
        send_to_user(self.membre, 'mot_de_passe_oublie', {
            'lien_reinitialisation': 'https://exemple/reset?token=abc',
            'duree_validite': '24 heures',
        })

        message = django_mail.outbox[0]
        self.assertTrue(message.body.strip(), "la version texte est vide")
        types = [type_ for _contenu, type_ in message.alternatives]
        self.assertIn('text/html', types)

    def test_le_prenom_est_injecte_sans_le_passer(self):
        send_to_user(self.membre, 'mot_de_passe_oublie', {
            'lien_reinitialisation': 'https://exemple/reset',
            'duree_validite': '24 heures',
        })

        self.assertIn('Bineta', django_mail.outbox[0].body)

    def test_membre_sans_adresse_ne_fait_pas_echouer_l_appel(self):
        """Le cas courant : inscription par téléphone, sans e-mail."""
        sans_email = User.objects.create_user(
            phone='+221770000001', password='Test1234!', role=User.Role.MEMBER
        )

        envoye = send_to_user(sans_email, 'compte_valide', {})

        self.assertFalse(envoye)
        self.assertEqual(len(django_mail.outbox), 0)

    def test_un_gabarit_manquant_ne_leve_pas(self):
        """Un code sans gabarit ne doit pas faire tomber l'action métier.

        Confirmer un virement compte plus que notifier qu'il est confirmé.

        Le code est volontairement inventé, et non emprunté au catalogue : ce
        test s'appuyait sur `ndiguel_echeance` tant que son gabarit n'existait
        pas, et il est tombé le jour où celui-ci a été écrit. Un test ne doit
        pas dépendre de ce qui reste à faire.
        """
        envoye = send_to_user(self.membre, 'code_qui_nexistera_jamais', {})

        self.assertFalse(envoye)
        self.assertEqual(len(django_mail.outbox), 0)

    def test_un_serveur_smtp_muet_ne_leve_pas(self):
        with patch.object(
            django_mail.EmailMultiAlternatives, 'send', side_effect=OSError('smtp down')
        ):
            envoye = send_to_user(self.membre, 'mot_de_passe_oublie', {
                'lien_reinitialisation': 'x', 'duree_validite': 'y',
            })

        self.assertFalse(envoye)

    def test_variable_absente_du_sujet_ne_bloque_pas_l_envoi(self):
        """`ndiguel_responsable` attend {campagne} ; sans elle, on envoie quand même."""
        with patch('core.mail._rendu', return_value=('<p>x</p>', 'x')):
            envoye = send_to_user(self.membre, 'ndiguel_responsable', {})

        self.assertTrue(envoye)
        # Le gabarit brut, non formaté — imparfait, mais pas silencieux.
        self.assertIn('{campagne}', django_mail.outbox[0].subject)


@COURRIEL_ACTIF
class CoupeCircuitTests(TestCase):
    def setUp(self):
        django_mail.outbox = []
        self.membre = User.objects.create_user(
            email='test@example.com', password='Test1234!', role=User.Role.MEMBER
        )

    @override_settings(EMAIL_ENABLED=False)
    def test_email_enabled_false_bloque_tout_envoi(self):
        envoye = send_to_user(self.membre, 'mot_de_passe_oublie', {
            'lien_reinitialisation': 'x', 'duree_validite': 'y',
        })

        self.assertFalse(envoye)
        self.assertEqual(len(django_mail.outbox), 0)


@COURRIEL_ACTIF
class EnvoiDeMasseTests(TestCase):
    def setUp(self):
        django_mail.outbox = []
        self.avec = [
            User.objects.create_user(
                email=f'membre{i}@example.com', password='Test1234!',
                first_name=f'Membre{i}', role=User.Role.MEMBER,
            )
            for i in range(3)
        ]
        self.sans = User.objects.create_user(
            phone='+221770000002', password='Test1234!', role=User.Role.MEMBER
        )

    def test_seuls_les_membres_avec_adresse_sont_servis(self):
        import threading

        with patch('core.mail._rendu', return_value=('<p>x</p>', 'x')):
            send_to_users(self.avec + [self.sans], 'fete_date_modifiee',
                          contexte={'fete': 'Magal', 'date': '01/01/2027'})
            # L'envoi part dans un fil : on attend qu'il finisse avant de compter.
            for fil in threading.enumerate():
                if fil.name.startswith('mail-'):
                    fil.join(timeout=10)

        self.assertEqual(len(django_mail.outbox), 3)
        destinataires = sorted(m.to[0] for m in django_mail.outbox)
        self.assertEqual(
            destinataires,
            ['membre0@example.com', 'membre1@example.com', 'membre2@example.com'],
        )

    def test_aucun_destinataire_ne_declenche_aucun_fil(self):
        send_to_users([self.sans], 'fete_date_modifiee', contexte={})
        self.assertEqual(len(django_mail.outbox), 0)


class CatalogueTests(TestCase):
    """Le catalogue documenté et le module doivent rester alignés."""

    def test_tous_les_sujets_sont_non_vides(self):
        for code, sujet in SUJETS.items():
            self.assertTrue(sujet.strip(), f"sujet vide pour {code}")

    def test_les_codes_sont_en_minuscules_avec_underscores(self):
        import re

        for code in SUJETS:
            self.assertRegex(code, r'^[a-z][a-z0-9_]*$', f"code mal formé : {code}")
