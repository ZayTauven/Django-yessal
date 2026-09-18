# -*- coding: utf-8 -*-
"""
Les zones territoriales : import du classeur, et CRUD.

Chaque cas de ce module correspond à un défaut CONSTATÉ sur les données réelles
du projet le 2026-09-18, pas à une hypothèse. Les références chiffrées données
dans les docstrings viennent d'une comparaison entre les deux fichiers de la
direction et le contenu de la base de démonstration.
"""

from io import BytesIO

import openpyxl
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from accounts.models import LDD, Daara
from accounts.services.ldd_import import ModeImport, importer, lire_feuille

User = get_user_model()


def classeur(lignes, *, entete=("DAARA", "LDD", "CODE LDD"), fusions=()):
    """Un classeur en mémoire, aussi proche que possible des vrais fichiers.

    `lignes` sont des tuples ; `fusions` des plages « A9:D9 » à fusionner, pour
    reproduire les lignes de totaux et les cellules de groupe.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(entete))
    for ligne in lignes:
        ws.append(list(ligne))
    for plage in fusions:
        ws.merge_cells(plage)
    flux = BytesIO()
    wb.save(flux)
    flux.seek(0)
    return flux


class LectureClasseurTests(TestCase):
    """Ce que `lire_feuille` sait reconnaître dans un classeur."""

    def test_report_vers_le_bas_des_cellules_de_groupe(self):
        """La zone n'est portée que par la première ligne de son groupe.

        C'est la convention des deux fichiers de référence : `LDD` et
        `CODE LDD` sont des cellules fusionnées verticalement, si bien que les
        lignes suivantes les rendent vides. Sans report, tous les Daaras sauf
        le premier perdraient leur zone.
        """
        lignes, ignorees, erreurs = lire_feuille(
            classeur([
                ("AVIATEUR", "ARMEE", "DS S18"),
                ("GENDARMERIE", None, None),
                ("POLICE", "", ""),
            ])
        )
        self.assertEqual(erreurs, [])
        self.assertEqual([l["ldd"] for l in lignes], ["ARMEE"] * 3)
        self.assertEqual([l["code"] for l in lignes], ["DS S18"] * 3)

    def test_ligne_de_totaux_fusionnee_est_ecartee(self):
        """🔴 LE DÉFAUT QUI FAISAIT ÉCHOUER L'IMPORT.

        `LDD DSMF SÉNÉGAL_CODE LDD.xlsx` se termine par `A399:D399`, fusionnée
        sur toute la largeur, portant « 397 … 31 ». L'ancien import la lisait
        comme un Daara de 177 caractères et Postgres levait
        `DataError: value too long for character varying(100)`.
        """
        flux = classeur(
            [("AVIATEUR", "ARMEE", "DS S18"), ("3 " + " " * 120 + "1", None, None)],
            fusions=("A3:C3",),
        )
        lignes, ignorees, erreurs = lire_feuille(flux)
        self.assertEqual(erreurs, [])
        self.assertEqual(len(lignes), 1)
        self.assertEqual(len(ignorees), 1)
        self.assertIn("totaux", ignorees[0])

    def test_valeur_trop_longue_est_une_erreur_de_ligne(self):
        """Un nom hors gabarit se dit, il ne fait pas tomber le serveur."""
        lignes, _, erreurs = lire_feuille(
            classeur([("X" * 150, "ARMEE", "DS S18")])
        )
        self.assertEqual(lignes, [])
        self.assertEqual(len(erreurs), 1)
        self.assertIn("150 caractères", erreurs[0])

    def test_colonnes_absentes(self):
        _, _, erreurs = lire_feuille(classeur([("A", "B", "C")], entete=("X", "Y", "Z")))
        self.assertEqual(len(erreurs), 1)
        self.assertIn("introuvables", erreurs[0])

    def test_daara_sans_zone_au_dessus(self):
        """Une première ligne sans zone ne peut pas hériter : on le dit."""
        _, _, erreurs = lire_feuille(classeur([("AVIATEUR", None, None)]))
        self.assertEqual(len(erreurs), 1)
        self.assertIn("n'est rattaché à aucune zone", erreurs[0])


class ImportAtomiqueTests(TestCase):
    """L'import écrit tout, ou rien."""

    def test_une_ligne_invalide_annule_tout(self):
        """🔴 L'ANCIEN IMPORT N'AVAIT AUCUNE TRANSACTION.

        Sur le fichier du Sénégal, il traitait 397 lignes puis levait sur la
        398e. Les 397 restaient acquises : l'écran annonçait « échec », la page
        rafraîchie montrait les données, et plus rien ne disait ce qui manquait.
        """
        rapport = importer(
            classeur([
                ("AVIATEUR", "ARMEE", "DS S18"),
                ("Y" * 150, None, None),
            ])
        )
        self.assertTrue(rapport.en_echec)
        self.assertEqual(Daara.objects.count(), 0)
        self.assertEqual(LDD.objects.count(), 0)
        self.assertIn("Rien n'a été enregistré", rapport.resume())

    def test_import_nominal(self):
        rapport = importer(
            classeur([
                ("AVIATEUR", "ARMEE", "DS S18"),
                ("GENDARMERIE", None, None),
            ])
        )
        self.assertFalse(rapport.en_echec)
        self.assertEqual(LDD.objects.count(), 1)
        self.assertEqual(Daara.objects.count(), 2)

    def test_rejouer_le_meme_fichier_ne_duplique_rien(self):
        lignes = [("AVIATEUR", "ARMEE", "DS S18"), ("GENDARMERIE", None, None)]
        importer(classeur(lignes))
        rapport = importer(classeur(lignes))
        self.assertEqual(Daara.objects.count(), 2)
        self.assertEqual(rapport.daaras_inchanges, 2)
        self.assertEqual(rapport.daaras_crees, [])


