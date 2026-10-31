"""Ce que `/etat/` laisse voir d'un Jëf (audit de sécurité du 2026-10-31).

Les deux `/etat/` sont ouverts à tout compte connecté. Ils renvoyaient le
montant de chaque Jëf à tous, et l'identifiant du donateur d'un Jëf anonyme.
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import LDD, Daara
from contributions.models import Donation
from events.models import Campaign

User = get_user_model()


class EtatTests(APITestCase):
    def setUp(self):
        daara = Daara.objects.create(name='KANDE', ldd=LDD.objects.create(code='DS S17', name='KAOLACK1'))
        actif = {'status': User.Status.ACTIVE, 'daara': daara}
        self.admin = User.objects.create_user(email='a@ex.org', password='x', role=User.Role.ADMIN, **actif)
        self.coordo = User.objects.create_user(email='c@ex.org', password='x', role=User.Role.CHEF_DAARA, **actif)
        self.awa = User.objects.create_user(email='awa@ex.org', password='x', first_name='Awa', **actif)
        self.modou = User.objects.create_user(email='modou@ex.org', password='x', first_name='Modou', **actif)
        self.camp = Campaign.objects.create(
            name='Magal', deadline=date.today() + timedelta(days=30), status='active',
        )
        Donation.objects.create(campaign=self.camp, donor=self.awa, amount=5000, payment_status='confirmed')
        Donation.objects.create(campaign=self.camp, donor=self.modou, amount=20000,
                                payment_status='confirmed', is_anonymous=True)

    def lignes(self, user):
        self.client.force_authenticate(user)
        r = self.client.get(f'/api/events/campaigns/{self.camp.id}/etat/')
        self.assertEqual(r.status_code, 200)
        return {l['member_name']: l for l in r.data['contributions']}

    def test_un_talube_ne_voit_que_son_propre_montant(self):
        lignes = self.lignes(self.awa)
        self.assertEqual(int(lignes['Awa']['amount']), 5000)
        self.assertIsNone(lignes['Contributeur anonyme']['amount'])

    def test_un_jef_anonyme_ne_dit_ni_qui_ni_d_ou(self):
        anonyme = self.lignes(self.coordo)['Contributeur anonyme']
        self.assertIsNone(anonyme['member_id'])
        self.assertIsNone(anonyme['daara_name'])
        self.assertEqual(int(anonyme['amount']), 20000)

    def test_l_administration_voit_tout(self):
        anonyme = self.lignes(self.admin)['Contributeur anonyme']
        self.assertEqual(anonyme['member_id'], self.modou.id)
