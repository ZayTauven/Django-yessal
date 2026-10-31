"""Audit des modules du 2026-10-31, lot « erreurs 500 et tableau de bord ».

  · approuver une demande de titre après une attribution directe → 500 ;
  · Daara sans nom accepté, Daara en double → 500, état d'un Daara absent → 500 ;
  · canal Pusher, auteur de canal, destinataire d'invitation mal formés → 500 ;
  · le tableau de bord comptait des Ndiguels clos « en cours », les appelait
    « Jëfs », rangeait les virements avec les espèces, écrivait les mois en
    anglais, et ne montrait rien à une tutelle.
"""

import datetime

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import LDD, Daara, MemberTitle, TitleRequest, Tutelle
from comms.models import Chat, ChatMembership
from contributions.models import Donation
from events.models import Campaign

User = get_user_model()


class TableauBase(APITestCase):
    def setUp(self):
        self.client.raise_request_exception = False
        self.ldd = LDD.objects.create(code='DS S10', name='DIOURBEL RABBI')
        self.daara = Daara.objects.create(name='Daara Diourbel', ldd=self.ldd)

        def compte(email, role):
            return User.objects.create_user(
                email=email, password='Motdepasse123!', role=role, status=User.Status.ACTIVE,
                daara=self.daara, first_name=email.split('@')[0].capitalize(), last_name='Test',
            )

        self.compte = compte
        self.admin = compte('admin@test.sn', User.Role.ADMIN)
        self.member = compte('membre@test.sn', User.Role.MEMBER)

    def en(self, user):
        self.client.force_authenticate(user)


