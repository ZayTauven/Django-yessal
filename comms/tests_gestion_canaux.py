"""Gestion des canaux et salons par l'administration (arbitrages du 2026-10-30).

- un canal créé par l'admin s'adresse à tout le Kourel ou à une audience
  ciblée, et l'abonnement SUIT les membres ;
- un salon par titre suit son titre ; un ajout à la main n'est jamais retiré ;
- les montants sur « Les Jëfs du Kourel » : interrupteur admin, et seulement
  pour les Jëfs dont le donateur a accepté d'être nommé ;
- réglages : nom, description, icône, push, archivage, épingle ;
- les notifications d'un Jëf mènent à son reçu.
"""
from unittest import mock

from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import LDD, Daara, MemberTitle, User
from comms.canaux import canal
from comms.models import Chat, ChatMembership, Message, Notification
from comms.services import destinataires_push
from contributions.models import Donation
from events.models import Campaign


class Base(APITestCase):
    def setUp(self):
        mock.patch('comms.services.trigger_pusher').start()
        self.fcm = mock.patch('comms.services.send_fcm_push').start()
        mock.patch('comms.services.lancer_en_arriere_plan', side_effect=lambda fn: fn()).start()
        self.addCleanup(mock.patch.stopall)

        self.casa = LDD.objects.create(code='DS C', name='Casamance')
        self.kande = Daara.objects.create(name='Kandé', ldd=self.casa)
        self.police = Daara.objects.create(name='Police', ldd=LDD.objects.create(code='DS A', name='Armée'))
        self.serigne = MemberTitle.objects.create(name='Serigne')
        self.sokhna = MemberTitle.objects.create(name='Sokhna')
        actif = {'password': 'x', 'status': User.Status.ACTIVE}
        self.admin = User.objects.create_user(email='admin@ex.org', role=User.Role.ADMIN, **actif)
        self.awa = User.objects.create_user(
            email='awa@ex.org', first_name='Awa', daara=self.kande, title=self.serigne, **actif,
        )
        self.modou = User.objects.create_user(email='modou@ex.org', first_name='Modou', daara=self.police, **actif)
        self.client.force_authenticate(self.admin)

    def membres(self, chat):
        return set(ChatMembership.objects.filter(chat=chat).values_list('user_id', flat=True))

    def creer_canal(self, **corps):
        r = self.client.post('/api/comms/channels/', {'name': 'Programme du Magal', **corps}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        return Chat.objects.get(pk=r.json()['id'])


class CreationDeCanalTests(Base):
    def test_seul_l_admin_cree_un_canal(self):
        self.client.force_authenticate(self.awa)
        r = self.client.post('/api/comms/channels/', {'name': 'X'}, format='json')
        self.assertEqual(r.status_code, 403)

    def test_canal_pour_tout_le_kourel(self):
        chat = self.creer_canal(icon='calendar', push_enabled=False)
        self.assertEqual(chat.chat_type, Chat.ChatType.CHANNEL)
        self.assertEqual(self.membres(chat), {self.admin.pk, self.awa.pk, self.modou.pk})
        self.assertTrue(ChatMembership.objects.get(chat=chat, user=self.admin).can_post)
        self.assertFalse(chat.push_enabled)
        # Un nouveau compte y est abonné d'office.
        nouveau = User.objects.create_user(email='n@ex.org', password='x')
        self.assertIn(nouveau.pk, self.membres(chat))

    def test_icone_inconnue_refusee(self):
        r = self.client.post('/api/comms/channels/', {'name': 'X', 'icon': 'licorne'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_audience_ciblee_et_qui_suit_les_membres(self):
        chat = self.creer_canal(audience={'titles': [self.serigne.pk], 'ldds': [self.casa.pk]})
        # L'admin créateur reste membre (auteur) ; Awa, Serigne de Casamance, y entre.
        self.assertEqual(self.membres(chat), {self.admin.pk, self.awa.pk})
        # Modou reçoit le titre ET rejoint la LDD : il entre.
        self.modou.title = self.serigne
        self.modou.daara = self.kande
        self.modou.save()
        self.assertIn(self.modou.pk, self.membres(chat))
        # Awa perd son titre : elle sort.
        self.awa.title = self.sokhna
        self.awa.save(update_fields=['title'])
        self.assertNotIn(self.awa.pk, self.membres(chat))

    def test_audience_invalide_refusee(self):
        r = self.client.post('/api/comms/channels/', {'name': 'X', 'audience': {'titles': [99999]}}, format='json')
        self.assertEqual(r.status_code, 400)


class SalonParTitreTests(Base):
    def creer_salon(self):
        r = self.client.post('/api/comms/', {'name': 'Les Serigne', 'title_ids': [self.serigne.pk]}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        return Chat.objects.get(pk=r.json()['id'])

    def test_salon_qui_suit_le_titre(self):
        chat = self.creer_salon()
        self.assertEqual(chat.chat_type, Chat.ChatType.GROUP)
        self.assertEqual(self.membres(chat), {self.admin.pk, self.awa.pk})
        self.modou.title = self.serigne
        self.modou.save()
        self.assertIn(self.modou.pk, self.membres(chat))
        self.modou.title = None
        self.modou.save()
        self.assertNotIn(self.modou.pk, self.membres(chat))

    def test_un_ajout_a_la_main_n_est_jamais_retire(self):
        chat = self.creer_salon()
        r = self.client.post(f'/api/comms/{chat.pk}/add-members/', {'user_ids': [self.modou.pk]}, format='json')
        self.assertEqual(r.status_code, 200)
        self.modou.daara = self.kande
        self.modou.save()
        self.assertIn(self.modou.pk, self.membres(chat))

    def test_seul_l_admin_cree_un_salon_par_titre(self):
        chef = User.objects.create_user(
            email='chef@ex.org', password='x', role=User.Role.CHEF_DAARA, daara=self.kande,
            status=User.Status.ACTIVE,
        )
        self.client.force_authenticate(chef)
        r = self.client.post('/api/comms/', {'name': 'S', 'title_ids': [self.serigne.pk]}, format='json')
        self.assertEqual(r.status_code, 403)


class ReglagesTests(Base):
    def test_renommer_decrire_icone(self):
        chat = self.creer_canal()
        r = self.client.patch(
            f'/api/comms/{chat.pk}/settings/',
            {'name': 'Veillées', 'description': 'Le programme', 'icon': 'moon'}, format='json',
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()['display_name'], r.json()['icon']), ('Veillées', 'moon'))

    def test_un_membre_ne_regle_rien(self):
        chat = self.creer_canal()
        self.client.force_authenticate(self.awa)
        self.assertEqual(self.client.patch(f'/api/comms/{chat.pk}/settings/', {'name': 'X'}, format='json').status_code, 403)

    def test_canal_fondateur_ni_cible_ni_archive(self):
        jefs = canal('jefs')
        r = self.client.patch(f'/api/comms/{jefs.pk}/settings/', {'audience': {'titles': [self.serigne.pk]}}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.post(f'/api/comms/{jefs.pk}/archive/', {}, format='json').status_code, 400)

    def test_push_on_off(self):
        chat = self.creer_canal()
        message = Message.objects.create(chat=chat, sender=self.admin, content='Rappel')
        self.assertEqual(set(destinataires_push(message)), {self.awa.pk, self.modou.pk})
        self.client.patch(f'/api/comms/{chat.pk}/settings/', {'push_enabled': False}, format='json')
        message.chat.refresh_from_db()
        self.assertEqual(destinataires_push(message), [])

    def test_le_canal_des_jefs_reste_muet_d_origine(self):
        self.assertFalse(canal('jefs').push_enabled)
        self.assertTrue(canal('guide').push_enabled)

    def test_archiver_masque_et_ferme_puis_reactiver(self):
        chat = self.creer_canal()
        self.client.post(f'/api/comms/{chat.pk}/archive/', {'archived': True}, format='json')
        self.client.force_authenticate(self.awa)
        ids = [c['id'] for c in self.client.get('/api/comms/').json()]
        self.assertNotIn(chat.pk, ids)
        self.client.force_authenticate(self.admin)
        r = self.client.post('/api/comms/messages/', {'chat': chat.pk, 'content': 'Encore ?'})
        self.assertEqual(r.status_code, 403)
        self.assertIn(chat.pk, [c['id'] for c in self.client.get('/api/comms/?archives=1').json()])
        self.client.post(f'/api/comms/{chat.pk}/archive/', {'archived': False}, format='json')
        self.client.force_authenticate(self.awa)
        self.assertIn(chat.pk, [c['id'] for c in self.client.get('/api/comms/').json()])


class EpingleTests(Base):
    def test_epingler_par_l_admin_et_pas_par_un_membre(self):
        chat = self.creer_canal()
        message = Message.objects.create(chat=chat, sender=self.admin, content='Le règlement du Kourel')
        self.client.force_authenticate(self.awa)
        self.assertEqual(self.client.post(f'/api/comms/{chat.pk}/pin/', {'message_id': message.pk}, format='json').status_code, 403)
        self.client.force_authenticate(self.admin)
        r = self.client.post(f'/api/comms/{chat.pk}/pin/', {'message_id': message.pk}, format='json')
        self.assertEqual(r.json()['pinned_message']['preview'], 'Le règlement du Kourel')
        detail = self.client.get(f'/api/comms/{chat.pk}/').json()
        self.assertEqual(detail['pinned_message']['id'], message.pk)
        self.client.post(f'/api/comms/{chat.pk}/pin/', {'message_id': None}, format='json')
        self.assertIsNone(self.client.get(f'/api/comms/{chat.pk}/').json()['pinned_message'])

    def test_un_message_d_un_autre_fil_ne_s_epingle_pas(self):
        chat = self.creer_canal()
        ailleurs = Message.objects.create(chat=canal('guide'), sender=self.admin, content='Autre')
        r = self.client.post(f'/api/comms/{chat.pk}/pin/', {'message_id': ailleurs.pk}, format='json')
        self.assertEqual(r.status_code, 400)


class MontantsTests(Base):
    def jef(self, public):
        ndiguel = Campaign.objects.create(
            name='Magal', status=Campaign.Status.ACTIVE, deadline=timezone.localdate(), created_by=self.admin,
        )
        with self.captureOnCommitCallbacks(execute=True):
            d = Donation.objects.create(
                campaign=ndiguel, donor=self.awa, amount=25000, public_in_channel=public,
                payment_status=Donation.PaymentStatus.CONFIRMED,
            )
        return Message.objects.get(chat=canal('jefs'), payload__donation_id=d.pk)

    def payload(self, message):
        r = self.client.get(f'/api/comms/messages/?chat={message.chat_id}')
        return next(m['payload'] for m in r.json() if m['id'] == message.pk)

    def test_montant_masque_tant_que_le_canal_ne_l_affiche_pas(self):
        carte = self.jef(public=True)
        self.assertEqual(carte.payload['amount'], 25000)
        self.assertNotIn('amount', self.payload(carte))
        jefs = canal('jefs')
        self.client.patch(f'/api/comms/{jefs.pk}/settings/', {'show_amounts': True}, format='json')
        self.assertEqual(self.payload(carte)['amount'], 25000)

    def test_un_jef_anonyme_n_a_jamais_de_montant(self):
        carte = self.jef(public=False)
        self.assertNotIn('amount', carte.payload)
        Chat.objects.filter(pk=canal('jefs').pk).update(show_amounts=True)
        self.assertNotIn('amount', self.payload(carte))

    def test_l_interrupteur_n_existe_que_sur_le_canal_des_jefs(self):
        chat = self.creer_canal()
        r = self.client.patch(f'/api/comms/{chat.pk}/settings/', {'show_amounts': True}, format='json')
        self.assertEqual(r.status_code, 400)


class NotificationVersLeRecuTests(Base):
    def setUp(self):
        super().setUp()
        # Le signal a importé `send_fcm_push` à son chargement : c'est SA
        # référence qu'il faut remplacer.
        self.fcm = mock.patch('comms.signals.send_fcm_push').start()
    def test_le_lien_part_dans_le_push_et_dans_l_api(self):
        from comms.notify import notify
        notify(self.awa, code='paiement_confirme', titre='Jëf confirmé', message='…',
               courriel=False, url='/dashboard/donations/42')
        self.assertEqual(self.fcm.call_args.kwargs['data'], {'url': '/dashboard/donations/42'})
        self.client.force_authenticate(self.awa)
        r = self.client.get('/api/comms/notifications/')
        lignes = r.json() if isinstance(r.json(), list) else r.json()['results']
        self.assertEqual(lignes[0]['url'], '/dashboard/donations/42')

    def test_sans_lien_la_liste_des_notifications(self):
        Notification.objects.create(user=self.awa, title='T', message='M')
        self.assertEqual(self.fcm.call_args.kwargs['data'], {'url': '/dashboard/notifications'})