class CodePartageTests(TestCase):
    """Un code de territoire porté par plusieurs zones."""

    def test_deux_zones_de_meme_code_restent_distinctes(self):
        """🔴 LE DÉFAUT QUI A DÉTRUIT DES DONNÉES.

        `LDD.code` était `unique=True` et l'import résolvait la zone sur le
        seul code. Or la direction partage ses codes : `DS S3` couvre trois
        zones, `DS S17` deux. La deuxième zone d'un code était donc rendue
        comme étant la première — quatre zones perdues, quarante-six Daaras
        versés dans le mauvais territoire (KAOLACK2 dans KAOLACK1, notamment).
        """
        rapport = importer(
            classeur([
                ("PASSY", "KAOLACK1 WADIEUKHTOU", "DS S17"),
                ("NDORONG", "KAOLACK2 WAKAANA", "DS S17"),
            ])
        )
        self.assertFalse(rapport.en_echec)
        self.assertEqual(LDD.objects.filter(code="DS S17").count(), 2)
        self.assertEqual(
            set(LDD.objects.values_list("name", flat=True)),
            {"KAOLACK1 WADIEUKHTOU", "KAOLACK2 WAKAANA"},
        )
        self.assertEqual(Daara.objects.get(name="NDORONG").ldd.name, "KAOLACK2 WAKAANA")


