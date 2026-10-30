"""La fenêtre de suivi de Sangue Bi et son rôle (décisions du 2026-10-30).

La guide voit tout, jusqu'au montant de chaque Jëf, et publie dans son canal ;
elle ne valide ni ne modifie rien, et n'entre dans aucun écran
d'administration.
"""
import datetime
from unittest import mock

from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import LDD, Daara, MemberTitle, User
from comms.canaux import canal
from comms.models import ChatMembership
from contributions.models import Donation
from events.models import Campaign


class Base(APITestCase):
    def setUp(self):
        mock.patch('comms.services.trigger_pusher').start()
        mock.patch('comms.services.send_fcm_push').start()
        mock.patch('comms.services.lancer_en_arriere_plan', side_effect=lambda fn: fn()).start()
        self.addCleanup(mock.patch.stopall)

        self.today = timezone.localdate()
        self.casa = LDD.objects.create(code='DS C', name='Casamance Midadi')
        self.armee = LDD.objects.create(code='DS A', name='Armée')
        LDD.objects.create(code='DS X', name='Sans Jëf')
        self.kande = Daara.objects.create(name='Kandé', ldd=self.casa)
        self.police = Daara.objects.create(name='Police', ldd=self.armee)
        self.aviateur = Daara.objects.create(name='Aviateur', ldd=self.armee)
        self.serigne = MemberTitle.objects.create(name='Serigne')

        actif = {'password': 'x', 'status': User.Status.ACTIVE}
        self.guide = User.objects.create_user(
            email='sanguebi@ex.org', first_name='Sangue', last_name='Bi', role=User.Role.GUIDE, **actif,
        )
        self.admin = User.objects.create_user(email='admin@ex.org', role=User.Role.ADMIN, **actif)
        self.membre = User.objects.create_user(email='m@ex.org', first_name='Modou', daara=self.kande, **actif)
        self.awa = User.objects.create_user(
            email='awa@ex.org', first_name='Awa', last_name='Ndiaye', daara=self.kande,
            title=self.serigne, residence_country='France', **actif,
        )
        self.fatou = User.objects.create_user(
            email='fatou@ex.org', first_name='Fatou', daara=self.police, residence_country='Senegal', **actif,
        )
        self.bineta = User.objects.create_user(
            email='bineta@ex.org', first_name='Bineta', last_name='Sow', daara=self.police,
            role=User.Role.CHEF_DAARA, residence_country='Sénégal', phone='+221770000001', **actif,
        )
        self.papice = User.objects.create_user(
            email='papice@ex.org', first_name='Papice', daara=self.aviateur, role=User.Role.CHEF_DAARA, **actif,
        )
        self.salle = Campaign.objects.create(
            name='Grande salle', status=Campaign.Status.ACTIVE, goal_amount=100000,
            deadline=self.today + datetime.timedelta(days=3), created_by=self.admin,
        )

    def jef(self, donor, amount, status=Donation.PaymentStatus.CONFIRMED, quand=None):
        with self.captureOnCommitCallbacks(execute=True):
            d = Donation.objects.create(campaign=self.salle, donor=donor, amount=amount, payment_status=status)
        if quand:
            Donation.objects.filter(pk=d.pk).update(created_at=quand)
        return d


class AccesTests(Base):
    def test_la_guide_et_l_admin_entrent_les_autres_non(self):
        for user, attendu in ((self.guide, 200), (self.admin, 200), (self.membre, 403), (self.bineta, 403)):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get('/api/suivi/').status_code, attendu, user.email)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/suivi/').status_code, 401)

    def test_la_guide_n_est_pas_administratrice(self):
        self.assertFalse(self.guide.is_staff)
        self.client.force_authenticate(self.guide)
        self.assertEqual(self.client.get('/api/users/').status_code, 403)
        d = self.jef(self.awa, 5000, Donation.PaymentStatus.PENDING)
        for url in ('admin/confirm-wire', 'admin/cancel'):
            r = self.client.post(f'/api/contributions/{d.id}/{url}/')
            self.assertEqual(r.status_code, 403, url)
        d.refresh_from_db()
        self.assertEqual(d.payment_status, Donation.PaymentStatus.PENDING)


class CanalTests(Base):
    def test_la_guide_publie_d_office_dans_son_canal(self):
        chat = canal('guide')
        self.assertTrue(ChatMembership.objects.get(chat=chat, user=self.guide).can_post)
        self.client.force_authenticate(self.guide)
        r = self.client.post('/api/comms/messages/', {'chat': chat.id, 'content': 'Assalamou alaykoum'})
        self.assertEqual(r.status_code, 201, r.content)

    def test_le_droit_suit_le_role(self):
        chat = canal('guide')
        self.membre.role = User.Role.GUIDE
        self.membre.save()
        self.assertTrue(ChatMembership.objects.get(chat=chat, user=self.membre).can_post)
        self.membre.role = User.Role.MEMBER
        self.membre.save(update_fields=['role'])
        self.assertFalse(ChatMembership.objects.get(chat=chat, user=self.membre).can_post)

    def test_un_auteur_designe_a_la_main_garde_son_droit(self):
        chat = canal('guide')
        ChatMembership.objects.filter(chat=chat, user=self.membre).update(can_post=True)
        self.membre.first_name = 'Modou Lamine'
        self.membre.save()
        self.assertTrue(ChatMembership.objects.get(chat=chat, user=self.membre).can_post)


