"""« Les Jëfs du Kourel » : noms et montants au choix de l'admin, anonymat respecté.

Demande du 2026-10-31 :
  · « Afficher les montants » restait sans effet : le montant n'existait que
    sur les cartes dont le donateur avait coché une case que presque personne
    ne cochait ;
  · l'admin choisit désormais de nommer les donateurs, et d'afficher leurs
    montants — jamais pour un Jëf anonyme ;
  · un seul choix pour le donateur : « Rester anonyme » ;
  · l'admin et Sangue Bi voient le vrai nom d'un anonyme, étiqueté.
"""

from django.utils import timezone
from rest_framework import status

from accounts.models import User
from comms.canaux import canal
from comms.models import Chat, Message
from comms.tests_gestion_canaux import Base
from contributions.models import Donation
from events.models import Campaign


class JefsBase(Base):
    def setUp(self):
        super().setUp()
        self.ndiguel = Campaign.objects.create(
            name='Magal', status=Campaign.Status.ACTIVE,
            deadline=timezone.localdate() + timezone.timedelta(days=5), created_by=self.admin,
        )
        self.jefs = canal('jefs')

    def reglages(self, noms, montants):
        Chat.objects.filter(pk=self.jefs.pk).update(show_names=noms, show_amounts=montants)

    def jef_confirme(self, **champs):
        champs.setdefault('public_in_channel', True)
        with self.captureOnCommitCallbacks(execute=True):
            don = Donation.objects.create(
                campaign=self.ndiguel, donor=self.awa, amount=25000,
                payment_status=Donation.PaymentStatus.CONFIRMED, **champs,
            )
        return Message.objects.get(chat=self.jefs, payload__donation_id=don.pk)

    def lu(self, carte):
        r = self.client.get(f'/api/comms/messages/?chat={self.jefs.pk}')
        return next(m for m in r.json() if m['id'] == carte.pk)


class CarteTests(JefsBase):
    def test_sans_les_noms_la_carte_reste_discrete(self):
        self.reglages(noms=False, montants=True)
        m = self.lu(self.jef_confirme())
        self.assertNotIn('Awa', m['content'])
        self.assertNotIn('donor', m['payload'])
        self.assertNotIn('amount', m['payload'])
        self.assertNotIn('text_named', m['payload'])

    def test_les_noms_puis_les_montants(self):
        carte = self.jef_confirme()
        self.reglages(noms=True, montants=False)
        m = self.lu(carte)
        self.assertIn('Awa', m['content'])
        self.assertEqual(m['payload']['donor']['name'], 'Awa')
        self.assertNotIn('amount', m['payload'])
        self.reglages(noms=True, montants=True)
        self.assertEqual(self.lu(carte)['payload']['amount'], 25000)

    def test_un_jef_anonyme_ne_montre_jamais_ni_nom_ni_montant(self):
        carte = self.jef_confirme(is_anonymous=True)
        self.assertNotIn('text_named', carte.payload)
        self.assertNotIn('amount', carte.payload)
        self.reglages(noms=True, montants=True)
        m = self.lu(carte)
        self.assertNotIn('Awa', m['content'])
        self.assertNotIn('donor', m['payload'])
        self.assertNotIn('amount', m['payload'])

    def test_un_ancien_jef_sans_consentement_reste_discret(self):
        carte = self.jef_confirme(public_in_channel=False)
        self.reglages(noms=True, montants=True)
        self.assertNotIn('Awa', self.lu(carte)['content'])

    def test_le_reglage_des_noms_se_fait_dans_les_parametres(self):
        r = self.client.patch(f'/api/comms/{self.jefs.pk}/settings/', {'show_names': False}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertFalse(r.json()['show_names'])


class CreationDuJefTests(JefsBase):
    def creer(self, **corps):
        self.client.force_authenticate(self.awa)
        r = self.client.post('/api/contributions/', {
            'campaign': self.ndiguel.id, 'amount': 5000, 'payment_method': 'wave', **corps,
        }, format='json')
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.content)
        return Donation.objects.get(pk=r.json()['id'])

    def test_un_jef_non_anonyme_peut_etre_nomme(self):
        self.assertTrue(self.creer().public_in_channel)

    def test_un_jef_anonyme_ne_peut_pas_l_etre(self):
        self.assertFalse(self.creer(is_anonymous=True).public_in_channel)

    def test_l_ancienne_application_garde_sa_promesse(self):
        # L'application déjà installée envoie encore la case, décochée.
        self.assertFalse(self.creer(public_in_channel=False).public_in_channel)


class EtiquetteAnonymeTests(JefsBase):
    def setUp(self):
        super().setUp()
        self.jef_confirme(is_anonymous=True)

    def ligne(self, user):
        self.client.force_authenticate(user)
        return self.client.get(f'/api/events/campaigns/{self.ndiguel.id}/etat/').json()['contributions'][0]

    def test_l_admin_et_sangue_bi_voient_le_nom_etiquete(self):
        guide = User.objects.create_user(email='guide@ex.org', password='x', role=User.Role.GUIDE,
                                         status=User.Status.ACTIVE)
        for user in (self.admin, guide):
            ligne = self.ligne(user)
            self.assertIn('Awa', ligne['member_name'])
            self.assertTrue(ligne['anonymous_revealed'])

    def test_les_autres_ne_voient_qu_un_contributeur_anonyme(self):
        ligne = self.ligne(self.modou)
        self.assertEqual(ligne['member_name'], 'Contributeur anonyme')
        self.assertIsNone(ligne['member_id'])
        self.assertFalse(ligne['anonymous_revealed'])


class ListesDeJefsTests(JefsBase):
    """« Les Jëfs » et le Suivi : l'anonymat tenu par le serveur, étiqueté à l'écran."""

    def setUp(self):
        super().setUp()
        self.jef_confirme(is_anonymous=True)
        self.chef = User.objects.create_user(
            email='chef@ex.org', password='x', role=User.Role.CHEF_DAARA,
            status=User.Status.ACTIVE, daara=self.kande,
        )

    def liste(self, user, **params):
        self.client.force_authenticate(user)
        data = self.client.get('/api/contributions/', params).json()
        return data['results'] if isinstance(data, dict) else data

    def test_le_coordinateur_ne_lit_plus_le_nom_d_un_anonyme(self):
        ligne = self.liste(self.chef)[0]
        self.assertEqual(ligne['donor_name'], 'Contributeur anonyme')
        self.assertIsNone(ligne['donor'])
        self.assertIsNone(ligne['donor_phone'])
        self.assertTrue(ligne['is_anonymous'])

    def test_l_admin_lit_le_nom_avec_l_indicateur(self):
        ligne = self.liste(self.admin)[0]
        self.assertEqual(ligne['donor_name'], 'Awa')
        self.assertTrue(ligne['is_anonymous'])

    def test_une_recherche_par_nom_ne_trahit_pas_un_anonyme(self):
        self.assertEqual(self.liste(self.chef, search='Awa'), [])
        self.assertEqual(len(self.liste(self.admin, search='Awa')), 1)

    def test_le_suivi_signale_l_anonymat(self):
        self.client.force_authenticate(self.admin)
        recent = self.client.get('/api/suivi/').json()['recent']
        self.assertTrue(recent[0]['is_anonymous'])
        self.assertEqual(recent[0]['donor']['name'], 'Awa')
