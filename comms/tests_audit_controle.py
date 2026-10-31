"""Audit des modules du 2026-10-31, lot « contrôle ».

Chaque test ferme un chemin éprouvé ouvert ce jour-là :

  · une pièce d'identité corrigée gardait son statut (validée ou refusée) ;
  · afficher la messagerie créait, pour le Daara du coordinateur, une config
    tout ouverte qui levait les restrictions de l'administration ;
  · comptes bloqués et inscriptions non validées restaient destinataires ;
  · organisateur et coordinateur ne pouvaient plus créer de tâche (403) ;
  · deux invitations croisées ouvraient deux tête-à-tête ;
  · un salon quitté par tous devenait un fil fantôme ingérable.
"""

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import LDD, Daara, UserDocument
from comms.models import Chat, ChatInvitation, ChatMembership, MessagingPilotageConfig, Notification
from comms.notify import administrateurs
from comms.services import get_effective_config
from events.models import Campaign, CampaignTodo

User = get_user_model()


class ControleBase(APITestCase):
    def setUp(self):
        ldd = LDD.objects.create(code='DS S10', name='DIOURBEL RABBI')
        self.daara = Daara.objects.create(name='Daara Diourbel', ldd=ldd)

        def compte(email, role, statut=User.Status.ACTIVE):
            return User.objects.create_user(
                email=email, password='Motdepasse123!', role=role, status=statut, daara=self.daara,
                first_name=email.split('@')[0].capitalize(), last_name='Test',
            )

        self.compte = compte
        self.admin = compte('admin@test.sn', User.Role.ADMIN)
        self.member = compte('membre@test.sn', User.Role.MEMBER)
        self.member2 = compte('membre2@test.sn', User.Role.MEMBER)
        self.chef = compte('chef@test.sn', User.Role.CHEF_DAARA)

    def en(self, user):
        self.client.force_authenticate(user)


class PiecesTests(ControleBase):
    def piece(self, statut):
        return UserDocument.objects.create(
            user=self.member, doc_type='national_id', image='documents/x.jpg', doc_number='111',
            status=statut, validated_by=self.admin, rejection_note='Photo floue',
        )

    def corriger(self, doc):
        self.en(self.member)
        return self.client.patch(f'/api/users/{self.member.id}/documents/{doc.id}/', {'doc_number': '999'}, format='multipart')

    def test_une_piece_validee_modifiee_repasse_en_attente(self):
        doc = self.piece(UserDocument.ValidationStatus.VALIDATED)
        self.assertEqual(self.corriger(doc).status_code, status.HTTP_200_OK)
        doc.refresh_from_db()
        self.assertEqual(doc.status, UserDocument.ValidationStatus.PENDING)
        self.assertIsNone(doc.validated_by_id)

    def test_une_piece_refusee_corrigee_revient_dans_la_file(self):
        doc = self.piece(UserDocument.ValidationStatus.REJECTED)
        self.corriger(doc)
        self.en(self.admin)
        data = self.client.get('/api/admin/documents/pending/').data
        ids = [d['id'] for d in (data['results'] if isinstance(data, dict) else data)]
        self.assertIn(doc.id, ids)
        doc.refresh_from_db()
        self.assertEqual(doc.rejection_note, '')


