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
