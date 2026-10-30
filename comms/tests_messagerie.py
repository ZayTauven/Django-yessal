"""La messagerie : ce que la liste, le fil et les push promettent au membre.

Chaque classe correspond à un défaut constaté le 2026-10-29 avant la refonte :
une pastille qui comptait ses propres messages, une liste triée par date de
création, une citation qui perdait son texte, aucun push pour un message.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from comms.models import Chat, ChatMembership, Message, UserMessagingPreferences
from comms.services import SEUIL_GRAND_SALON, destinataires_push

User = get_user_model()


def creer(email, **extra):
    return User.objects.create_user(email=email, password='x', **extra)


def salon(*membres, chat_type=Chat.ChatType.GROUP, name='Salon'):
    chat = Chat.objects.create(chat_type=chat_type, name=name, created_by=membres[0])
    for m in membres:
        ChatMembership.objects.create(chat=chat, user=m, added_by=membres[0])
    return chat


class SansReseau(APITestCase):
    """Ni Pusher ni Firebase : on capture ce qui serait parti."""

    def setUp(self):
        self.pusher = mock.patch('comms.services.trigger_pusher').start()
        self.fcm = mock.patch('comms.services.send_fcm_push').start()
        # L'arrière-plan s'exécute sur place : les assertions voient le résultat.
        mock.patch('comms.services.lancer_en_arriere_plan', side_effect=lambda fn: fn()).start()
        self.addCleanup(mock.patch.stopall)


class NonLusTests(SansReseau):
    def setUp(self):
        super().setUp()
        self.awa = creer('awa@ex.org', first_name='Awa')
        self.modou = creer('modou@ex.org', first_name='Modou')
        self.chat = salon(self.awa, self.modou, chat_type=Chat.ChatType.DIRECT)

    def non_lus(self, user):
        self.client.force_authenticate(user)
        rows = self.client.get('/api/comms/').json()
        rows = rows['results'] if isinstance(rows, dict) else rows
        return next(r for r in rows if r['id'] == self.chat.id)['unread_count']

    def test_ses_propres_messages_ne_sont_pas_des_non_lus(self):
        self.client.force_authenticate(self.awa)
        self.client.post('/api/comms/messages/', {'chat': self.chat.id, 'content': 'Salam'})
        self.assertEqual(self.non_lus(self.awa), 0)
        self.assertEqual(self.non_lus(self.modou), 1)

    def test_les_notes_systeme_ne_comptent_pas(self):
        Message.objects.create(chat=self.chat, sender=self.awa, message_type='system', content='Démarrée')
        self.assertEqual(self.non_lus(self.modou), 0)


class OrdreDeLaListeTests(SansReseau):
    def test_la_conversation_active_passe_devant(self):
        awa = creer('awa@ex.org')
        ancien = salon(awa, name='Ancien')
        salon(awa, name='Récent mais muet')
        Message.objects.create(chat=ancien, sender=awa, content='On reprend')

        self.client.force_authenticate(awa)
        rows = self.client.get('/api/comms/').json()
        rows = rows['results'] if isinstance(rows, dict) else rows
        self.assertEqual(rows[0]['display_name'], 'Ancien')
        self.assertTrue(rows[0]['last_message']['is_mine'])


class FilEtCitationTests(SansReseau):
    def setUp(self):
        super().setUp()
        self.awa = creer('awa@ex.org', first_name='Awa', last_name='Ndiaye')
        self.modou = creer('modou@ex.org', first_name='Modou')
        self.chat = salon(self.awa, self.modou)
        self.client.force_authenticate(self.modou)

    def test_la_reponse_cite_auteur_et_texte(self):
        origine = Message.objects.create(chat=self.chat, sender=self.awa, content='Rendez-vous à 15h ?')
        r = self.client.post('/api/comms/messages/', {
            'chat': self.chat.id, 'content': 'Oui', 'reply_to': origine.id,
        })
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['reply_preview']['sender_name'], 'Awa Ndiaye')
        self.assertEqual(r.json()['reply_preview']['content'], 'Rendez-vous à 15h ?')

    def test_on_ne_repond_pas_a_un_message_d_un_autre_fil(self):
        ailleurs = salon(self.awa, self.modou, name='Autre')
        etranger = Message.objects.create(chat=ailleurs, sender=self.awa, content='x')
        r = self.client.post('/api/comms/messages/', {
            'chat': self.chat.id, 'content': 'Oui', 'reply_to': etranger.id,
        })
        self.assertEqual(r.status_code, 400)

    def test_un_message_vide_est_refuse(self):
        r = self.client.post('/api/comms/messages/', {'chat': self.chat.id, 'content': '   '})
        self.assertEqual(r.status_code, 400)

    @override_settings(MEDIA_ROOT='test_media_messagerie')
    def test_une_piece_jointe_part_sans_legende(self):
        fichier = SimpleUploadedFile('recu.pdf', b'%PDF-1.4 test', content_type='application/pdf')
        r = self.client.post(
            '/api/comms/messages/', {'chat': self.chat.id, 'file': fichier}, format='multipart',
        )
        self.assertEqual(r.status_code, 201, r.content)
        corps = r.json()
        self.assertEqual(corps['message_type'], 'file')
        self.assertTrue(corps['file_name'].startswith('recu'))
        self.assertFalse(corps['file_is_image'])
        Message.objects.get(pk=corps['id']).file.delete(save=False)

    def test_pages_en_remontant(self):
        ids = [Message.objects.create(chat=self.chat, sender=self.awa, content=f'm{i}').id for i in range(5)]
        page = self.client.get(f'/api/comms/messages/?chat={self.chat.id}&limit=2').json()
        self.assertEqual([m['id'] for m in page['results']], ids[3:])
        self.assertTrue(page['has_more'])

        avant = self.client.get(
            f'/api/comms/messages/?chat={self.chat.id}&limit=10&before={ids[3]}'
        ).json()
        self.assertEqual([m['id'] for m in avant['results']], ids[:3])
        self.assertFalse(avant['has_more'])

    def test_sans_limit_l_ancien_contrat_est_servi(self):
        Message.objects.create(chat=self.chat, sender=self.awa, content='a')
        corps = self.client.get(f'/api/comms/messages/?chat={self.chat.id}').json()
        self.assertIsInstance(corps, list)


class PushTests(SansReseau):
    def setUp(self):
        super().setUp()
        self.awa = creer('awa@ex.org', first_name='Awa')
        self.modou = creer('modou@ex.org', first_name='Modou')
        self.fatou = creer('fatou@ex.org', first_name='Fatou')

    def ecrire(self, chat, auteur, texte, reply_to=None):
        # Les envois partent après validation de la transaction (`publier`).
        with self.captureOnCommitCallbacks(execute=True):
            return Message.objects.create(chat=chat, sender=auteur, content=texte, reply_to=reply_to)

    def test_tete_a_tete_pousse_vers_l_autre_seulement(self):
        chat = salon(self.awa, self.modou, chat_type=Chat.ChatType.DIRECT)
        self.ecrire(chat, self.awa, 'Salam')
        self.assertEqual([c.args[0] for c in self.fcm.call_args_list], [self.modou.id])
        titre, corps = self.fcm.call_args.args[1:3]
        self.assertEqual((titre, corps), ('Awa', 'Salam'))
        self.assertEqual(self.fcm.call_args.kwargs['data']['url'], f'/dashboard/chat/{chat.id}')

    def test_la_sourdine_et_les_preferences_sont_respectees(self):
        chat = salon(self.awa, self.modou, self.fatou)
        ChatMembership.objects.filter(chat=chat, user=self.modou).update(is_muted=True)
        UserMessagingPreferences.objects.filter(user=self.fatou).update(notifications_enabled=False)
        self.ecrire(chat, self.awa, 'Bonjour')
        self.fcm.assert_not_called()

    def test_grand_salon_ne_pousse_que_reponses_et_mentions(self):
        foule = [creer(f'm{i}@ex.org', first_name=f'M{i}') for i in range(SEUIL_GRAND_SALON)]
        chat = salon(self.awa, self.modou, self.fatou, *foule)

        message = self.ecrire(chat, self.awa, 'Annonce générale')
        self.assertEqual(destinataires_push(message), [])

        origine = self.ecrire(chat, self.modou, 'Question')
        reponse = self.ecrire(chat, self.awa, 'Réponse', reply_to=origine)
        self.assertEqual(destinataires_push(reponse), [self.modou.id])

        mention = self.ecrire(chat, self.awa, 'Merci @fatou pour la liste')
        self.assertEqual(destinataires_push(mention), [self.fatou.id])

    def test_la_liste_de_chaque_membre_est_prevenue(self):
        chat = salon(self.awa, self.modou)
        self.ecrire(chat, self.awa, 'Salam')
        canaux = [c.args[0] for c in self.pusher.call_args_list if c.args[1] == 'chat-updated']
        self.assertEqual(sorted(canaux[0]), sorted([f'private-user.{self.awa.id}', f'private-user.{self.modou.id}']))


class SalonTests(SansReseau):
    def setUp(self):
        super().setUp()
        self.chef = creer('chef@ex.org', first_name='Chef', role=User.Role.CHEF_DAARA)
        self.awa = creer('awa@ex.org', first_name='Awa')
        self.modou = creer('modou@ex.org', first_name='Modou')
        self.chat = salon(self.chef, self.awa)

    def test_quitter_un_salon_laisse_une_trace(self):
        self.client.force_authenticate(self.awa)
        r = self.client.post(f'/api/comms/{self.chat.id}/leave/')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(ChatMembership.objects.filter(chat=self.chat, user=self.awa).exists())
        self.assertTrue(self.chat.messages.filter(message_type='system', content__contains='quitté').exists())

    def test_un_membre_simple_n_ajoute_personne(self):
        self.client.force_authenticate(self.awa)
        r = self.client.post(f'/api/comms/{self.chat.id}/add-members/', {'user_ids': [self.modou.id]}, format='json')
        self.assertEqual(r.status_code, 403)

    def test_le_coordinateur_ajoute(self):
        self.client.force_authenticate(self.chef)
        r = self.client.post(f'/api/comms/{self.chat.id}/add-members/', {'user_ids': [self.modou.id]}, format='json')
        self.assertEqual(r.json(), {'added': 1})
        self.assertTrue(ChatMembership.objects.filter(chat=self.chat, user=self.modou).exists())

    def test_la_sourdine_se_lit_dans_la_liste(self):
        self.client.force_authenticate(self.awa)
        self.client.post(f'/api/comms/{self.chat.id}/mute/')
        rows = self.client.get('/api/comms/').json()
        rows = rows['results'] if isinstance(rows, dict) else rows
        self.assertTrue(rows[0]['is_muted'])


class AdminsEntreEuxTests(SansReseau):
    """Deux administrateurs ne se trouvaient pas : la recherche et l'annuaire
    écartaient tout compte admin, quel que soit celui qui cherchait."""

    def setUp(self):
        super().setUp()
        self.fatou = creer('fatou@ex.org', first_name='Fatou', last_name='Diop', role=User.Role.ADMIN)
        self.ibou = creer('ibou@ex.org', first_name='Ibou', last_name='Diop', role=User.Role.ADMIN)
        self.guide = creer('guide@ex.org', first_name='Sokhna', last_name='Diop', role=User.Role.GUIDE)
        self.awa = creer('awa@ex.org', first_name='Awa', last_name='Diop')

    def ids_trouves(self, qui, q='Diop'):
        self.client.force_authenticate(qui)
        return {u['id'] for u in self.client.get('/api/comms/search-members/', {'q': q}).json()}

    def test_un_admin_trouve_un_autre_admin(self):
        self.assertEqual(self.ids_trouves(self.fatou), {self.ibou.id, self.guide.id, self.awa.id})

    def test_un_membre_ne_trouve_toujours_pas_les_admins(self):
        self.assertEqual(self.ids_trouves(self.awa), {self.guide.id})

    def test_l_annuaire_de_la_messagerie_porte_admins_et_guide(self):
        self.client.force_authenticate(self.fatou)
        ids = {u['id'] for u in self.client.get('/api/directory/users/', {'messagerie': '1'}).json()}
        self.assertTrue({self.ibou.id, self.guide.id, self.awa.id} <= ids)
        # Sans le paramètre, l'annuaire reste celui de la communauté.
        ids = {u['id'] for u in self.client.get('/api/directory/users/').json()}
        self.assertEqual(ids, {self.awa.id})

    def test_un_coordinateur_n_obtient_pas_les_admins_par_le_parametre(self):
        chef = creer('chef@ex.org', role=User.Role.CHEF_DAARA)
        self.client.force_authenticate(chef)
        ids = {u['id'] for u in self.client.get('/api/directory/users/', {'messagerie': '1'}).json()}
        self.assertNotIn(self.ibou.id, ids)

    def test_un_admin_ajoute_un_autre_admin_a_un_salon(self):
        chat = salon(self.fatou, self.awa)
        self.client.force_authenticate(self.fatou)
        r = self.client.post(f'/api/comms/{chat.id}/add-members/', {'user_ids': [self.ibou.id]}, format='json')
        self.assertEqual(r.json(), {'added': 1})


class TempsReelTests(SansReseau):
    """Ce que les événements Pusher portent, et quand ils partent."""

    def setUp(self):
        super().setUp()
        self.awa = creer('awa@ex.org', first_name='Awa')
        self.modou = creer('modou@ex.org', first_name='Modou')
        self.chat = salon(self.awa, self.modou)
        self.client.force_authenticate(self.modou)

    def evenements(self, nom):
        return [c.args for c in self.pusher.call_args_list if c.args[1] == nom]

    def test_rien_ne_part_avant_la_validation_de_la_transaction(self):
        with self.captureOnCommitCallbacks(execute=False) as rappels:
            Message.objects.create(chat=self.chat, sender=self.awa, content='Salam')
            self.assertEqual(self.evenements('new-message'), [])
        self.assertTrue(rappels)

    def test_la_bulle_optimiste_retrouve_son_message(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post('/api/comms/messages/', {'chat': self.chat.id, 'content': '', 'client_id': 'tmp-42',
                                                     'file': SimpleUploadedFile('a.txt', b'x')}, format='multipart')
        (canal, _, charge), = self.evenements('new-message')
        self.assertEqual(charge['client_id'], 'tmp-42')
        Message.objects.get(pk=charge['id']).file.delete(save=False)

    def test_une_reaction_publie_l_etat_complet(self):
        message = Message.objects.create(chat=self.chat, sender=self.awa, content='Salam')
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f'/api/comms/messages/{message.id}/react/', {'emoji': '🙏'}, format='json')
        charge = self.evenements('message-reaction')[-1][2]
        self.assertEqual(charge['reactions'], [{'emoji': '🙏', 'count': 1, 'user_ids': [self.modou.id]}])

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f'/api/comms/messages/{message.id}/react/', {'emoji': '🙏'}, format='json')
        self.assertEqual(self.evenements('message-reaction')[-1][2]['reactions'], [])

    def test_lu_sur_un_appareil_eteint_la_pastille_des_autres(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f'/api/comms/{self.chat.id}/read/')
        canaux = [args[0] for args in self.evenements('chat-read')]
        self.assertIn(f'private-user.{self.modou.id}', canaux)
        self.assertIn(f'private-chat.{self.chat.id}', canaux)

    def test_le_total_des_non_lus_ignore_la_sourdine(self):
        autre = salon(self.awa, self.modou, name='Autre')
        for chat in (self.chat, self.chat, autre):
            Message.objects.create(chat=chat, sender=self.awa, content='x')
        self.assertEqual(self.client.get('/api/comms/unread/').json(), {'total': 3, 'chats': 2})
        ChatMembership.objects.filter(chat=autre, user=self.modou).update(is_muted=True)
        self.assertEqual(self.client.get('/api/comms/unread/').json(), {'total': 2, 'chats': 1})