class ReconciliationTests(TestCase):
    """La réparation des rattachements, et ses garde-fous."""

    def setUp(self):
        self.k1 = LDD.objects.create(code="DS S17", name="KAOLACK1 WADIEUKHTOU")
        # Rattachement fautif hérité de l'ancien import.
        self.egare = Daara.objects.create(name="NDORONG", ldd=self.k1)

    def _fichier(self):
        return classeur([
            ("PASSY", "KAOLACK1 WADIEUKHTOU", "DS S17"),
            ("NDORONG", "KAOLACK2 WAKAANA", "DS S17"),
        ])

    def test_strict_ne_deplace_jamais(self):
        """Un import de routine SIGNALE un écart, il ne le corrige pas d'office."""
        rapport = importer(self._fichier(), mode=ModeImport.STRICT)
        self.egare.refresh_from_db()
        self.assertEqual(self.egare.ldd, self.k1)
        self.assertEqual(Daara.objects.filter(name="NDORONG").count(), 2)
        self.assertEqual(len(rapport.avertissements), 1)

    def test_reconciliation_deplace_et_preserve_identifiant(self):
        """Déplacer, et non supprimer-recréer : membres et dons suivent.

        L'identifiant du Daara est la seule chose qui rattache ses membres,
        ses dons et ses Ndiguels. Le recréer sous la bonne zone les perdrait
        tous.
        """
        membre = User.objects.create_user(
            email="m@test.sn", password="x", first_name="M", last_name="T", daara=self.egare
        )
        rapport = importer(self._fichier(), mode=ModeImport.RECONCILIATION)

        self.egare.refresh_from_db()
        self.assertEqual(self.egare.ldd.name, "KAOLACK2 WAKAANA")
        self.assertEqual(Daara.objects.filter(name="NDORONG").count(), 1)
        self.assertEqual(len(rapport.daaras_deplaces), 1)

        membre.refresh_from_db()
        self.assertEqual(membre.daara_id, self.egare.id)

    def test_reconciliation_ne_sort_pas_du_perimetre_du_fichier(self):
        """🔴 FAUX POSITIF RELEVÉ PAR L'ESSAI À BLANC DU 2026-09-18.

        « DSMF THIOFEL » existe au Sénégal sous MADINATOU SALAM et, sous le
        même nom, à l'étranger sous KARAMNA PARIS. En important le seul fichier
        des étrangers, le nom n'y a qu'une zone et un seul Daara le porte en
        base : la règle concluait au rattachement fautif et expédiait un Daara
        sénégalais à Paris.

        Un fichier n'a rien à dire d'une zone dont il ne parle pas.
        """
        senegal = LDD.objects.create(code="DS S4", name="MADINATOU SALAM")
        thiofel = Daara.objects.create(name="DSMF THIOFEL", ldd=senegal)

        importer(
            classeur([("DSMF THIOFEL", "KARAMNA PARIS", "DS FR2")]),
            mode=ModeImport.RECONCILIATION,
        )

        thiofel.refresh_from_db()
        self.assertEqual(thiofel.ldd, senegal, "le Daara sénégalais ne doit pas bouger")
        self.assertEqual(Daara.objects.filter(name="DSMF THIOFEL").count(), 2)

    def test_reconciliation_s_abstient_sur_un_nom_ambigu(self):
        """Un nom que le fichier place dans deux zones ne se déplace pas.

        « DAWAMOU CHOUKRY » figure sous KAOLACK2 ET sous MADINATOU SALAM,
        « FAWZEYNI » sous MEDINA/FASS ET sous SERIGNE BETHIO MOY DEUG. Choisir
        reviendrait à tirer au sort.
        """
        importer(
            classeur([
                ("NDORONG", "KAOLACK1 WADIEUKHTOU", "DS S17"),
                ("NDORONG", "KAOLACK2 WAKAANA", "DS S17"),
            ]),
            mode=ModeImport.RECONCILIATION,
        )
        self.egare.refresh_from_db()
        self.assertEqual(self.egare.ldd, self.k1)
        self.assertEqual(Daara.objects.filter(name="NDORONG").count(), 2)


