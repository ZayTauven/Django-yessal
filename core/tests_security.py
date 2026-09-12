"""Tests de non-régression sur les garanties de sécurité.

Chacun de ces tests correspond à un défaut constaté en audit. Ils ne vérifient
pas un comportement métier : ils vérifient qu'une protection est en place, et
qu'elle ne repartira pas au premier remaniement.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import LDD, Daara
from contributions.models import Donation
from events.models import Campaign, Fete

User = get_user_model()


class WebhookBictorysAuthTests(APITestCase):
    """Le webhook d'encaissement doit refuser par défaut.

    L'authentification s'écrivait « si un secret est configuré, le vérifier ».
    Comme `BICTORYS_WEBHOOK_SECRET` n'était lu nulle part dans settings.py, le
    contrôle ne s'exécutait jamais : un POST anonyme suffisait à faire passer
    un don à « confirmé ».
    """

    def setUp(self):
        self.url = reverse('bictorys-webhook')
        self.donor = User.objects.create_user(
            email='donor@test.com',
            password='Donor123!',
            role=User.Role.MEMBER,
            status=User.Status.ACTIVE,
        )
        self.fete = Fete.objects.create(name='Magal', date='2026-01-01')
        self.campaign = Campaign.objects.create(
            name='Ndiguel Magal',
            fete=self.fete,
            goal_amount=100000,
            deadline='2026-12-31',
        )
        self.donation = Donation.objects.create(
            donor=self.donor,
            campaign=self.campaign,
            amount=5000,
            external_ref='don_test_ref',
            payment_status=Donation.PaymentStatus.PENDING,
        )

    def _payload(self):
        return {
            'id': 'bict_1',
            'status': 'succeeded',
            'amount': 5000,
            'paymentReference': 'don_test_ref',
        }

    @override_settings(BICTORYS_WEBHOOK_SECRET='')
    def test_refuse_quand_aucun_secret_configure(self):
        """Sans secret côté serveur, l'appel est rejeté — pas accepté."""
        res = self.client.post(self.url, self._payload(), format='json')

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.donation.refresh_from_db()
        self.assertEqual(self.donation.payment_status, Donation.PaymentStatus.PENDING)

    @override_settings(BICTORYS_WEBHOOK_SECRET='le-bon-secret')
    def test_refuse_un_secret_errone(self):
        res = self.client.post(
            self.url, self._payload(), format='json', HTTP_X_SECRET_KEY='mauvais'
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.donation.refresh_from_db()
        self.assertEqual(self.donation.payment_status, Donation.PaymentStatus.PENDING)

    @override_settings(BICTORYS_WEBHOOK_SECRET='le-bon-secret')
    def test_accepte_et_confirme_avec_le_bon_secret(self):
        res = self.client.post(
            self.url,
            self._payload(),
            format='json',
            HTTP_X_SECRET_KEY='le-bon-secret',
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.donation.refresh_from_db()
        self.assertEqual(self.donation.payment_status, Donation.PaymentStatus.CONFIRMED)

    @override_settings(BICTORYS_WEBHOOK_SECRET='le-bon-secret')
    def test_montant_illisible_ne_provoque_pas_une_500(self):
        """Un champ non numérique venait faire lever int() — donc une 500,
        que le prestataire aurait réessayée en boucle."""
        payload = self._payload()
        payload['amount'] = 'beaucoup'

        res = self.client.post(
            self.url, payload, format='json', HTTP_X_SECRET_KEY='le-bon-secret'
        )

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


class PermissionParDefautTests(APITestCase):
    """DRF doit refuser l'anonyme quand une vue ne dit rien.

    Sans `DEFAULT_PERMISSION_CLASSES`, DRF retombe sur `AllowAny` : toute vue
    écrite sans `permission_classes` est publique en silence.
    """

    def test_endpoint_authentifie_rejette_l_anonyme(self):
        res = self.client.get(reverse('users-list'))
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_le_defaut_est_bien_is_authenticated(self):
        from django.conf import settings

        self.assertIn(
            'rest_framework.permissions.IsAuthenticated',
            settings.REST_FRAMEWORK['DEFAULT_PERMISSION_CLASSES'],
        )


class DaaraPublicTests(APITestCase):
    """La liste des Daaras reste ouverte, mais réduite.

    Le formulaire d'inscription en a besoin sans compte. Elle répondait
    toutefois avec le sérialiseur complet : nom du chef, collecteurs
    nominatifs, effectifs — un annuaire interne aspirable par un curl.
    """

    def setUp(self):
        self.ldd = LDD.objects.create(code='DS1', name='Zone Test')
        self.chef = User.objects.create_user(
            email='chef@test.com',
            password='Chef123!',
            first_name='Bineta',
            last_name='Sow',
            role=User.Role.CHEF_DAARA,
            status=User.Status.ACTIVE,
        )
        self.daara = Daara.objects.create(
            name='Daara Test', ldd=self.ldd, chef=self.chef
        )

    def test_anonyme_peut_lister_pour_l_inscription(self):
        res = self.client.get(reverse('daara-list'))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(len(res.data) >= 1)

    def test_anonyme_ne_voit_ni_chef_ni_collecteurs(self):
        res = self.client.get(reverse('daara-list'))
        entry = res.data[0]

        self.assertEqual(set(entry.keys()), {'id', 'name', 'ldd'})
        for fuite in ('chef', 'chef_full_name', 'collectors', 'members_count'):
            self.assertNotIn(fuite, entry)

    def test_un_membre_authentifie_garde_la_vue_complete(self):
        self.client.force_authenticate(self.chef)
        res = self.client.get(reverse('daara-list'))

        self.assertIn('chef_full_name', res.data[0])


class RoleAdminEtIsStaffTests(TestCase):
    """`role='admin'` et `is_staff` doivent dire la même chose.

    Le produit raisonne en rôles, `permissions.IsAdminUser` ne regarde que
    `is_staff`. Un administrateur nommé depuis l'interface serait passé par le
    garde du front (qui lit le rôle) puis se serait heurté à un 403 sur chaque
    appel : un administrateur sans aucun pouvoir.
    """

    def test_nommer_un_admin_le_rend_staff(self):
        user = User.objects.create_user(
            email='promu@test.com', password='Promu123!', role=User.Role.MEMBER
        )
        self.assertFalse(user.is_staff)

        user.role = User.Role.ADMIN
        user.save()

        user.refresh_from_db()
        self.assertTrue(user.is_staff)

    def test_la_synchro_survit_a_un_update_fields_restreint(self):
        """`update_fields=['role']` aurait écarté `is_staff` de l'écriture."""
        user = User.objects.create_user(
            email='promu2@test.com', password='Promu123!', role=User.Role.MEMBER
        )

        user.role = User.Role.ADMIN
        user.save(update_fields=['role'])

        user.refresh_from_db()
        self.assertTrue(user.is_staff)

    def test_un_membre_ordinaire_ne_devient_pas_staff(self):
        user = User.objects.create_user(
            email='simple@test.com', password='Simple123!', role=User.Role.MEMBER
        )
        user.save()

        user.refresh_from_db()
        self.assertFalse(user.is_staff)


class PieceJointeConversationTests(APITestCase):
    """Une pièce jointe ne doit pas pouvoir être une page exécutable.

    `comms.Message.file` était le seul `FileField` nu du produit : tous les
    autres champs de fichier sont des `ImageField`, que Pillow protège en
    refusant d'ouvrir ce qui n'est pas une image. Celui-ci ne vérifiait que la
    TAILLE.

    Constaté le 2026-09-12 depuis un compte membre ordinaire : l'envoi d'un
    fichier `.html` était accepté (201), puis servi en `Content-Type:
    text/html` avec `Content-Disposition: inline` depuis le domaine de l'API —
    soit une page arbitraire hébergée sur le domaine HTTPS de l'organisation
    et partageable depuis une conversation.

    Ces tests rejouent exactement cet envoi. Le premier échouerait de nouveau
    si la liste blanche disparaissait du modèle, ou si un remaniement du
    sérialiseur cessait de reporter les validateurs du champ.
    """

    def setUp(self):
        from comms.models import Chat, ChatMembership

        ldd = LDD.objects.create(code='PJ', name='LDD pièces jointes')
        daara = Daara.objects.create(name='Daara pièces jointes', ldd=ldd)
        self.membre = User.objects.create_user(
            email='piece.jointe@test.com', password='Piece123!',
            role=User.Role.MEMBER, daara=daara,
        )
        self.chat = Chat.objects.create(chat_type='group', name='Salon de test',
                                        created_by=self.membre)
        ChatMembership.objects.create(chat=self.chat, user=self.membre)
        self.client.force_authenticate(user=self.membre)

    def _envoyer(self, nom, contenu=b'peu importe'):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return self.client.post(
            '/api/comms/messages/',
            {
                'chat': self.chat.id,
                'content': 'test',
                'file': SimpleUploadedFile(nom, contenu),
            },
            format='multipart',
        )

    def test_une_page_html_est_refusee(self):
        reponse = self._envoyer('piege.html', b'<script>alert(1)</script>')
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('file', reponse.data)

    def test_un_svg_est_refuse(self):
        """Le SVG porte du script, et les navigateurs l'exécutent."""
        self.assertEqual(
            self._envoyer('piege.svg', b'<svg xmlns="http://www.w3.org/2000/svg"/>').status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_un_javascript_est_refuse(self):
        self.assertEqual(
            self._envoyer('piege.js', b'alert(1)').status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_l_extension_seule_compte_pas_le_type_annonce(self):
        """Renommer ne doit pas suffire à faire passer une page.

        L'inverse est le vrai risque : un `.html` déclaré `image/png` par le
        client. Le contrôle porte sur le NOM, précisément parce que c'est le
        nom — et non l'en-tête envoyé par le client — qui décidera du
        Content-Type au moment de servir le fichier.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile

        reponse = self.client.post(
            '/api/comms/messages/',
            {
                'chat': self.chat.id,
                'content': 'test',
                'file': SimpleUploadedFile('piege.html', b'<h1>x</h1>',
                                           content_type='image/png'),
            },
            format='multipart',
        )
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_les_pieces_jointes_legitimes_passent(self):
        """Une liste blanche trop serrée casserait l'usage : on le vérifie."""
        for nom in ('rapport.pdf', 'photo.png', 'recu.jpg', 'tableau.xlsx',
                    'note.txt', 'vocal.m4a'):
            with self.subTest(fichier=nom):
                reponse = self._envoyer(nom)
                self.assertEqual(
                    reponse.status_code, status.HTTP_201_CREATED,
                    f"{nom} devrait être accepté : {reponse.data}",
                )


class DefautsQuiSeFermentTests(TestCase):
    """Une variable d'environnement ABSENTE doit mener au comportement sûr.

    Le 12 septembre 2026, la pile de démonstration tournait sur un domaine
    public avec `DEBUG=True` et `ALLOWED_HOSTS=['*']` — non par décision, mais
    parce que la ligne `DEBUG` avait disparu du fichier d'environnement lors
    d'une édition à la main, et que le défaut valait `'true'`. Rien ne l'a
    signalé : l'application répondait 200 partout.

    Le même fichier portait une `SECRET_KEY` de onze caractères, que le garde
    laissait passer parce qu'il ne comparait qu'à la clé de développement.
    """

    def test_une_valeur_de_debug_absente_ou_illisible_ferme(self):
        from core.settings import _coerce_debug

        for valeur in ('', None, 'peut-etre', 'oui', '2'):
            with self.subTest(valeur=valeur):
                self.assertFalse(
                    _coerce_debug(valeur),
                    f"{valeur!r} ne doit pas activer DEBUG",
                )

    def test_les_valeurs_explicites_restent_comprises(self):
        from core.settings import _coerce_debug

        for valeur in ('1', 'true', 'True', 'yes', 'on', 'dev', True):
            self.assertTrue(_coerce_debug(valeur), f"{valeur!r} doit activer DEBUG")
        for valeur in ('0', 'false', 'no', 'off', 'prod', 'production', False):
            self.assertFalse(_coerce_debug(valeur), f"{valeur!r} ne doit pas activer DEBUG")

    def test_le_defaut_de_debug_est_ferme(self):
        """Le défaut lui-même, pas seulement la fonction qui le lit.

        C'est ce test qui aurait échoué avant le 12 septembre 2026 : le défaut
        valait `'true'`, donc un fichier d'environnement sans ligne `DEBUG`
        démarrait l'application en mode débogage.
        """
        from core.settings import DEBUG_PAR_DEFAUT, _coerce_debug

        self.assertFalse(
            _coerce_debug(DEBUG_PAR_DEFAUT),
            "DEBUG doit être faux quand la variable n'est pas renseignée",
        )

    def test_une_cle_trop_courte_empeche_le_demarrage(self):
        from django.core.exceptions import ImproperlyConfigured

        from core.settings import _verifier_secret_key

        with self.assertRaises(ImproperlyConfigured) as ctx:
            _verifier_secret_key('x' * 11, debug=False)
        self.assertIn('11', str(ctx.exception))

    def test_la_cle_de_developpement_empeche_le_demarrage(self):
        from django.core.exceptions import ImproperlyConfigured

        from core.settings import DEV_SECRET_KEY, _verifier_secret_key

        with self.assertRaises(ImproperlyConfigured):
            _verifier_secret_key(DEV_SECRET_KEY, debug=False)

    def test_une_cle_correcte_passe(self):
        from core.settings import _verifier_secret_key

        _verifier_secret_key('a' * 50, debug=False)  # ne doit rien lever

    def test_en_developpement_le_garde_ne_gene_pas(self):
        """Un développeur ne doit pas avoir à générer une clé pour démarrer."""
        from core.settings import DEV_SECRET_KEY, _verifier_secret_key

        _verifier_secret_key(DEV_SECRET_KEY, debug=True)
        _verifier_secret_key('court', debug=True)
