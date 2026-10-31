"""Audit des modules du 2026-10-31, lot « argent ».

Chaque test ferme un chemin éprouvé ouvert ce jour-là :

  · une demande de collecte payée en ligne restait « espèces, en attente »,
    donc dans la file des collecteurs — double encaissement possible ;
  · le webhook Bictorys repassait « confirmé » un Jëf annulé par l'admin ;
  · un collecteur encaissait son propre Jëf (`claim` le refusait, pas `collect`) ;
  · un montant compté « Infinity » faisait tomber la remise en 500.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import LDD, Daara
from comms.models import Notification
from contributions.models import Donation
from events.models import Campaign

User = get_user_model()

BICTORYS = dict(
    BICTORYS_ENABLED=True, BICTORYS_BASE_URL='http://bictorys.test',
    BICTORYS_PUBLIC_KEY='cle', BASE_URL='http://front.test', BICTORYS_WEBHOOK_SECRET='s3cret',
)


def reponse_bictorys():
    reponse = mock.Mock()
    reponse.json.return_value = {'id': 'bx1', 'checkoutUrl': 'http://pay.test/x'}
    reponse.raise_for_status.return_value = None
    return reponse


class AuditArgentBase(APITestCase):
    def setUp(self):
        self.ldd = LDD.objects.create(code='DS S10', name='DIOURBEL RABBI')
        self.daara = Daara.objects.create(name='Daara Diourbel', ldd=self.ldd)

        def compte(email, role):
            return User.objects.create_user(
                email=email, password='Motdepasse123!', role=role,
                status=User.Status.ACTIVE, daara=self.daara,
                first_name=email.split('@')[0].capitalize(), last_name='Test',
            )

        self.compte = compte
        self.admin = compte('admin@test.sn', User.Role.ADMIN)
        self.member = compte('membre@test.sn', User.Role.MEMBER)
        self.collector = compte('collecteur@test.sn', User.Role.COLLECTOR)
        self.campaign = Campaign.objects.create(
            name='Ndiguel Magal', deadline=timezone.localdate() + timezone.timedelta(days=10),
            status=Campaign.Status.ACTIVE, created_by=self.admin,
        )

    def demande_prise(self):
        """Une demande de collecte que le collecteur a prise (« Je m'en charge »)."""
        return Donation.objects.create(
            campaign=self.campaign, donor=self.member, amount=5000,
            payment_method=Donation.PaymentMethod.MANUAL, payment_status=Donation.PaymentStatus.PENDING,
            collector=self.collector, claimed_at=timezone.now(),
        )

    def file_du_collecteur(self):
        self.client.force_authenticate(self.collector)
        data = self.client.get('/api/contributions/?a_collecter=1').data
        return [d['id'] for d in (data['results'] if isinstance(data, dict) else data)]


@override_settings(**BICTORYS)
class DemandePayeeEnLigneTests(AuditArgentBase):
    def test_payer_par_wave_sort_la_demande_de_la_file(self):
        don = self.demande_prise()
        self.client.force_authenticate(self.member)
        with mock.patch('contributions.services.bictorys.requests.post', return_value=reponse_bictorys()) as post:
            r = self.client.post(f'/api/contributions/{don.id}/pay/', {'payment_method': 'wave'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        don.refresh_from_db()
        self.assertEqual(don.payment_method, Donation.PaymentMethod.WAVE)
        self.assertIsNone(don.collector_id)
        self.assertIsNone(don.claimed_at)
        self.assertNotIn(don.id, self.file_du_collecteur())
        # La demande de validation Wave part sur le téléphone du MEMBRE, pas du collecteur.
        self.assertEqual(post.call_args.kwargs['json']['customer']['email'], self.member.email)
        self.assertTrue(Notification.objects.filter(user=self.collector, title__icontains='réglé en ligne').exists())

    def test_carte_heritee_s_enregistre_bictorys(self):
        don = self.demande_prise()
        self.client.force_authenticate(self.member)
        with mock.patch('contributions.services.bictorys.requests.post', return_value=reponse_bictorys()):
            self.client.post(f'/api/contributions/{don.id}/pay/', {'payment_method': 'visa'}, format='json')
        don.refresh_from_db()
        self.assertEqual(don.payment_method, Donation.PaymentMethod.BICTORYS)

    def test_echec_de_la_passerelle_laisse_la_demande_intacte(self):
        import requests

        don = self.demande_prise()
        self.client.force_authenticate(self.member)
        with mock.patch('contributions.services.bictorys.requests.post', side_effect=requests.ConnectionError('x')):
            r = self.client.post(f'/api/contributions/{don.id}/pay/', {'payment_method': 'wave'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('indisponible', r.data['error'])
        don.refresh_from_db()
        self.assertEqual(don.payment_method, Donation.PaymentMethod.MANUAL)
        self.assertEqual(don.collector_id, self.collector.id)

    def test_virement_libere_aussi_le_collecteur(self):
        don = self.demande_prise()
        self.client.force_authenticate(self.member)
        r = self.client.post(f'/api/contributions/{don.id}/pay/',
                             {'payment_method': 'virement', 'wire_reference': 'VIR-1'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        don.refresh_from_db()
        self.assertEqual(don.payment_status, Donation.PaymentStatus.PENDING_WIRE)
        self.assertIsNone(don.collector_id)

    def test_collecteur_qui_paie_pour_autrui_reste_le_payeur(self):
        # Hors demande de collecte, rien ne change : le collecteur règle par
        # Wave au nom du membre, et c'est lui qui valide sur son téléphone.
        don = Donation.objects.create(
            campaign=self.campaign, donor=self.member, amount=5000,
            payment_method=Donation.PaymentMethod.WAVE, collector=self.collector,
        )
        self.client.force_authenticate(self.collector)
        with mock.patch('contributions.services.bictorys.requests.post', return_value=reponse_bictorys()) as post:
            self.client.post(f'/api/contributions/{don.id}/pay/', {'payment_method': 'wave'}, format='json')
        don.refresh_from_db()
        self.assertEqual(don.collector_id, self.collector.id)
        self.assertEqual(post.call_args.kwargs['json']['customer']['email'], self.collector.email)


@override_settings(BICTORYS_WEBHOOK_SECRET='s3cret')
class WebhookTests(AuditArgentBase):
    def appel(self, statut, montant=5000):
        return self.client.post(
            '/api/contributions/webhook/bictorys/',
            data=f'{{"id":"bx1","status":"{statut}","amount":{montant},"paymentReference":"don_ref"}}',
            content_type='application/json', HTTP_X_SECRET_KEY='s3cret',
        )

    def jef(self, statut):
        return Donation.objects.create(
            campaign=self.campaign, donor=self.member, amount=5000,
            payment_method=Donation.PaymentMethod.WAVE, payment_status=statut, external_ref='don_ref',
        )

    def test_un_jef_annule_reste_annule_et_l_admin_est_prevenu(self):
        don = self.jef(Donation.PaymentStatus.CANCELLED)
        self.assertEqual(self.appel('succeeded').status_code, 200)
        don.refresh_from_db()
        self.assertEqual(don.payment_status, Donation.PaymentStatus.CANCELLED)
        self.assertTrue(Notification.objects.filter(user=self.admin, title='Paiement reçu pour un Jëf annulé').exists())

    def test_un_echec_tardif_n_ecrase_pas_une_annulation(self):
        don = self.jef(Donation.PaymentStatus.CANCELLED)
        self.appel('failed')
        don.refresh_from_db()
        self.assertEqual(don.payment_status, Donation.PaymentStatus.CANCELLED)

    def test_la_confirmation_est_datee(self):
        don = self.jef(Donation.PaymentStatus.PENDING)
        self.appel('succeeded')
        don.refresh_from_db()
        self.assertEqual(don.payment_status, Donation.PaymentStatus.CONFIRMED)
        self.assertIsNotNone(don.validated_at)

    def test_un_succes_peut_suivre_un_echec(self):
        don = self.jef(Donation.PaymentStatus.FAILED)
        self.appel('succeeded')
        don.refresh_from_db()
        self.assertEqual(don.payment_status, Donation.PaymentStatus.CONFIRMED)


class EncaissementTests(AuditArgentBase):
    def test_un_collecteur_n_encaisse_pas_son_propre_jef(self):
        self.compte('collecteur2@test.sn', User.Role.COLLECTOR)  # la zone reste couverte
        self.client.force_authenticate(self.collector)
        r = self.client.post('/api/contributions/', {
            'campaign': self.campaign.id, 'amount': 5000, 'payment_method': 'manual',
        }, format='json')
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        r = self.client.post(f"/api/contributions/{r.data['id']}/collect/")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_seul_collecteur_de_sa_zone_il_ne_peut_pas_demander_de_collecte(self):
        # Il ne peut pas l'encaisser lui-même : personne d'autre ne le pourrait.
        self.client.force_authenticate(self.collector)
        r = self.client.get('/api/contributions/collecte/')
        self.assertFalse(r.data['disponible'])
        r = self.client.post('/api/contributions/', {
            'campaign': self.campaign.id, 'amount': 5000, 'payment_method': 'manual',
        }, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_un_montant_compte_infini_est_refuse(self):
        don = Donation.objects.create(
            campaign=self.campaign, donor=self.member, amount=1000,
            payment_method=Donation.PaymentMethod.MANUAL, payment_status=Donation.PaymentStatus.COLLECTED,
            collector=self.collector, collected_at=timezone.now(),
        )
        self.client.force_authenticate(self.admin)
        for valeur in ('Infinity', 'NaN'):
            r = self.client.post('/api/contributions/admin/confirm-cash/', {
                'collector': self.collector.id, 'donations': [don.id], 'counted_amount': valeur,
            }, format='json')
            self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST, valeur)