class GraphieTests(TestCase):
    """Deux graphies d'un même nom ne font pas deux Daaras."""

    def test_double_espace_ne_cree_pas_de_jumeau(self):
        """🔴 DÉFAUT INTRODUIT PUIS CORRIGÉ LE 2026-09-18.

        Le fichier du Sénégal contient « NGABOU PEKK BI  MEDINA » avec DEUX
        espaces. La lecture les réduit à un — c'est souhaitable — mais
        l'appariement se faisait sur la chaîne brute : le Daara déjà en base
        n'était plus reconnu, et l'import en créait un second dans la MÊME
        zone, au nom typographiquement différent et humainement identique.
        """
        zone = LDD.objects.create(code="DS S23", name="MEDINA/FASS")
        existant = Daara.objects.create(name="NGABOU PEKK BI  MEDINA", ldd=zone)

        importer(classeur([("NGABOU PEKK BI  MEDINA", "MEDINA/FASS", "DS S23")]))

        self.assertEqual(Daara.objects.filter(ldd=zone).count(), 1)
        existant.refresh_from_db()
        self.assertEqual(existant.name, "NGABOU PEKK BI  MEDINA", "strict ne renomme pas")

    def test_reconciliation_aligne_l_orthographe(self):
        zone = LDD.objects.create(code="DS S23", name="MEDINA/FASS")
        existant = Daara.objects.create(name="NGABOU PEKK BI  MEDINA", ldd=zone)

        rapport = importer(
            classeur([("NGABOU PEKK BI  MEDINA", "MEDINA/FASS", "DS S23")]),
            mode=ModeImport.RECONCILIATION,
        )

        self.assertEqual(Daara.objects.filter(ldd=zone).count(), 1)
        existant.refresh_from_db()
        self.assertEqual(existant.name, "NGABOU PEKK BI MEDINA")
        self.assertEqual(len(rapport.orthographes_alignees), 1)

    def test_casse_et_accents_ne_distinguent_pas(self):
        zone = LDD.objects.create(code="DS S4", name="MADINATOU SALAM")
        Daara.objects.create(name="individuel", ldd=zone)
        importer(classeur([("INDIVIDUEL", "MADINATOU SALAM", "DS S4")]))
        self.assertEqual(Daara.objects.filter(ldd=zone).count(), 1)

    def test_plusieurs_graphies_deja_en_base_ne_sont_pas_arbitrees(self):
        """Quand la base porte déjà deux graphies, l'import ne choisit pas."""
        zone = LDD.objects.create(code="DS S4", name="MADINATOU SALAM")
        Daara.objects.create(name="INDIVIDUEL", ldd=zone)
        Daara.objects.create(name="individuel ", ldd=zone)

        rapport = importer(
            classeur([("INDIVIDUEL", "MADINATOU SALAM", "DS S4")]),
            mode=ModeImport.RECONCILIATION,
        )
        self.assertEqual(Daara.objects.filter(ldd=zone).count(), 2)
        self.assertEqual(rapport.orthographes_alignees, [])
        # Le doublon doit se DIRE, sans quoi il reste invisible import après
        # import — y compris quand l'une des graphies correspond au fichier.
        self.assertEqual(len(rapport.avertissements), 1)
        self.assertIn("doublon à arbitrer", rapport.avertissements[0])