class PilotageTests(ControleBase):
    def setUp(self):
        super().setUp()
        MessagingPilotageConfig.objects.create(daara=None, allow_group_creation=False, allow_file_sharing=False)

    def test_afficher_la_messagerie_n_ecrit_rien(self):
        self.en(self.chef)
        self.assertEqual(self.client.get('/api/comms/pilotage/').status_code, 200)
        self.assertFalse(MessagingPilotageConfig.objects.filter(daara=self.daara).exists())
        self.assertFalse(get_effective_config(self.member).allow_group_creation)

    def test_une_config_de_daara_ne_desserre_pas_la_globale(self):
        # Les lignes tout ouvertes créées avant le correctif ne rouvrent rien.
        MessagingPilotageConfig.objects.create(daara=self.daara)
        cfg = get_effective_config(self.member)
        self.assertFalse(cfg.allow_group_creation)
        self.assertFalse(cfg.allow_file_sharing)
        self.assertTrue(cfg.allow_member_invite)

    def test_le_coordinateur_ne_rouvre_pas_ce_que_l_admin_a_ferme(self):
        self.en(self.chef)
        r = self.client.patch('/api/comms/pilotage/', {'allow_group_creation': True}, format='json')
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_le_coordinateur_peut_resserrer(self):
        self.en(self.chef)
        r = self.client.patch('/api/comms/pilotage/', {'allow_member_invite': False}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        cfg = MessagingPilotageConfig.objects.get(daara=self.daara)
        # Partie des valeurs GLOBALES, pas des valeurs par défaut (tout ouvert).
        self.assertFalse(cfg.allow_group_creation)
        self.assertFalse(get_effective_config(self.member).allow_member_invite)


class DestinatairesTests(ControleBase):
    def setUp(self):
        super().setUp()
        self.bloque = self.compte('bloque@test.sn', User.Role.MEMBER, User.Status.BLOCKED)
        self.attente = self.compte('attente@test.sn', User.Role.MEMBER, User.Status.PENDING)

    def test_annonce_critique(self):
        self.en(self.admin)
        r = self.client.post('/api/comms/announcements/', {'title': 'Alerte', 'content': 'x', 'urgency': 'critical'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        self.assertTrue(Notification.objects.filter(user=self.member).exists())
        self.assertFalse(Notification.objects.filter(user__in=[self.bloque, self.attente]).exists())

    def test_recherche_en_messagerie(self):
        self.en(self.member)
        for nom in ('bloque', 'attente'):
            self.assertEqual(self.client.get(f'/api/comms/search-members/?q={nom}').data, [])

    def test_administrateur_bloque(self):
        admin_bloque = self.compte('ancien-admin@test.sn', User.Role.ADMIN, User.Status.BLOCKED)
        self.assertNotIn(admin_bloque, administrateurs())

    def test_createsuperuser_nait_actif(self):
        su = User.objects.create_superuser(email='su@test.sn', password='Motdepasse123!')
        self.assertEqual(su.status, User.Status.ACTIVE)


class TachesTests(ControleBase):
    def setUp(self):
        super().setUp()
        self.campaign = Campaign.objects.create(
            name='Magal', deadline=timezone.localdate() + timezone.timedelta(days=10),
            status=Campaign.Status.ACTIVE, created_by=self.admin, organizer=self.member,
        )

    def creer(self, user):
        self.en(user)
        return self.client.post('/api/events/campaign-todos/', {'campaign': self.campaign.id, 'title': 'Tentes'}, format='json')

    def test_organisateur_et_coordinateur_creent(self):
        self.assertEqual(self.creer(self.member).status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.creer(self.chef).status_code, status.HTTP_201_CREATED)

    def test_un_autre_membre_ne_cree_pas(self):
        self.assertEqual(self.creer(self.member2).status_code, status.HTTP_403_FORBIDDEN)

    def test_lancer_un_ndiguel_reste_reserve_a_l_admin(self):
        self.en(self.chef)
        r = self.client.post('/api/events/campaigns/', {'name': 'X', 'deadline': '2027-01-01'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_une_tache_ne_change_pas_de_ndiguel(self):
        autre = Campaign.objects.create(name='Autre', deadline=self.campaign.deadline, status='active', created_by=self.admin)
        tache = CampaignTodo.objects.create(campaign=self.campaign, title='x')
        self.en(self.member)
        r = self.client.patch(f'/api/events/campaign-todos/{tache.id}/', {'campaign': autre.id}, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        tache.refresh_from_db()
        self.assertEqual(tache.campaign_id, self.campaign.id)


class InvitationsTests(ControleBase):
    def tete_a_tete(self):
        return Chat.objects.filter(chat_type='direct', memberships__user=self.member).filter(
            memberships__user=self.member2).distinct().count()

    def test_invitation_croisee_refusee(self):
        self.en(self.member)
        self.assertEqual(self.client.post('/api/comms/invitations/', {'recipient': self.member2.id}, format='json').status_code, 201)
        self.en(self.member2)
        r = self.client.post('/api/comms/invitations/', {'recipient': self.member.id}, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_accepter_rouvre_le_tete_a_tete_existant(self):
        self.en(self.member)
        r = self.client.post('/api/comms/invitations/', {'recipient': self.member2.id}, format='json')
        # Entre-temps, un tête-à-tête s'est ouvert par un autre chemin.
        chat = Chat.objects.create(chat_type='direct', created_by=self.member2)
        ChatMembership.objects.create(chat=chat, user=self.member)
        ChatMembership.objects.create(chat=chat, user=self.member2)
        self.en(self.member2)
        self.client.post(f"/api/comms/invitations/{r.data['id']}/accept/")
        self.assertEqual(self.tete_a_tete(), 1)
        self.assertEqual(ChatInvitation.objects.get(pk=r.data['id']).chat_id, chat.id)


class SalonQuitteTests(ControleBase):
    def salon(self):
        salon = Chat.objects.create(chat_type='group', name='Salon', created_by=self.member)
        ChatMembership.objects.create(chat=salon, user=self.member, is_moderator=True)
        ChatMembership.objects.create(chat=salon, user=self.member2)
        return salon

    def quitter(self, user, salon):
        self.en(user)
        return self.client.post(f'/api/comms/{salon.id}/leave/')

    def test_le_dernier_moderateur_passe_la_main(self):
        salon = self.salon()
        self.quitter(self.member, salon)
        self.assertTrue(ChatMembership.objects.get(chat=salon, user=self.member2).is_moderator)

    def test_le_dernier_parti_archive_le_salon_et_l_admin_le_retrouve(self):
        salon = self.salon()
        self.quitter(self.member2, salon)
        self.quitter(self.member, salon)
        salon.refresh_from_db()
        self.assertIsNotNone(salon.archived_at)
        self.en(self.admin)
        data = self.client.get('/api/comms/?archives=1').data
        ids = [c['id'] for c in (data['results'] if isinstance(data, dict) else data)]
        self.assertIn(salon.id, ids)
        r = self.client.post(f'/api/comms/{salon.id}/archive/', {'archived': False}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    def test_un_membre_ne_voit_pas_les_salons_orphelins(self):
        salon = Chat.objects.create(chat_type='group', name='Orphelin', created_by=self.member)
        self.en(self.member)
        data = self.client.get('/api/comms/').data
        ids = [c['id'] for c in (data['results'] if isinstance(data, dict) else data)]
        self.assertNotIn(salon.id, ids)