class VueDEnsembleTests(Base):
    def test_totaux_attente_et_diaspora(self):
        self.jef(self.awa, 60000)          # France
        self.jef(self.fatou, 40000)        # « Senegal » sans accent
        self.jef(self.membre, 7000, Donation.PaymentStatus.COLLECTED)
        self.jef(self.membre, 1000, Donation.PaymentStatus.CANCELLED)
        self.client.force_authenticate(self.guide)
        r = self.client.get('/api/suivi/').json()
        self.assertEqual(r['donations']['amount'], 100000)
        self.assertEqual(r['donations']['count'], 2)
        self.assertEqual(r['donations']['diaspora_pct'], 60)
        self.assertEqual(r['donations']['pending'], {'amount': 7000, 'count': 1})
        # Ni l'admin ni la guide ne sont des Talubés.
        self.assertEqual(r['members']['total'], 5)
        self.assertEqual(r['members']['titled'], 1)
        self.assertEqual(r['ldd'], {'active': 2, 'total': 3, 'top': 'Casamance Midadi'})
        self.assertEqual(r['campaigns_list'][0]['pct'], 100)

    def test_la_periode_borne_les_jefs(self):
        self.jef(self.awa, 60000, quand=timezone.now() - datetime.timedelta(days=120))
        self.jef(self.fatou, 40000)
        self.client.force_authenticate(self.guide)
        self.assertEqual(self.client.get('/api/suivi/?periode=mois').json()['donations']['amount'], 40000)
        self.assertEqual(self.client.get('/api/suivi/?periode=tout').json()['donations']['amount'], 100000)
        mensuel = self.client.get('/api/suivi/').json()['monthly']
        self.assertEqual(len(mensuel), 8)
        self.assertEqual(mensuel[-1]['amount'], 40000)

    def test_a_regarder(self):
        self.jef(self.fatou, 10000)
        self.jef(self.membre, 5000, Donation.PaymentStatus.PENDING)
        self.client.force_authenticate(self.guide)
        alertes = {a['kind']: a for a in self.client.get('/api/suivi/').json()['alerts']}
        self.assertIn('se termine dans 3 jours, à 10 %', alertes['ndiguel_fin']['text'])
        self.assertEqual(alertes['en_attente']['strong'], '1 Jëf')
        self.assertEqual(alertes['daara_sans_jef']['strong'], 'Daara Aviateur')
        self.assertEqual(alertes['ldd_sans_jef']['strong'], '2 LDD sur 3')

    def test_un_ndiguel_pas_encore_ouvert_n_est_pas_signale(self):
        Campaign.objects.filter(pk=self.salle.pk).update(opens_on=self.today + datetime.timedelta(days=1))
        self.client.force_authenticate(self.guide)
        kinds = [a['kind'] for a in self.client.get('/api/suivi/').json()['alerts']]
        self.assertNotIn('ndiguel_fin', kinds)

    def test_coordinateurs_et_derniers_jefs(self):
        self.jef(self.fatou, 10000)
        self.client.force_authenticate(self.guide)
        r = self.client.get('/api/suivi/').json()
        chefs = {c['name']: c for c in r['coordinators_list']}
        self.assertEqual(chefs['Bineta Sow']['amount'], 10000)
        self.assertEqual(chefs['Papice']['amount'], 0)
        self.assertEqual(r['recent'][0]['amount'], 10000)
        self.assertEqual(r['recent'][0]['daara'], 'Police')


class RepartitionTests(Base):
    def setUp(self):
        super().setUp()
        self.jef(self.awa, 60000)
        self.jef(self.fatou, 30000)
        self.jef(self.bineta, 10000)
        self.client.force_authenticate(self.guide)

    def get(self, par):
        return self.client.get(f'/api/suivi/repartition/?par={par}').json()

    def test_par_ldd_et_par_daara(self):
        ldd = self.get('ldd')
        self.assertEqual([(l['name'], l['amount']) for l in ldd['rows']], [('Casamance Midadi', 60000), ('Armée', 40000)])
        self.assertEqual(ldd['without_jef'], 1)
        daara = self.get('daara')
        self.assertEqual(daara['rows'][0]['name'], 'Kandé')
        self.assertEqual(daara['rows'][0]['pct'], 60)
        self.assertEqual(daara['without_jef'], 1)

    def test_par_titre(self):
        rows = {l['name']: l for l in self.get('titre')['rows']}
        self.assertEqual(rows['Serigne']['amount'], 60000)
        self.assertEqual(rows['Serigne']['members'], 1)
        self.assertEqual(rows['Sans titre']['amount'], 40000)

    def test_par_pays_les_graphies_d_un_meme_pays_se_rejoignent(self):
        rows = {l['name']: l['amount'] for l in self.get('pays')['rows']}
        self.assertEqual(rows, {'France': 60000, 'Sénégal': 40000})


class DaaraTests(Base):
    def test_jusqu_au_membre(self):
        self.jef(self.fatou, 30000)
        self.jef(self.fatou, 5000)
        self.client.force_authenticate(self.guide)
        r = self.client.get(f'/api/suivi/daara/{self.police.id}/').json()
        self.assertEqual(r['coordinator']['name'], 'Bineta Sow')
        self.assertEqual(r['coordinator']['phone'], '+221770000001')
        self.assertEqual((r['amount'], r['count'], r['members_count'], r['donors_count']), (35000, 2, 2, 1))
        self.assertEqual(r['members'][0]['name'], 'Fatou')
        self.assertEqual(r['members'][0]['amount'], 35000)
        self.assertEqual([d['amount'] for d in r['donations']], [5000, 30000])

    def test_un_daara_inconnu(self):
        self.client.force_authenticate(self.guide)
        self.assertEqual(self.client.get('/api/suivi/daara/999999/').status_code, 404)
