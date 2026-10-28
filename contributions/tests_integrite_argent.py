"""Audit « Intégrité argent » (2026-10-28) : ce qui pouvait faire disparaître un Jëf.

Avant cet audit, supprimer un Ndiguel ou un membre effaçait en cascade tous ses
Jëfs — confirmés et remis compris. Et n'importe quel coordinateur de Daara
pouvait supprimer n'importe quel Ndiguel.
"""

from django.contrib.auth import get_user_model
from django.db.models import ProtectedError
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Daara, LDD
from contributions.models import Donation
from events.models import Campaign, Fete

User = get_user_model()


class IntegriteBase(APITestCase):
    def setUp(self):
        daara = Daara.objects.create(name='Daara', ldd=LDD.objects.create(code='DS S1', name='PIKINE'))

        def compte(email, role):
            return User.objects.create_user(
                email=email, password='Motdepasse123!', role=role,
                status=User.Status.ACTIVE, daara=daara,
            )

        self.admin = compte('admin@test.sn', User.Role.ADMIN)
        self.chef = compte('chef@test.sn', User.Role.CHEF_DAARA)
        self.membre = compte('membre@test.sn', User.Role.MEMBER)
        self.fete = Fete.objects.create(name='Magal', date=timezone.now().date(), recurrence=Fete.Recurrence.ANNUAL)
        self.campaign = Campaign.objects.create(
            name='Ndiguel', deadline=timezone.now().date(), fete=self.fete, created_by=self.admin,
            status=Campaign.Status.ACTIVE,
        )
        Donation.objects.create(
            campaign=self.campaign, donor=self.membre, amount=5000, payment_status='confirmed',
        )


class SuppressionsTests(IntegriteBase):
    def test_la_base_refuse_d_effacer_un_jef_par_ricochet(self):
        with self.assertRaises(ProtectedError):
            self.campaign.delete()
        with self.assertRaises(ProtectedError):
            self.membre.delete()

    def test_un_coordinateur_ne_supprime_pas_un_ndiguel(self):
        self.client.force_authenticate(self.chef)
        r = self.client.delete(reverse('campaigns-detail', args=[self.campaign.pk]))
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_un_ndiguel_qui_porte_des_jefs_se_cloture(self):
        self.client.force_authenticate(self.admin)
        r = self.client.delete(reverse('campaigns-detail', args=[self.campaign.pk]))
        self.assertEqual(r.status_code, status.HTTP_409_CONFLICT)
        vide = Campaign.objects.create(
            name='Vide', deadline=timezone.now().date(), fete=self.fete, created_by=self.admin,
        )
        self.assertEqual(
            self.client.delete(reverse('campaigns-detail', args=[vide.pk])).status_code,
            status.HTTP_204_NO_CONTENT,
        )

    def test_un_compte_avec_des_jefs_se_bloque(self):
        self.client.force_authenticate(self.admin)
        r = self.client.delete(f'/api/users/{self.membre.pk}/')
        self.assertEqual(r.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('Bloquez', r.data['detail'])
        self.assertTrue(Donation.objects.filter(donor=self.membre).exists())

    def test_montant_en_francs_entiers(self):
        self.client.force_authenticate(self.membre)
        r = self.client.post(reverse('donations-list'), {
            'campaign': self.campaign.pk, 'amount': '100.50', 'payment_method': 'wave',
        }, format='json')
        self.assertEqual(r.status_code, 400)


class AttributionsTests(IntegriteBase):
    def test_la_tutelle_d_un_autre_est_refusee(self):
        from accounts.models import Tutelle
        autre = User.objects.create_user(
            email='autre@test.sn', password='Motdepasse123!', role=User.Role.MEMBER,
            status=User.Status.ACTIVE,
        )
        tutelle = Tutelle.objects.create(tutor=autre, first_name='Aïda', last_name='Sow')
        self.client.force_authenticate(self.membre)
        r = self.client.post(reverse('donations-list'), {
            'campaign': self.campaign.pk, 'amount': 1000, 'payment_method': 'wave',
            'beneficiary': tutelle.pk,
        }, format='json')
        self.assertEqual(r.status_code, 400)

    def test_un_admin_ne_confirme_pas_son_propre_virement(self):
        virement = Donation.objects.create(
            campaign=self.campaign, donor=self.admin, amount=5000,
            payment_method='virement', payment_status='pending_wire',
        )
        self.client.force_authenticate(self.admin)
        r = self.client.post(f"{reverse('donations-list')}{virement.pk}/admin/confirm-wire/")
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