class LDDCrudTests(APITestCase):
    """Le CRUD des zones, qui n'existait pas.

    🔴 `LDDViewSet` ÉTAIT UN `ReadOnlyModelViewSet` alors que l'écran
    d'administration appelait `createLDD`, `updateLDD` et `deleteLDD`. Les
    trois recevaient un 405 : aucune zone ne pouvait être créée, renommée ni
    supprimée depuis l'interface prévue pour cela.
    """

    def setUp(self):
        self.client = APIClient()
        self.zone = LDD.objects.create(code="DS S1", name="Zone A")
        self.vide = LDD.objects.create(code="DS S2", name="Zone vide")
        self.daara = Daara.objects.create(name="Daara A1", ldd=self.zone)
        self.admin = User.objects.create_user(
            email="admin@test.sn", password="x", first_name="A", last_name="D",
            role=User.Role.ADMIN,
        )
        self.membre = User.objects.create_user(
            email="membre@test.sn", password="x", first_name="M", last_name="B",
            role=User.Role.MEMBER,
        )
        self.liste = reverse('ldd-list')

    def _detail(self, zone):
        return reverse('ldd-detail', args=[zone.pk])

    # ── Lecture ────────────────────────────────────────────────────────────
    def test_lecture_reste_anonyme(self):
        """L'inscription fait choisir sa zone AVANT tout compte."""
        self.assertEqual(self.client.get(self.liste).status_code, status.HTTP_200_OK)

    def test_zone_fermee_cachee_par_defaut(self):
        self.vide.is_active = False
        self.vide.save()
        rows = self.client.get(self.liste).data
        rows = rows if isinstance(rows, list) else rows['results']
        self.assertNotIn(self.vide.pk, {r['id'] for r in rows})

    def test_admin_peut_demander_les_zones_fermees(self):
        self.vide.is_active = False
        self.vide.save()
        self.client.force_authenticate(self.admin)
        rows = self.client.get(self.liste, {'include_inactive': '1'}).data
        rows = rows if isinstance(rows, list) else rows['results']
        self.assertIn(self.vide.pk, {r['id'] for r in rows})

    def test_compteur_de_daaras_expose(self):
        rows = self.client.get(self.liste).data
        rows = rows if isinstance(rows, list) else rows['results']
        par_id = {r['id']: r for r in rows}
        self.assertEqual(par_id[self.zone.pk]['daaras_count'], 1)
        self.assertEqual(par_id[self.vide.pk]['daaras_count'], 0)

    # ── Écriture ───────────────────────────────────────────────────────────
    def test_creation_refusee_a_l_anonyme(self):
        r = self.client.post(self.liste, {'code': 'DS S9', 'name': 'Neuve'})
        self.assertIn(r.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_creation_refusee_au_membre(self):
        self.client.force_authenticate(self.membre)
        r = self.client.post(self.liste, {'code': 'DS S9', 'name': 'Neuve'})
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_cree_une_zone(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post(self.liste, {'code': 'DS S9', 'name': 'Neuve'})
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertTrue(LDD.objects.filter(code='DS S9', name='Neuve').exists())

    def test_admin_renomme_une_zone(self):
        self.client.force_authenticate(self.admin)
        r = self.client.patch(self._detail(self.zone), {'name': 'Zone renommée'})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.zone.refresh_from_db()
        self.assertEqual(self.zone.name, 'Zone renommée')

    def test_deux_zones_peuvent_partager_un_code(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post(self.liste, {'code': 'DS S1', 'name': 'Zone A bis'})
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)

    def test_meme_code_et_meme_nom_refuses(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post(self.liste, {'code': 'DS S1', 'name': 'Zone A'})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    # ── Suppression ────────────────────────────────────────────────────────
    def test_suppression_refusee_si_la_zone_tient_des_daaras(self):
        """🔴 `Daara.ldd` ÉTAIT EN CASCADE.

        Supprimer une zone emportait en silence tous ses Daaras, leurs
        adhésions, et détachait leurs membres. Vingt-neuf Daaras pouvaient
        disparaître sur un clic.
        """
        self.client.force_authenticate(self.admin)
        r = self.client.delete(self._detail(self.zone))
        self.assertEqual(r.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(r.data['daaras_count'], 1)
        self.assertTrue(LDD.objects.filter(pk=self.zone.pk).exists())
        self.assertTrue(Daara.objects.filter(pk=self.daara.pk).exists())

    def test_suppression_d_une_zone_vide(self):
        self.client.force_authenticate(self.admin)
        r = self.client.delete(self._detail(self.vide))
        self.assertEqual(r.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(LDD.objects.filter(pk=self.vide.pk).exists())

    # ── Transfert en bloc ──────────────────────────────────────────────────
    def test_transfert_en_bloc_puis_suppression(self):
        """Vider une zone à la main demandait une modification par Daara."""
        self.client.force_authenticate(self.admin)
        url = reverse('ldd-transferer-daaras', args=[self.zone.pk])
        r = self.client.post(url, {'target_ldd': self.vide.pk}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['moved'], 1)

        self.daara.refresh_from_db()
        self.assertEqual(self.daara.ldd, self.vide)

        self.assertEqual(
            self.client.delete(self._detail(self.zone)).status_code,
            status.HTTP_204_NO_CONTENT,
        )

    def test_transfert_refuse_sur_collision_de_nom(self):
        """`unique_together (name, ldd)` : on le dit au lieu de laisser lever."""
        Daara.objects.create(name="Daara A1", ldd=self.vide)
        self.client.force_authenticate(self.admin)
        url = reverse('ldd-transferer-daaras', args=[self.zone.pk])
        r = self.client.post(url, {'target_ldd': self.vide.pk}, format='json')
        self.assertEqual(r.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(r.data['collisions'], ['Daara A1'])
        self.daara.refresh_from_db()
        self.assertEqual(self.daara.ldd, self.zone)

    def test_transfert_refuse_au_membre(self):
        self.client.force_authenticate(self.membre)
        url = reverse('ldd-transferer-daaras', args=[self.zone.pk])
        r = self.client.post(url, {'target_ldd': self.vide.pk}, format='json')
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
