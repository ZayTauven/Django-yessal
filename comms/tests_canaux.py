"""Les canaux du Kourel, les cartes de Jëf, la modification et la recherche.

Décisions du 2026-10-29 : deux canaux globaux, abonnement d'office ; dans
« Les Jëfs du Kourel » le nom n'apparaît qu'avec le consentement du donateur,
le montant jamais ; dans « Annonces de Sangue Bi » seuls des comptes désignés
publient.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Daara, LDD, MemberTitle
from comms.canaux import EMAIL_SYSTEME, PALIERS, canal, composer_carte, publier_jef
from comms.models import Chat, ChatMembership, Message
from comms.services import destinataires_push
from contributions.models import Donation
from events.models import Campaign, Fete

User = get_user_model()


class Base(APITestCase):
    def setUp(self):
        mock.patch('comms.services.trigger_pusher').start()
        mock.patch('comms.signals.trigger_pusher').start()
        self.fcm = mock.patch('comms.services.send_fcm_push').start()
        mock.patch('comms.services.lancer_en_arriere_plan', side_effect=lambda fn: fn()).start()
        self.addCleanup(mock.patch.stopall)

        self.daara = Daara.objects.create(name='Police', ldd=LDD.objects.create(code='SEN', name='Sénégal'))
        self.awa = User.objects.create_user(
            email='awa@ex.org', password='x', first_name='Awa', last_name='Ndiaye', daara=self.daara,
        )
        self.modou = User.objects.create_user(email='modou@ex.org', password='x', first_name='Modou')
        self.admin = User.objects.create_user(email='admin@ex.org', password='x', role=User.Role.ADMIN)
        fete = Fete.objects.create(name='Magal', date=timezone.now().date(), recurrence=Fete.Recurrence.ANNUAL)
        self.ndiguel = Campaign.objects.create(
            name='Magal 18 Safar', status=Campaign.Status.ACTIVE, deadline=timezone.now().date(),
            fete=fete, daara=self.daara, created_by=self.admin,
        )
        self.guide = canal('guide')
        self.jefs = canal('jefs')

    def jef(self, donor=None, **extra):
        with self.captureOnCommitCallbacks(execute=True):
            return Donation.objects.create(
                campaign=self.ndiguel, donor=donor or self.awa, amount=12345,
                payment_status=Donation.PaymentStatus.CONFIRMED, **extra,
            )


class AbonnementTests(Base):
    def test_les_deux_canaux_existent_et_tout_nouveau_membre_y_est_abonne(self):
        self.assertIsNotNone(self.guide)
        self.assertIsNotNone(self.jefs)
        for chat in (self.guide, self.jefs):
            self.assertTrue(ChatMembership.objects.filter(chat=chat, user=self.modou).exists())

    def test_on_ne_quitte_pas_un_canal(self):
        self.client.force_authenticate(self.modou)
        r = self.client.post(f'/api/comms/{self.guide.id}/leave/')
        self.assertEqual(r.status_code, 400)


class PublicationTests(Base):
    def test_un_membre_ne_publie_pas_dans_un_canal(self):
        self.client.force_authenticate(self.modou)
        r = self.client.post('/api/comms/messages/', {'chat': self.guide.id, 'content': 'Bonjour'})
        self.assertEqual(r.status_code, 403)

    def test_seul_un_admin_designe_un_auteur_et_l_auteur_publie(self):
        self.client.force_authenticate(self.modou)
        r = self.client.post(f'/api/comms/{self.guide.id}/authors/', {'user_id': self.modou.id}, format='json')
        self.assertEqual(r.status_code, 403)

        self.client.force_authenticate(self.admin)
        r = self.client.post(f'/api/comms/{self.guide.id}/authors/', {'user_id': self.modou.id}, format='json')
        self.assertEqual(r.json(), {'user_id': self.modou.id, 'can_post': True})

        self.client.force_authenticate(self.modou)
        r = self.client.post('/api/comms/messages/', {'chat': self.guide.id, 'content': 'Veillée ce soir'})
        self.assertEqual(r.status_code, 201, r.content)

    def test_personne_n_est_auteur_du_canal_des_jefs(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post(f'/api/comms/{self.jefs.id}/authors/', {'user_id': self.modou.id}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_une_annonce_du_guide_sonne_chez_tous_les_jefs_jamais(self):
        ChatMembership.objects.filter(chat=self.guide, user=self.modou).update(can_post=True)
        annonce = Message.objects.create(chat=self.guide, sender=self.modou, content='Veillée')
        self.assertIn(self.awa.id, destinataires_push(annonce))

        carte = self.jef()
        message = Message.objects.get(chat=self.jefs, payload__donation_id=carte.id)
        self.assertEqual(destinataires_push(message), [])


class CarteDeJefTests(Base):
    def test_anonyme_par_defaut_et_jamais_de_montant(self):
        don = self.jef()
        carte = Message.objects.get(chat=self.jefs, payload__donation_id=don.id)
        self.assertEqual(carte.message_type, Message.MessageType.CARD)
        self.assertNotIn('Awa', carte.content)
        self.assertIsNone(carte.payload['donor'])
        self.assertIn('Magal 18 Safar', carte.content)
        self.assertNotIn('12345', carte.content + str(carte.payload))
        self.assertNotIn('12 345', carte.content)
        self.assertEqual(carte.sender.email, EMAIL_SYSTEME)

    def test_le_nom_avec_le_consentement_du_donateur(self):
        self.awa.title = MemberTitle.objects.create(name='AMIR')
        self.awa.save()
        don = self.jef(public_in_channel=True)
        carte = Message.objects.get(chat=self.jefs, payload__donation_id=don.id)
        self.assertIn('Awa', carte.content)
        self.assertEqual(carte.payload['donor']['title'], 'AMIR')

    def test_une_seule_carte_par_jef(self):
        don = self.jef()
        with self.captureOnCommitCallbacks(execute=True):
            don.save()
        publier_jef(don)
        self.assertEqual(Message.objects.filter(chat=self.jefs, payload__donation_id=don.id).count(), 1)

    def test_un_jef_en_attente_ne_publie_rien(self):
        with self.captureOnCommitCallbacks(execute=True):
            Donation.objects.create(campaign=self.ndiguel, donor=self.awa, amount=500)
        self.assertFalse(Message.objects.filter(chat=self.jefs).exists())

    def test_les_cartes_varient(self):
        variantes = set()
        for pk in range(1, 13):
            don = Donation(pk=pk, campaign=self.ndiguel, donor=self.awa, amount=1)
            texte, payload = composer_carte(don, rang=3)
            variantes.add((payload['variant'], payload.get('picto'), payload.get('tone'), texte))
        self.assertGreater(len({v[1] for v in variantes}), 2)
        self.assertGreater(len({v[3] for v in variantes}), 1)

    def test_un_palier_ne_s_annonce_qu_une_fois_meme_publie_en_retard(self):
        dons = [self.jef() for _ in range(min(PALIERS))]
        paliers = Message.objects.filter(chat=self.jefs, payload__variant='palier')
        self.assertEqual(paliers.count(), 1)
        self.assertEqual(paliers.get().payload['donation_id'], dons[-1].id)

        # Publication rétroactive, dans le désordre : chaque Jëf garde son rang.
        Message.objects.filter(chat=self.jefs).delete()
        for don in reversed(dons):
            publier_jef(don)
        self.assertEqual(Message.objects.filter(chat=self.jefs, payload__variant='palier').count(), 1)

    def test_palier_et_diaspora(self):
        don = Donation(pk=7, campaign=self.ndiguel, donor=self.awa, amount=1)
        palier = min(PALIERS)
        texte, payload = composer_carte(don, rang=palier)
        self.assertEqual(payload['variant'], 'palier')
        self.assertIn(str(palier), texte)

        self.modou.residence_country = 'France'
        don = Donation(pk=8, campaign=self.ndiguel, donor=self.modou, amount=1)  # pk pair : variante diaspora
        self.assertEqual(composer_carte(don, rang=3)[1]['variant'], 'diaspora')


class ModificationEtRechercheTests(Base):
    def setUp(self):
        super().setUp()
        self.salon = Chat.objects.create(chat_type=Chat.ChatType.GROUP, name='Salon', created_by=self.awa)
        for u in (self.awa, self.modou):
            ChatMembership.objects.create(chat=self.salon, user=u)
        self.message = Message.objects.create(chat=self.salon, sender=self.awa, content='Rendez-vous à 15h')

    def test_on_corrige_son_propre_message(self):
        self.client.force_authenticate(self.awa)
        r = self.client.patch(f'/api/comms/messages/{self.message.id}/', {'content': 'Rendez-vous à 16h'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['content'], 'Rendez-vous à 16h')
        self.assertIsNotNone(r.json()['edited_at'])

    def test_on_ne_reecrit_pas_le_message_d_un_autre(self):
        self.client.force_authenticate(self.modou)
        r = self.client.patch(f'/api/comms/messages/{self.message.id}/', {'content': 'Piraté'}, format='json')
        self.assertEqual(r.status_code, 403)
        self.message.refresh_from_db()
        self.assertEqual(self.message.content, 'Rendez-vous à 15h')

    def test_recherche_dans_une_conversation(self):
        Message.objects.create(chat=self.salon, sender=self.modou, content='Les nattes sont prêtes')
        self.client.force_authenticate(self.modou)
        r = self.client.get(f'/api/comms/messages/?chat={self.salon.id}&q=nattes')
        self.assertEqual([m['content'] for m in r.json()], ['Les nattes sont prêtes'])

    def test_le_titre_accompagne_l_auteur(self):
        self.awa.title = MemberTitle.objects.create(name='DIEUWRIGNE')
        self.awa.save()
        self.client.force_authenticate(self.modou)
        r = self.client.get(f'/api/comms/messages/?chat={self.salon.id}')
        self.assertEqual(r.json()[0]['sender']['title'], 'DIEUWRIGNE')
