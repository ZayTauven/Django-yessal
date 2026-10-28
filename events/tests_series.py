"""Séries et éditions des Ndiguels (chantier « gestion des Ndiguels », lot 2).

Le Toggou Adiouma est le cas le plus contraignant : chaque vendredi, une
édition distincte, créée sans geste humain, ouverte du jeudi au samedi pour
la diaspora, annoncée une seule fois. Le Magal (18 Safar) est lunaire : sa
date est estimée, puis confirmée par l'administration.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from comms.models import Notification
from events import editions
from events.models import Campaign, Fete
from events.occurrences import depuis_hegire, prochaine, vers_hegire

User = get_user_model()
VENDREDI = date(2026, 10, 2)        # référence du Toggou en base de démo
MERCREDI_28 = date(2026, 10, 28)


def toggou(**champs):
    base = dict(
        name='Toggou Adiouma', date=VENDREDI, recurrence=Fete.Recurrence.WEEKLY,
        description='Rassemblement du vendredi.', auto_editions=True,
        ouvre_jours_avant=1, ferme_jours_apres=1, objectif_par_defaut=Decimal('50000'),
    )
    base.update(champs)
    return Fete.objects.create(**base)


class OccurrencesTests(TestCase):
    def test_hebdomadaire(self):
        f = Fete(date=VENDREDI, recurrence=Fete.Recurrence.WEEKLY)
        self.assertEqual(prochaine(f, MERCREDI_28), date(2026, 10, 30))
        self.assertEqual(prochaine(f, date(2026, 10, 30)), date(2026, 10, 30))
        self.assertEqual(prochaine(f, date(2026, 9, 1)), VENDREDI)

    def test_trimestrielle_garde_le_jour(self):
        f = Fete(date=date(2026, 1, 31), recurrence=Fete.Recurrence.QUARTERLY)
        self.assertEqual(prochaine(f, date(2026, 2, 1)), date(2026, 4, 30))
        self.assertEqual(prochaine(f, date(2026, 5, 1)), date(2026, 7, 31))

    def test_annuelle_et_non_recurrente(self):
        self.assertEqual(prochaine(Fete(date=date(2026, 4, 17), recurrence='annual'), MERCREDI_28), date(2027, 4, 17))
        self.assertIsNone(prochaine(Fete(date=VENDREDI, recurrence='none'), MERCREDI_28))

    def test_hegirien_a_un_jour_pres_des_magal_reels(self):
        # Magal 2025 le 13 août, Magal 2024 le 23 août (18 Safar 1447 et 1446).
        self.assertEqual(depuis_hegire(1447, 2, 18), date(2025, 8, 13))
        self.assertLessEqual(abs((depuis_hegire(1446, 2, 18) - date(2024, 8, 23)).days), 1)
        for d in (date(2020, 1, 1), date(2026, 10, 30), date(2031, 12, 31)):
            self.assertEqual(depuis_hegire(*vers_hegire(d)), d)

    def test_magal_ne_derive_pas(self):
        f = Fete(recurrence=Fete.Recurrence.ANNUAL_HIJRI, hijri_mois=2, hijri_jour=18)
        self.assertEqual(prochaine(f, MERCREDI_28), date(2027, 7, 23))
        self.assertEqual(prochaine(f, date(2027, 8, 1)), date(2028, 7, 11))


class GenerationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email='a@t.sn', password='Motdepasse123!',
                                              role=User.Role.ADMIN, status=User.Status.ACTIVE)
        self.membre = User.objects.create_user(email='m@t.sn', password='Motdepasse123!',
                                               role=User.Role.MEMBER, status=User.Status.ACTIVE)

    def test_le_toggou_du_vendredi_est_cree_a_l_avance_une_seule_fois(self):
        f = toggou()
        self.assertEqual(editions.generer(date(2026, 10, 25)), [])  # trop tôt (ouverture jeudi 29, J-3 = lundi 26)
        creees = editions.generer(MERCREDI_28)
        self.assertEqual(len(creees), 1)
        e = creees[0]
        self.assertEqual((e.occurrence_date, e.opens_on, e.deadline),
                         (date(2026, 10, 30), date(2026, 10, 29), date(2026, 10, 31)))
        self.assertEqual(e.name, 'Toggou Adiouma — vendredi 30 octobre 2026')
        self.assertEqual((e.description, e.goal_amount), ('Rassemblement du vendredi.', Decimal('50000')))
        self.assertEqual(e.get_effective_status(MERCREDI_28), 'pending')
        self.assertEqual(e.get_effective_status(date(2026, 10, 29)), 'active')
        self.assertEqual(e.get_effective_status(date(2026, 11, 1)), 'completed')
        # Relancer, ou le lendemain : rien de plus.
        self.assertEqual(editions.generer(MERCREDI_28) + editions.generer(date(2026, 10, 29)), [])
        self.assertEqual(Campaign.objects.filter(fete=f).count(), 1)

    def test_un_serveur_arrete_ne_cree_pas_les_toggou_passes(self):
        f = toggou()
        editions.generer(date(2026, 10, 31))  # samedi : la fenêtre du 30 court encore
        editions.generer(date(2026, 11, 12))  # deux semaines plus tard
        dates = sorted(Campaign.objects.filter(fete=f).values_list('occurrence_date', flat=True))
        self.assertEqual(dates, [date(2026, 10, 30), date(2026, 11, 13)])

    def test_annonce_unique_sans_courriel(self):
        from django.core import mail

        toggou()
        editions.generer(MERCREDI_28)
        self.assertEqual(editions.annoncer(MERCREDI_28), [])  # pas encore ouverte
        self.assertEqual(len(editions.annoncer(date(2026, 10, 29))), 1)
        self.assertEqual(editions.annoncer(date(2026, 10, 30)), [])
        self.assertTrue(Notification.objects.filter(user=self.membre, title__contains='la collecte est ouverte').exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_le_magal_attend_la_confirmation(self):
        f = Fete.objects.create(name='Magal', date=date(2027, 7, 22), recurrence=Fete.Recurrence.ANNUAL_HIJRI,
                                hijri_mois=2, hijri_jour=18, auto_editions=True)
        self.assertEqual(editions.generer(date(2027, 3, 1)), [])
        e = editions.generer(date(2027, 5, 30))[0]
        self.assertTrue(e.a_confirmer)
        self.assertEqual(e.get_effective_status(date(2027, 7, 23)), 'pending')
        self.assertTrue(Notification.objects.filter(user=self.admin, title='Date à confirmer : Magal').exists())

        editions.confirmer_date(e, date(2027, 7, 22))
        e.refresh_from_db()
        self.assertEqual((e.a_confirmer, e.occurrence_date, e.name),
                         (False, date(2027, 7, 22), 'Magal — jeudi 22 juillet 2027'))
        self.assertEqual(f.hijri_jour, 18)


class SeriesApiTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email='a@t.sn', password='Motdepasse123!',
                                              role=User.Role.ADMIN, status=User.Status.ACTIVE, is_staff=True)
        self.membre = User.objects.create_user(email='m@t.sn', password='Motdepasse123!',
                                               role=User.Role.MEMBER, status=User.Status.ACTIVE)

    def test_preparer_la_prochaine_edition(self):
        f = toggou(auto_editions=False)
        self.client.force_authenticate(self.admin)
        url = f'/api/events/fetes/{f.pk}/prochaine-edition/'
        r1 = self.client.post(url)
        r2 = self.client.post(url)
        self.assertEqual((r1.status_code, r2.status_code), (201, 201))
        self.assertEqual(r2.data['occurrence_date'],
                         str(date.fromisoformat(r1.data['occurrence_date']) + timedelta(days=7)))
        self.client.force_authenticate(self.membre)
        self.assertEqual(self.client.post(url).status_code, 403)

    def test_une_edition_a_confirmer_est_invisible_et_refuse_les_jefs(self):
        f = Fete.objects.create(name='Magal', recurrence=Fete.Recurrence.ANNUAL_HIJRI,
                                hijri_mois=2, hijri_jour=18, auto_editions=True)
        e, _ = editions.creer_edition(f, date(2027, 7, 23))
        self.client.force_authenticate(self.membre)
        self.assertNotIn(e.pk, [c['id'] for c in self.client.get(reverse('campaigns-list')).data])
        self.assertEqual(self.client.get(reverse('campaigns-detail', args=[e.pk])).status_code, 404)
        r = self.client.post(reverse('donations-list'), {'campaign': e.pk, 'amount': 1000, 'payment_method': 'wave'},
                             format='json')
        self.assertEqual(r.status_code, 400)

        self.client.force_authenticate(self.admin)
        r = self.client.post(reverse('campaigns-confirmer-date', args=[e.pk]), {'date': '2027-07-22'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(r.data['a_confirmer'])

    def test_la_serie_donne_sa_prochaine_date(self):
        f = toggou()
        self.client.force_authenticate(self.membre)
        r = self.client.get(f'/api/events/fetes/{f.pk}/')
        self.assertEqual(r.data['date'], '2026-10-02')
        self.assertIsNotNone(r.data['prochaine_date'])
        self.assertGreaterEqual(r.data['days_until'], 0)

    def test_generation_sans_date_refusee(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post('/api/events/fetes/', {'name': 'X', 'recurrence': 'weekly', 'auto_editions': True},
                             format='json')
        self.assertEqual(r.status_code, 400)
