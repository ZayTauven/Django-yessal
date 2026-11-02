# -*- coding: utf-8 -*-
"""
Le pays de résidence, en code ISO (`core/pays.py`).

Le cas d'origine, 2026-11-02 : un texte libre où « Senegl » devenait un pays à
part de la répartition des Jëfs et comptait dans la part de la diaspora.
"""

import importlib

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework import status
from rest_framework.test import APIClient

from core.pays import PAYS, code_pays, est_diaspora, nom_pays

User = get_user_model()


class CodePaysTests(SimpleTestCase):
    def test_codes_et_noms_connus(self):
        for saisie in ('SN', 'sn', 'Sénégal', 'SENEGAL', ' senegal '):
            self.assertEqual(code_pays(saisie), 'SN', saisie)
        self.assertEqual(code_pays("Côte d’Ivoire"), 'CI')
        self.assertEqual(code_pays("cote d'ivoire"), 'CI')
        self.assertEqual(code_pays('Guinée-Bissau'), 'GW')
        self.assertEqual(code_pays('Italy'), 'IT')
        self.assertEqual(code_pays('USA'), 'US')

    def test_fautes_et_villes_refusees(self):
        for saisie in ('Senegl', 'Frence', 'Dakar', 'XX'):
            self.assertIsNone(code_pays(saisie), saisie)

    def test_chaque_nom_de_la_table_se_retrouve(self):
        for code, nom in PAYS:
            self.assertEqual(code_pays(nom), code, nom)
            self.assertEqual(nom_pays(code), nom)

    def test_diaspora(self):
        self.assertFalse(est_diaspora('SN'))
        self.assertFalse(est_diaspora(None))
        self.assertFalse(est_diaspora(''))
        self.assertTrue(est_diaspora('FR'))


class ProfilTests(TestCase):
    def setUp(self):
        self.membre = User.objects.create_user(email='m@ex.org', password='x', status=User.Status.ACTIVE)
        self.client = APIClient()
        self.client.force_authenticate(self.membre)

    def patch(self, valeur):
        return self.client.patch('/api/profile/', {'residence_country': valeur}, format='json')

    def test_code_enregistre_et_nom_rendu(self):
        r = self.patch('FR')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual((r.data['residence_country'], r.data['residence_country_name']), ('FR', 'France'))

    def test_un_nom_tape_par_une_ancienne_application_est_converti(self):
        r = self.patch('Sénégal')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.membre.refresh_from_db()
        self.assertEqual(self.membre.residence_country, 'SN')

    def test_une_faute_de_frappe_est_refusee(self):
        r = self.patch('Senegl')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(r.data['residence_country'], ['Choisissez votre pays dans la liste.'])

    def test_vider_le_champ(self):
        self.patch('FR')
        r = self.patch('')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIsNone(r.data['residence_country'])


class MigrationTests(TestCase):
    def test_les_anciennes_saisies_deviennent_des_codes(self):
        migration = importlib.import_module('accounts.migrations.0021_pays_de_residence_en_code_iso')
        saisies = {'a@ex.org': 'Sénégal', 'b@ex.org': 'Senegal', 'c@ex.org': 'États-Unis', 'd@ex.org': 'Senegl'}
        for email in saisies:
            User.objects.create_user(email=email, password='x')
        # Les valeurs d'avant la migration ne tiennent pas dans deux
        # caractères : SQLite, base des tests, ne borne pas la longueur.
        for email, valeur in saisies.items():
            User.objects.filter(email=email).update(residence_country=valeur)

        migration.vers_codes(apps, None)

        codes = dict(User.objects.filter(email__in=saisies).values_list('email', 'residence_country'))
        self.assertEqual(codes, {'a@ex.org': 'SN', 'b@ex.org': 'SN', 'c@ex.org': 'US', 'd@ex.org': None})
