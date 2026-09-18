# -*- coding: utf-8 -*-
"""
L'annuaire, vu depuis la collecte physique.

Un collecteur est debout devant quelqu'un et doit décider, en quelques
secondes, laquelle des lignes à l'écran est la personne qui lui tend son
versement. Se tromper impute le don au mauvais compte — et un don mal imputé
ne se voit pas : il s'affiche normalement, du côté de celui qui n'a rien donné.

Sur les 31 membres de la base de démonstration, 4 portent un nom partagé
(« Souleymane Sy » et « Babacar Cissé »), soit 12 %.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import LDD, Daara

User = get_user_model()


class AnnuaireRechercheTests(TestCase):
    """Ce sur quoi la recherche de l'annuaire porte."""

    def setUp(self):
        self.client = APIClient()
        zone = LDD.objects.create(code='DS S15', name='CASAMANCE MIDADI')
        self.kande = Daara.objects.create(name='KANDE', ldd=zone)
        self.thionck = Daara.objects.create(name='THIONCK ESSYL', ldd=zone)

        # Deux homonymes, comme en base réelle.
        self.sy_kande = User.objects.create_user(
            email='sy1@test.sn', password='x', first_name='Souleymane',
            last_name='Sy', phone='+221779000002', role=User.Role.MEMBER,
            daara=self.kande,
        )
        self.sy_thionck = User.objects.create_user(
            email='sy2@test.sn', password='x', first_name='Souleymane',
            last_name='Sy', phone='+221779000021', role=User.Role.MEMBER,
            daara=self.thionck,
        )
        self.collecteur = User.objects.create_user(
            email='collecteur@test.sn', password='x', first_name='Modou',
            last_name='Fall', role=User.Role.COLLECTOR, daara=self.kande,
        )
        self.client.force_authenticate(self.collecteur)
        self.url = reverse('directory-users-list')

    def _ids(self, **params):
        r = self.client.get(self.url, params)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        rows = r.data if isinstance(r.data, list) else r.data['results']
        return {row['id'] for row in rows}

    def test_recherche_par_daara(self):
        """🔴 CHERCHER « KANDE » RENDAIT ZÉRO RÉSULTAT.

        `search_fields` couvrait le nom, l'e-mail et le téléphone, mais pas le
        Daara — alors que `UserManagementViewSet`, côté administration, le
        cherchait déjà. Or le Daara est la SEULE chose dont un collecteur soit
        certain face à deux homonymes : c'est là qu'il collecte.
        """
        trouves = self._ids(search='KANDE')
        self.assertIn(self.sy_kande.id, trouves)
        self.assertNotIn(self.sy_thionck.id, trouves)

    def test_le_nom_seul_ne_departage_pas(self):
        """Constat de départ : le nom rend bien les deux homonymes."""
        self.assertEqual(
            self._ids(search='Souleymane'),
            {self.sy_kande.id, self.sy_thionck.id},
        )

    def test_recherche_par_telephone_toujours_operante(self):
        self.assertEqual(self._ids(search='779000021'), {self.sy_thionck.id})

    def test_champs_d_identification_servis(self):
        """Le Daara et le téléphone DOIVENT accompagner chaque ligne.

        Ils étaient déjà rendus ; ce test empêche qu'un allègement du
        sérialiseur ne les retire sans qu'on voie le rapport avec la collecte.
        """
        r = self.client.get(self.url, {'search': 'Souleymane'})
        rows = r.data if isinstance(r.data, list) else r.data['results']
        self.assertTrue(rows)
        for row in rows:
            self.assertIn('daara_name', row)
            self.assertIn('phone', row)
            self.assertTrue(row['daara_name'], "le Daara ne peut pas être vide")

    def test_daaras_distincts_pour_deux_homonymes(self):
        """Ce qui rend la distinction possible à l'écran."""
        r = self.client.get(self.url, {'search': 'Souleymane Sy'})
        rows = r.data if isinstance(r.data, list) else r.data['results']
        daaras = {row['daara_name'] for row in rows}
        self.assertEqual(daaras, {'KANDE', 'THIONCK ESSYL'})
