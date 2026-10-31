"""La fenêtre de collecte des éditions : jamais deux éditions ouvertes ensemble.

Constaté sur la démo le 2026-10-31 : le Toggou passé de 1/1 à 6/0 gardait son
édition du 30 octobre ouverte jusqu'au 31, et celle du 6 novembre, calculée
avec la nouvelle règle, s'ouvrait le 31 — deux Toggou « En cours » le même jour.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from events.editions import creer_edition
from events.models import Campaign, Fete

User = get_user_model()


class FenetreBase(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email='admin@test.sn', password='Motdepasse123!', role=User.Role.ADMIN, status=User.Status.ACTIVE,
        )
        self.client.force_authenticate(self.admin)
        self.jour = timezone.localdate()
        # Le « vendredi » de la série tombe hier : l'édition de cette semaine
        # (1/1) court d'avant-hier à aujourd'hui.
        self.ref = self.jour - timedelta(days=1)
        self.fete = Fete.objects.create(
            name='Toggou', date=self.ref, recurrence=Fete.Recurrence.WEEKLY,
            ouvre_jours_avant=1, ferme_jours_apres=1,
        )

    def edition(self, occurrence, ouverture, cloture):
        return Campaign.objects.create(
            name=f'Toggou {occurrence}', fete=self.fete, occurrence_date=occurrence,
            opens_on=ouverture, deadline=cloture, status=Campaign.Status.ACTIVE,
        )

    def fenetre(self, ouvre, ferme):
        return self.client.patch(
            f'/api/events/fetes/{self.fete.id}/',
            {'ouvre_jours_avant': ouvre, 'ferme_jours_apres': ferme}, format='json',
        )


class RealignementTests(FenetreBase):
    def test_passer_a_6_0_ne_fait_pas_chevaucher_les_editions(self):
        cette_semaine = self.edition(self.ref, self.ref - timedelta(days=1), self.jour)
        prochaine = self.edition(self.ref + timedelta(days=7), self.ref + timedelta(days=6), self.ref + timedelta(days=8))
        self.assertEqual(self.fenetre(6, 0).status_code, status.HTTP_200_OK)
        cette_semaine.refresh_from_db()
        prochaine.refresh_from_db()
        # L'édition ouverte garde ses dates (des Jëfs y sont peut-être déjà).
        self.assertEqual((cette_semaine.opens_on, cette_semaine.deadline), (self.ref - timedelta(days=1), self.jour))
        # La suivante prend la semaine pleine, à partir du lendemain de la clôture.
        self.assertEqual(prochaine.opens_on, self.jour + timedelta(days=1))
        self.assertEqual(prochaine.deadline, self.ref + timedelta(days=7))

    def test_une_edition_deja_ouverte_qui_chevauche_est_repoussee(self):
        self.edition(self.ref, self.ref - timedelta(days=1), self.jour)
        chevauchante = self.edition(self.ref + timedelta(days=7), self.jour, self.ref + timedelta(days=7))
        self.fenetre(6, 0)
        chevauchante.refresh_from_db()
        self.assertEqual(chevauchante.opens_on, self.jour + timedelta(days=1))

    def test_resserrer_la_fenetre_ne_referme_pas_une_collecte_en_cours(self):
        self.fete.ouvre_jours_avant, self.fete.ferme_jours_apres = 6, 0
        self.fete.save()
        en_cours = self.edition(self.ref + timedelta(days=3), self.jour - timedelta(days=2), self.ref + timedelta(days=3))
        self.fenetre(1, 1)
        en_cours.refresh_from_db()
        self.assertEqual(en_cours.opens_on, self.jour - timedelta(days=2))

    def test_les_editions_closes_ne_bougent_pas(self):
        close = self.edition(self.ref - timedelta(days=7), self.ref - timedelta(days=8), self.ref - timedelta(days=6))
        self.fenetre(6, 0)
        close.refresh_from_db()
        self.assertEqual((close.opens_on, close.deadline), (self.ref - timedelta(days=8), self.ref - timedelta(days=6)))

    def test_une_nouvelle_edition_ouvre_apres_la_precedente(self):
        self.edition(self.ref, self.ref - timedelta(days=1), self.jour + timedelta(days=2))
        self.fete.ouvre_jours_avant, self.fete.ferme_jours_apres = 6, 0
        self.fete.save()
        suivante, creee = creer_edition(self.fete, self.ref + timedelta(days=7))
        self.assertTrue(creee)
        self.assertEqual(suivante.opens_on, self.jour + timedelta(days=3))


class BorneTests(FenetreBase):
    def test_une_semaine_pleine_est_acceptee(self):
        self.assertEqual(self.fenetre(6, 0).status_code, status.HTTP_200_OK)

    def test_une_collecte_plus_longue_que_la_periode_est_refusee(self):
        r = self.fenetre(6, 1)
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('déborderait', str(r.data))

    def test_la_borne_suit_la_recurrence(self):
        r = self.client.patch(
            f'/api/events/fetes/{self.fete.id}/',
            {'recurrence': Fete.Recurrence.QUARTERLY, 'ouvre_jours_avant': 60, 'ferme_jours_apres': 30},
            format='json',
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
