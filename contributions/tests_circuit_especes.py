"""Intégrité de l'argent et circuit des espèces.

Le circuit : un membre DEMANDE une collecte (`pending`), un collecteur
ENCAISSE (`collected`), l'administration CONFIRME à la remise, par un bordereau
(`CashDeposit`). Chaque test ci-dessous fermait un chemin réellement ouvert
avant le 2026-10-28 :

  · le client écrivait lui-même `payment_status` ;
  · PUT, PATCH et DELETE étaient ouverts à tous les rôles ;
  · n'importe quel compte enregistrait un Jëf au nom d'un autre ;
  · le mode collecte du mobile (`member_id`) imputait le Jëf au collecteur ;
  · `pay` rétrogradait un Jëf déjà confirmé ;
  · aucune étape ne séparait celui qui encaisse de celui qui confirme.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Daara, LDD
from comms.models import Notification
from contributions.models import CashDeposit, Donation
from events.models import Campaign, Fete

User = get_user_model()


class CircuitEspecesBase(APITestCase):
    def setUp(self):
        ldd = LDD.objects.create(code='DS S10', name='DIOURBEL RABBI')
        self.daara = Daara.objects.create(name='Daara Diourbel', ldd=ldd)

        def compte(email, role, **extra):
            return User.objects.create_user(
                email=email, password='Motdepasse123!', role=role,
                status=User.Status.ACTIVE, daara=self.daara, **extra,
            )

        self.admin = compte('admin@test.sn', User.Role.ADMIN, first_name='Awa', last_name='Admin')
        self.admin2 = compte('admin2@test.sn', User.Role.ADMIN, first_name='Bamba', last_name='Admin')
        self.member = compte('membre@test.sn', User.Role.MEMBER, first_name='Fatou', last_name='Ndiaye')
        self.other = compte('autre@test.sn', User.Role.MEMBER, first_name='Modou', last_name='Fall')
        self.collector = compte('collecteur@test.sn', User.Role.COLLECTOR, first_name='Moussa', last_name='Sarr')
        self.chef = compte('chef@test.sn', User.Role.CHEF_DAARA, first_name='Ibra', last_name='Diop')

        fete = Fete.objects.create(
            name='Magal', date=timezone.now().date(), recurrence=Fete.Recurrence.ANNUAL,
        )
        self.campaign = Campaign.objects.create(
            name='Ndiguel Magal', deadline=timezone.now().date(), fete=fete,
            daara=self.daara, created_by=self.admin,
        )
        self.list_url = reverse('donations-list')

    def creer(self, auteur, **payload):
        self.client.force_authenticate(auteur)
        corps = {'campaign': self.campaign.id, 'amount': 10000, 'payment_method': 'manual'}
        corps.update(payload)
        return self.client.post(self.list_url, corps, format='json')

    def url(self, donation, suffixe):
        return f"{self.list_url}{donation.pk}/{suffixe}/"

    def encaisse(self, montant=10000, collecteur=None):
        return Donation.objects.create(
            campaign=self.campaign, donor=self.member, amount=montant,
            payment_method='manual', payment_status=Donation.PaymentStatus.COLLECTED,
            collector=collecteur or self.collector, collected_at=timezone.now(),
        )


class IntegriteEcritureTests(CircuitEspecesBase):
    def test_le_client_ne_choisit_pas_le_statut(self):
        r = self.creer(self.member, payment_status='confirmed', payment_method='wave')
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Donation.objects.get(pk=r.data['id']).payment_status, 'pending')

    def test_un_membre_peut_faire_son_jef(self):
        # Les deux formes réellement émises : le mobile omet `donor`, le web
        # l'envoie à null. Toutes deux rendaient 400 avant le 2026-10-28.
        for corps in ({}, {'donor': None}):
            r = self.creer(self.member, payment_method='wave', **corps)
            self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
            self.assertEqual(Donation.objects.get(pk=r.data['id']).donor, self.member)

    def test_ni_modification_ni_suppression(self):
        d = Donation.objects.create(campaign=self.campaign, donor=self.member, amount=5000)
        self.client.force_authenticate(self.member)
        detail = f"{self.list_url}{d.pk}/"
        for verbe in (self.client.patch, self.client.put, self.client.delete):
            self.assertEqual(
                verbe(detail, {'payment_status': 'confirmed'}, format='json').status_code,
                status.HTTP_405_METHOD_NOT_ALLOWED,
            )
        d.refresh_from_db()
        self.assertEqual(d.payment_status, 'pending')

    def test_un_membre_ne_donne_pas_au_nom_d_un_autre(self):
        r = self.creer(self.member, donor=self.other.id, payment_method='wave')
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Donation.objects.exists())

    def test_montant_negatif_ou_nul_refuse(self):
        for montant in (-5000, 0):
            self.assertEqual(self.creer(self.member, amount=montant).status_code, 400)

    def test_moyen_herite_refuse(self):
        self.assertEqual(self.creer(self.member, payment_method='paypal').status_code, 400)

    def test_pay_ne_retrograde_pas_un_jef_confirme(self):
        d = Donation.objects.create(
            campaign=self.campaign, donor=self.member, amount=5000,
            payment_status=Donation.PaymentStatus.CONFIRMED,
        )
        self.client.force_authenticate(self.member)
        r = self.client.post(self.url(d, 'pay'), {'payment_method': 'virement', 'wire_reference': 'VIR-1'}, format='json')
        self.assertEqual(r.status_code, 400)
        d.refresh_from_db()
        self.assertEqual(d.payment_status, 'confirmed')

    def test_un_chef_ne_paie_pas_a_la_place_d_un_membre(self):
        d = Donation.objects.create(campaign=self.campaign, donor=self.member, amount=5000)
        self.client.force_authenticate(self.chef)
        r = self.client.post(self.url(d, 'pay'), {'payment_method': 'virement', 'wire_reference': 'VIR-1'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)


class DemandeEtEncaissementTests(CircuitEspecesBase):
    def test_demande_de_collecte_previent_les_collecteurs(self):
        r = self.creer(self.member)
        d = Donation.objects.get(pk=r.data['id'])
        self.assertEqual(d.payment_status, 'pending')
        self.assertIsNone(d.collector)
        self.assertTrue(
            Notification.objects.filter(user=self.collector, title__icontains='récolter').exists()
        )

    def test_collecte_sur_le_terrain_au_nom_du_membre(self):
        r = self.creer(self.collector, donor=self.member.id)
        d = Donation.objects.get(pk=r.data['id'])
        self.assertEqual(d.donor, self.member)
        self.assertEqual(d.collector, self.collector)
        self.assertEqual(d.payment_status, 'collected')
        self.assertIsNotNone(d.collected_at)
        # Le membre a son reçu ; personne n'est envoyé ramasser un argent déjà ramassé.
        self.assertTrue(Notification.objects.filter(user=self.member, title='Jëf encaissé').exists())
        self.assertFalse(Notification.objects.filter(title__icontains='récolter').exists())

    def test_relais_member_id_des_anciennes_versions_mobiles(self):
        r = self.creer(self.collector, member_id=self.member.id)
        d = Donation.objects.get(pk=r.data['id'])
        self.assertEqual(d.donor, self.member)
        self.assertEqual(d.collector, self.collector)

    def test_un_chef_qui_encaisse_est_trace_comme_collecteur(self):
        r = self.creer(self.chef, donor=self.member.id)
        self.assertEqual(Donation.objects.get(pk=r.data['id']).collector, self.chef)

    def test_paiement_digital_pour_autrui_reste_en_attente(self):
        r = self.creer(self.collector, donor=self.member.id, payment_method='wave')
        d = Donation.objects.get(pk=r.data['id'])
        self.assertEqual(d.payment_status, 'pending')
        self.assertEqual(d.collector, self.collector)

    def test_le_collecteur_voit_et_encaisse_une_demande(self):
        demande = Donation.objects.create(
            campaign=self.campaign, donor=self.other, amount=7500, payment_method='manual',
        )
        self.client.force_authenticate(self.collector)
        ids = [row['id'] for row in self.client.get(self.list_url).data]
        self.assertIn(demande.pk, ids)

        r = self.client.post(self.url(demande, 'collect'))
        self.assertEqual(r.status_code, 200)
        demande.refresh_from_db()
        self.assertEqual(demande.payment_status, 'collected')
        self.assertEqual(demande.collector, self.collector)

        # Pas deux fois.
        self.assertEqual(self.client.post(self.url(demande, 'collect')).status_code, 400)

    def test_un_membre_n_encaisse_pas(self):
        demande = Donation.objects.create(
            campaign=self.campaign, donor=self.member, amount=7500, payment_method='manual',
        )
        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.post(self.url(demande, 'collect')).status_code, 403)


class RemiseTests(CircuitEspecesBase):
    def remettre(self, jefs, compte, auteur=None, collecteur=None):
        self.client.force_authenticate(auteur or self.admin)
        return self.client.post(
            f"{self.list_url}admin/confirm-cash/",
            {
                'collector': (collecteur or self.collector).pk,
                'donations': [j.pk for j in jefs],
                'counted_amount': compte,
            },
            format='json',
        )

    def test_caisse_par_collecteur(self):
        self.encaisse(10000)
        self.encaisse(5000)
        self.client.force_authenticate(self.admin)
        caisses = self.client.get(f"{self.list_url}admin/pending-cash/").data
        self.assertEqual(len(caisses), 1)
        self.assertEqual(caisses[0]['collector'], self.collector.pk)
        self.assertEqual(caisses[0]['count'], 2)
        self.assertEqual(Decimal(caisses[0]['total_amount']), Decimal('15000'))

    def test_montant_compte_faux_refuse_toute_la_remise(self):
        a, b = self.encaisse(10000), self.encaisse(5000)
        r = self.remettre([a, b], 14000)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(CashDeposit.objects.exists())
        a.refresh_from_db()
        self.assertEqual(a.payment_status, 'collected')

    def test_remise_valide_confirme_et_compte_dans_le_ndiguel(self):
        a, b = self.encaisse(10000), self.encaisse(5000)
        r = self.remettre([a, b], '15000')
        self.assertEqual(r.status_code, 201)

        remise = CashDeposit.objects.get()
        self.assertEqual(remise.total_amount, Decimal('15000'))
        self.assertEqual(remise.validated_by, self.admin)
        for j in (a, b):
            j.refresh_from_db()
            self.assertEqual(j.payment_status, 'confirmed')
            self.assertEqual(j.cash_deposit, remise)
            self.assertEqual(j.validated_by, self.admin)

        self.client.force_authenticate(self.admin)
        ndiguel = self.client.get(reverse('campaigns-detail', args=[self.campaign.pk])).data
        self.assertEqual(Decimal(str(ndiguel['collected_amount'])), Decimal('15000'))

        # Une remise ne se rejoue pas.
        self.assertEqual(self.remettre([a], 10000).status_code, 400)

    def test_on_ne_valide_pas_sa_propre_remise(self):
        jef = self.encaisse(10000, collecteur=self.admin)
        r = self.remettre([jef], 10000, auteur=self.admin, collecteur=self.admin)
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.remettre([jef], 10000, auteur=self.admin2, collecteur=self.admin).status_code, 201)

    def test_une_remise_ne_melange_pas_les_collecteurs(self):
        a = self.encaisse(10000)
        b = self.encaisse(5000, collecteur=self.chef)
        self.assertEqual(self.remettre([a, b], 15000).status_code, 400)

    def test_seul_l_admin_valide(self):
        jef = self.encaisse(10000)
        self.assertEqual(self.remettre([jef], 10000, auteur=self.chef).status_code, 403)


class AnnulationTests(CircuitEspecesBase):
    def annuler(self, jef, motif='Doublon'):
        self.client.force_authenticate(self.admin)
        return self.client.post(self.url(jef, 'admin/cancel'), {'reason': motif}, format='json')

    def test_motif_obligatoire(self):
        self.assertEqual(self.annuler(self.encaisse(), motif='  ').status_code, 400)

    def test_annuler_un_jef_encaisse(self):
        jef = self.encaisse()
        self.assertEqual(self.annuler(jef).status_code, 200)
        jef.refresh_from_db()
        self.assertEqual(jef.payment_status, 'cancelled')
        self.assertEqual(jef.cancellation_reason, 'Doublon')
        self.assertTrue(Notification.objects.filter(user=self.member, title='Jëf annulé').exists())
        self.assertTrue(Notification.objects.filter(user=self.collector, title='Jëf annulé').exists())

    def test_un_jef_confirme_ne_s_annule_pas(self):
        jef = Donation.objects.create(
            campaign=self.campaign, donor=self.member, amount=5000,
            payment_status=Donation.PaymentStatus.CONFIRMED,
        )
        self.assertEqual(self.annuler(jef).status_code, 400)