class TitresTests(TableauBase):
    def setUp(self):
        super().setUp()
        self.t1 = MemberTitle.objects.create(name='Diawrigne')
        self.t2 = MemberTitle.objects.create(name='Jawriñ')

    def test_l_attribution_directe_clot_la_demande_en_attente(self):
        self.en(self.member)
        self.assertEqual(self.client.post('/api/title-requests/', {'title': self.t1.id}, format='json').status_code, 201)
        self.en(self.admin)
        self.client.post(f'/api/members/{self.member.id}/assign-title/', {'title_id': self.t2.id}, format='json')
        demande = TitleRequest.objects.get()
        self.assertEqual(demande.status, TitleRequest.Status.REFUSED)
        r = self.client.patch(f'/api/title-requests/{demande.id}/review/', {'action': 'approve'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_approuver_un_changement_epuise_repond_400(self):
        demande = TitleRequest.objects.create(member=self.member, title=self.t1)
        self.member.title_change_count = 1
        self.member.save(update_fields=['title_change_count'])
        self.en(self.admin)
        r = self.client.patch(f'/api/title-requests/{demande.id}/review/', {'action': 'approve'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_un_titre_desactive_ne_se_demande_pas(self):
        self.t1.is_active = False
        self.t1.save(update_fields=['is_active'])
        self.en(self.member)
        r = self.client.post('/api/title-requests/', {'title': self.t1.id}, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)


class EntreesInvalidesTests(TableauBase):
    def test_daara_sans_nom_ou_en_double(self):
        self.en(self.admin)
        for nom in ('  ', 'Daara Diourbel', 'daara diourbel'):
            r = self.client.post('/api/daara/', {'ldd_id': self.ldd.id, 'name': nom}, format='json')
            self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST, nom)

    def test_etat_d_un_daara_absent(self):
        self.en(self.admin)
        self.assertEqual(self.client.get('/api/daara/999999/etat/').status_code, status.HTTP_404_NOT_FOUND)

    def test_canal_pusher_mal_forme(self):
        self.en(self.member)
        r = self.client.post('/api/comms/pusher/auth/', {'channel_name': 'private-chat.abc', 'socket_id': '1.2'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_auteur_de_canal_inconnu(self):
        canal = Chat.objects.create(chat_type=Chat.ChatType.CHANNEL, name='Annonces')
        ChatMembership.objects.create(chat=canal, user=self.admin, can_post=True)
        self.en(self.admin)
        for valeur in (999999, 'abc', None):
            r = self.client.post(f'/api/comms/{canal.id}/authors/', {'user_id': valeur}, format='json')
            self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST, valeur)

    def test_destinataire_d_invitation_mal_forme(self):
        self.en(self.member)
        r = self.client.post('/api/comms/invitations/', {'recipient': 'abc'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)


class TableauDeBordTests(TableauBase):
    def setUp(self):
        super().setUp()
        aujourd_hui = timezone.localdate()

        def ndiguel(nom, **champs):
            champs.setdefault('status', Campaign.Status.ACTIVE)
            champs.setdefault('deadline', aujourd_hui + datetime.timedelta(days=30))
            return Campaign.objects.create(name=nom, created_by=self.admin, **champs)

        self.en_cours = ndiguel('En cours')
        self.bientot_fini = ndiguel('Finit dans 3 jours', deadline=aujourd_hui + datetime.timedelta(days=3))
        ndiguel('Clos le mois dernier', deadline=aujourd_hui - datetime.timedelta(days=20))
        ndiguel('Date lunaire à confirmer', a_confirmer=True)
        ndiguel('Ouvre dans 5 jours', opens_on=aujourd_hui + datetime.timedelta(days=5))
        ndiguel('Suspendu', status=Campaign.Status.INACTIVE)
        # « À venir » posé à la main, mais sa date d'ouverture est passée : en cours.
        ndiguel('Ouvert par sa date', status=Campaign.Status.PENDING, opens_on=aujourd_hui - datetime.timedelta(days=1))

    def stats(self, user):
        self.en(user)
        return self.client.get('/api/analytics/').json()

    def test_ndiguels_en_cours_et_leur_libelle(self):
        kpis = {k['title']: k['value'] for k in self.stats(self.admin)['kpis']}
        self.assertNotIn('Jëfs Actifs', kpis)
        self.assertEqual(kpis['Ndiguels en cours'], '3')

    def test_l_alerte_d_echeance_ignore_les_ndiguels_clos(self):
        alertes = {a['id']: a['title'] for a in self.stats(self.admin)['alerts']}
        self.assertEqual(alertes['ending_soon'], '1 Ndiguel(s) se terminent dans 7 jours')

    def test_mois_en_francais_et_virements_hors_especes(self):
        for methode in (Donation.PaymentMethod.VIREMENT, Donation.PaymentMethod.MANUAL):
            Donation.objects.create(
                campaign=self.en_cours, donor=self.member, amount=1000,
                payment_method=methode, payment_status=Donation.PaymentStatus.CONFIRMED,
            )
        barres = self.stats(self.admin)['bar_chart']
        self.assertEqual(len(barres), 1)
        mois = timezone.localdate().month
        self.assertEqual(barres[0]['month'], ('janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.',
                                              'août', 'sept.', 'oct.', 'nov.', 'déc.')[mois - 1])
        self.assertEqual((barres[0]['online'], barres[0]['manual']), (1000, 1000))

    def test_la_tutelle_voit_les_jefs_faits_en_son_nom(self):
        pupille = self.compte('pupille@test.sn', User.Role.TUTELLE)
        fiche = Tutelle.objects.create(tutor=self.member, first_name='Aïda', last_name='Test',
                                       relation='fille', linked_user=pupille)
        Donation.objects.create(
            campaign=self.en_cours, donor=self.member, beneficiary=fiche, amount=2500,
            payment_method=Donation.PaymentMethod.WAVE, payment_status=Donation.PaymentStatus.CONFIRMED,
        )
        kpis = {k['title']: k['value'] for k in self.stats(pupille)['kpis']}
        self.assertEqual(kpis['Mes Contributions'], '2,500 FCFA')
