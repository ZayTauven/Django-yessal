"""Zones de collecte, « Je m'en charge » et interrupteur de la collecte en espèces.

Décisions du 2026-10-28 : un collecteur ne voit que les demandes de ses zones
LDD ; un membre dont la zone n'est couverte par personne ne peut pas demander
de collecte ; l'administration peut suspendre la collecte (code par courriel +
confirmation), ce qui annule les demandes mais laisse l'argent encaissé à
remettre. Le mode collecteur (pour autrui) n'est pas borné aux zones.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import AuditLog, CodeVerification, Daara, LDD, PilotageSettings
from comms.models import Notification
from contributions.models import Donation
from events.models import Campaign, Fete

User = get_user_model()
CODE = 123456


class ZonesBase(APITestCase):
    def setUp(self):
        self.dakar = LDD.objects.create(code='DS S1', name='PIKINE')
        self.paris = LDD.objects.create(code='DS FR2', name='KARAMNA PARIS')
        self.daara_dakar = Daara.objects.create(name='Daara Pikine', ldd=self.dakar)
        self.daara_paris = Daara.objects.create(name='Daara Paris', ldd=self.paris)

        def compte(email, role, daara, **extra):
            return User.objects.create_user(
                email=email, password='Motdepasse123!', role=role,
                status=User.Status.ACTIVE, daara=daara, **extra,
            )

        self.admin = compte('admin@test.sn', User.Role.ADMIN, None, first_name='Awa')
        self.col_dakar = compte('c1@test.sn', User.Role.COLLECTOR, self.daara_dakar, first_name='Moussa')
        self.col_dakar2 = compte('c2@test.sn', User.Role.COLLECTOR, self.daara_dakar, first_name='Ibou')
        self.membre_dakar = compte('m1@test.sn', User.Role.MEMBER, self.daara_dakar, first_name='Fatou')
        self.membre_paris = compte('m2@test.sn', User.Role.MEMBER, self.daara_paris, first_name='Aïda')

        fete = Fete.objects.create(name='Magal', date=timezone.now().date(), recurrence=Fete.Recurrence.ANNUAL)
        self.campaign = Campaign.objects.create(
            name='Ndiguel', deadline=timezone.now().date(), fete=fete, created_by=self.admin,
        )
        self.list_url = reverse('donations-list')

    def demande(self, membre, montant=5000):
        return Donation.objects.create(
            campaign=self.campaign, donor=membre, amount=montant, payment_method='manual',
        )

    def creer(self, auteur, **payload):
        self.client.force_authenticate(auteur)
        corps = {'campaign': self.campaign.id, 'amount': 5000, 'payment_method': 'manual'}
        corps.update(payload)
        return self.client.post(self.list_url, corps, format='json')

    def url(self, d, suffixe):
        return f"{self.list_url}{d.pk}/{suffixe}/"


class NominationEtZonesTests(ZonesBase):
    def test_la_nomination_donne_la_zone_du_daara(self):
        self.assertEqual(list(self.col_dakar.collecte_zones.all()), [self.dakar])
        self.membre_paris.role = User.Role.COLLECTOR
        self.membre_paris.save(update_fields=['role'])
        self.assertEqual(list(self.membre_paris.collecte_zones.all()), [self.paris])

    def test_une_affectation_manuelle_n_est_jamais_ecrasee(self):
        self.col_dakar.collecte_zones.set([self.paris])
        self.col_dakar.save()
        self.assertEqual(list(self.col_dakar.collecte_zones.all()), [self.paris])

    def test_le_collecteur_ne_voit_que_sa_zone(self):
        ici, la_bas = self.demande(self.membre_dakar), self.demande(self.membre_paris)
        self.client.force_authenticate(self.col_dakar)
        ids = [r['id'] for r in self.client.get(self.list_url, {'a_collecter': 1}).data]
        self.assertEqual(ids, [ici.pk])
        self.assertEqual(self.client.post(self.url(la_bas, 'collect')).status_code, 404)

    def test_pas_de_demande_hors_zone(self):
        self.client.force_authenticate(self.membre_paris)
        self.assertEqual(
            self.client.get(f"{self.list_url}collecte/").data,
            {'active': True, 'disponible': False, 'raison': 'hors_zone'},
        )
        self.assertEqual(self.creer(self.membre_paris).status_code, 400)
        self.assertEqual(self.creer(self.membre_dakar).status_code, 201)

    def test_le_mode_collecteur_n_est_pas_borne_aux_zones(self):
        r = self.creer(self.col_dakar, donor=self.membre_paris.id)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Donation.objects.get(pk=r.data['id']).payment_status, 'collected')

    def test_la_demande_previent_la_zone_pas_les_admins(self):
        self.creer(self.membre_dakar)
        prevenus = set(Notification.objects.filter(title__icontains='récolter').values_list('user', flat=True))
        self.assertEqual(prevenus, {self.col_dakar.pk, self.col_dakar2.pk})


class PriseEnChargeTests(ZonesBase):
    def test_je_m_en_charge_puis_j_encaisse(self):
        d = self.demande(self.membre_dakar)
        self.client.force_authenticate(self.col_dakar)
        self.assertEqual(self.client.post(self.url(d, 'claim')).status_code, 200)
        d.refresh_from_db()
        self.assertEqual((d.collector, d.payment_status), (self.col_dakar, 'pending'))
        self.assertTrue(Notification.objects.filter(user=self.membre_dakar, title='Un collecteur arrive').exists())

        # Le collègue la voit prise, et ne peut ni la prendre ni l'encaisser.
        self.client.force_authenticate(self.col_dakar2)
        lignes = self.client.get(self.list_url, {'a_collecter': 1}).data
        self.assertEqual(lignes[0]['collector'], self.col_dakar.pk)
        self.assertEqual(self.client.post(self.url(d, 'claim')).status_code, 409)
        self.assertEqual(self.client.post(self.url(d, 'collect')).status_code, 409)

        self.client.force_authenticate(self.col_dakar)
        self.assertEqual(self.client.post(self.url(d, 'collect')).status_code, 200)

    def test_je_ne_peux_plus(self):
        d = self.demande(self.membre_dakar)
        self.client.force_authenticate(self.col_dakar)
        self.client.post(self.url(d, 'claim'))
        self.client.force_authenticate(self.col_dakar2)
        self.assertEqual(self.client.post(self.url(d, 'release')).status_code, 403)
        self.client.force_authenticate(self.col_dakar)
        self.assertEqual(self.client.post(self.url(d, 'release')).status_code, 200)
        d.refresh_from_db()
        self.assertIsNone(d.collector)


@patch('accounts.services.code_verification.secrets.randbelow', return_value=CODE)
class InterrupteurTests(ZonesBase):
    reglages = '/api/settings/pilotage/'

    def suspendre(self, code=CODE, confirme=True):
        self.client.force_authenticate(self.admin)
        self.client.post(f"{self.reglages}collecte/code/", {'active': False}, format='json')
        return self.client.post(
            f"{self.reglages}collecte/",
            {'active': False, 'code': str(code), 'confirme': confirme}, format='json',
        )

    def test_mauvais_code_puis_sans_confirmation(self, _):
        self.assertEqual(self.suspendre(code=111111).status_code, 400)
        self.assertEqual(CodeVerification.objects.get(utilise_le__isnull=True).tentatives, 1)
        self.assertEqual(self.suspendre(confirme=False).status_code, 400)
        self.assertTrue(PilotageSettings.load().collecte_physique_active)

    def test_suspension(self, _):
        demande = self.demande(self.membre_dakar)
        encaisse = Donation.objects.create(
            campaign=self.campaign, donor=self.membre_paris, amount=3000, payment_method='manual',
            payment_status='collected', collector=self.col_dakar, collected_at=timezone.now(),
        )
        r = self.suspendre()
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['demandes_annulees'], 1)
        self.assertFalse(PilotageSettings.load().collecte_physique_active)

        demande.refresh_from_db()
        encaisse.refresh_from_db()
        self.assertEqual(demande.payment_status, 'cancelled')
        self.assertEqual(encaisse.payment_status, 'collected')  # reste à remettre
        self.assertEqual(
            Notification.objects.filter(title='Collecte en espèces suspendue').count(),
            User.objects.filter(status=User.Status.ACTIVE).count(),
        )
        self.assertTrue(AuditLog.objects.filter(action='collecte_suspendue').exists())

        # Plus de demande, plus d'encaissement… mais la remise reste ouverte.
        self.assertEqual(self.creer(self.membre_dakar).status_code, 400)
        self.assertEqual(self.creer(self.col_dakar, donor=self.membre_dakar.id).status_code, 400)
        self.client.force_authenticate(self.admin)
        r = self.client.post(f"{self.list_url}admin/confirm-cash/", {
            'collector': self.col_dakar.pk, 'donations': [encaisse.pk], 'counted_amount': 3000,
        }, format='json')
        self.assertEqual(r.status_code, 201)

    def test_le_put_general_ne_bascule_pas(self, _):
        self.client.force_authenticate(self.admin)
        self.client.post(self.reglages, {'collecte_physique_active': False}, format='json')
        self.assertTrue(PilotageSettings.load().collecte_physique_active)

    def test_reserve_aux_admins(self, _):
        self.client.force_authenticate(self.col_dakar)
        r = self.client.post(f"{self.reglages}collecte/code/", {'active': False}, format='json')
        self.assertEqual(r.status_code, 403)


class GestionCollecteursTests(ZonesBase):
    def test_liste_zones_et_couverture(self):
        self.client.force_authenticate(self.admin)
        liste = self.client.get('/api/collecteurs/').data
        self.assertEqual({c['id'] for c in liste}, {self.col_dakar.pk, self.col_dakar2.pk})

        r = self.client.post(
            f'/api/collecteurs/{self.col_dakar.pk}/zones/',
            {'zones': [self.dakar.pk, self.paris.pk]}, format='json',
        )
        self.assertEqual(r.status_code, 200)
        couverture = {z['code']: z['collecteurs'] for z in self.client.get('/api/collecteurs/couverture/').data}
        self.assertEqual(couverture['DS FR2'], 1)
        self.assertEqual(couverture['DS S1'], 2)

        # Paris est désormais couvert : Aïda peut demander une collecte.
        self.assertEqual(self.creer(self.membre_paris).status_code, 201)
