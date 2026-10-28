"""Dates des Ndiguels et planificateur : c'est le serveur qui sait quel jour on est.

Chantier « gestion des Ndiguels » (2026-10-28), lot 1 :
  · la date d'ouverture fait passer un Ndiguel d'« À venir » à « En cours »,
    sans geste à la main ;
  · le jour est celui de Dakar, jamais celui de l'appareil du membre ;
  · une tâche planifiée tourne au plus une fois par jour, rattrapée le jour
    même si le serveur redémarre, jamais rejouée le lendemain.
"""

from datetime import date, datetime, time, timedelta
from unittest.mock import Mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from events.management.commands.planificateur import Tache, executer_si_du
from events.models import Campaign, ExecutionPlanifiee

User = get_user_model()
JOUR = date(2026, 10, 30)  # un vendredi


def dakar(jour, h, m=0):
    return timezone.make_aware(datetime.combine(jour, time(h, m)))


class StatutParLesDatesTests(TestCase):
    def ndiguel(self, **champs):
        base = {'name': 'Toggou Adiouma', 'deadline': JOUR, 'status': Campaign.Status.ACTIVE}
        base.update(champs)
        return Campaign(**base)

    def test_le_fuseau_de_reference_est_dakar(self):
        self.assertEqual(settings.TIME_ZONE, 'Africa/Dakar')

    def test_ouverture_et_cloture_inclusives(self):
        c = self.ndiguel(opens_on=JOUR, status=Campaign.Status.PENDING)
        self.assertEqual(c.get_effective_status(JOUR - timedelta(days=1)), 'pending')
        self.assertEqual(c.get_effective_status(JOUR), 'active')
        self.assertEqual(c.get_effective_status(JOUR + timedelta(days=1)), 'completed')

    def test_la_main_l_emporte_sur_les_dates(self):
        for statut in (Campaign.Status.INACTIVE, Campaign.Status.COMPLETED):
            c = self.ndiguel(opens_on=JOUR, status=statut)
            self.assertEqual(c.get_effective_status(JOUR), statut)

    def test_sans_date_d_ouverture_rien_ne_change(self):
        self.assertEqual(self.ndiguel(status=Campaign.Status.PENDING).get_effective_status(JOUR), 'pending')
        self.assertEqual(self.ndiguel().get_effective_status(JOUR), 'active')


class DatesParLApiTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email='admin@test.sn', password='Motdepasse123!', role=User.Role.ADMIN,
            status=User.Status.ACTIVE,
        )
        self.client.force_authenticate(self.admin)
        self.url = reverse('campaigns-list')

    def test_ouverture_apres_cloture_refusee(self):
        demain = timezone.localdate() + timedelta(days=1)
        r = self.client.post(self.url, {
            'name': 'Toggou', 'deadline': str(demain), 'opens_on': str(demain + timedelta(days=1)),
            'status': 'active',
        }, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('opens_on', r.data)

    def test_le_serveur_compte_les_jours(self):
        aujourd_hui = timezone.localdate()
        r = self.client.post(self.url, {
            'name': 'Toggou', 'deadline': str(aujourd_hui + timedelta(days=9)),
            'opens_on': str(aujourd_hui + timedelta(days=3)), 'status': 'active',
        }, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data['effective_status'], r.data['days_until_open'], r.data['days_remaining']),
                         ('pending', 3, 9))


class PlanificateurTests(TestCase):
    tache = Tache('rappel_test', time(8, 0))

    def test_rien_avant_l_heure(self):
        lancer = Mock()
        self.assertFalse(executer_si_du(self.tache, dakar(JOUR, 7, 59), lancer))
        lancer.assert_not_called()

    def test_une_seule_fois_par_jour(self):
        lancer = Mock()
        self.assertTrue(executer_si_du(self.tache, dakar(JOUR, 8, 0), lancer))
        self.assertFalse(executer_si_du(self.tache, dakar(JOUR, 8, 1), lancer))
        self.assertFalse(executer_si_du(self.tache, dakar(JOUR, 23, 59), lancer))
        self.assertTrue(executer_si_du(self.tache, dakar(JOUR + timedelta(days=1), 8, 0), lancer))
        self.assertEqual(lancer.call_count, 2)

    def test_un_redemarrage_rattrape_le_jour_meme(self):
        lancer = Mock()
        executer_si_du(self.tache, dakar(JOUR, 8, 0), lancer)
        # Serveur arrêté jusqu'au surlendemain 10 h : on lance celle du jour,
        # sans rejouer la journée manquée.
        with self.assertLogs('events.management.commands.planificateur', 'WARNING'):
            self.assertTrue(executer_si_du(self.tache, dakar(JOUR + timedelta(days=2), 10, 0), lancer))
        self.assertEqual(lancer.call_count, 2)

    def test_un_echec_est_inscrit_et_pas_relance(self):
        lancer = Mock(side_effect=RuntimeError('SMTP muet'))
        with self.assertLogs('events.management.commands.planificateur', 'ERROR'):
            self.assertTrue(executer_si_du(self.tache, dakar(JOUR, 8, 0), lancer))
        suivi = ExecutionPlanifiee.objects.get(nom='rappel_test')
        self.assertEqual((suivi.dernier_resultat, suivi.dernier_message), ('echec', 'SMTP muet'))
        self.assertFalse(executer_si_du(self.tache, dakar(JOUR, 8, 1), lancer))
