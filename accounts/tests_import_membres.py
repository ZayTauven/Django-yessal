# -*- coding: utf-8 -*-
"""
L'import des membres depuis Excel, et l'accueil des comptes qu'il crée.

Les cas viennent de l'épreuve du 2026-10-29 sur la base de démonstration : la
colonne Daara jetée par le navigateur, le rôle « Membre » refusé sans un mot,
et les huit noms de Daara portés par plusieurs LDD.
"""

from io import BytesIO

import openpyxl
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import LDD, Daara
from accounts.services.accueil import etapes_accueil
from accounts.services.user_import import MOT_DE_PASSE_PAR_DEFAUT, Verdict

User = get_user_model()

ENTETE = ("Prénom", "Nom", "Email", "Téléphone", "Rôle", "Daara", "LDD")


def classeur(lignes, entete=ENTETE):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(entete))
    for ligne in lignes:
        ws.append(list(ligne))
    flux = BytesIO()
    wb.save(flux)
    flux.seek(0)
    flux.name = "membres.xlsx"
    return flux


class Socle(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.kaolack = LDD.objects.create(code="DS S17", name="KAOLACK1 WADIEUKHTOU")
        cls.medina = LDD.objects.create(code="DS S1", name="MEDINA/FASS TOUHFATOU")
        cls.diourbel = LDD.objects.create(code="DS S10", name="DIOURBEL RABBI")
        cls.kande = Daara.objects.create(name="KANDE", ldd=cls.kaolack)
        # Un nom porté par deux LDD : le nom seul ne suffit pas.
        cls.fawzeyni_medina = Daara.objects.create(name="FAWZEYNI", ldd=cls.medina)
        cls.fawzeyni_diourbel = Daara.objects.create(name="FAWZEYNI", ldd=cls.diourbel)
        cls.admin = User.objects.create_user(
            email="admin@yessal.test", password="x", role=User.Role.ADMIN,
            status=User.Status.ACTIVE, first_name="Ad", last_name="Min",
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def importer(self, lignes, apercu=False, entete=ENTETE):
        return self.client.post(
            "/api/users/import-excel/",
            {"file": classeur(lignes, entete), "apercu": "true" if apercu else "false"},
            format="multipart",
        )

    def verdicts(self, reponse):
        return [(l["nom"], l["verdict"]) for l in reponse.data["lignes"]]


class AffectationDuDaaraTests(Socle):
    def test_daara_et_ldd_exacts_sont_affectes(self):
        r = self.importer([("Awa", "Ndiaye", "awa@ex.org", "", "", "KANDE", "KAOLACK1 WADIEUKHTOU")])
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["lignes"][0]["verdict"], Verdict.CREE)
        self.assertEqual(User.objects.get(email="awa@ex.org").daara, self.kande)

    def test_ecart_de_casse_et_d_accents_reste_le_meme_daara(self):
        self.importer([("Awa", "Ndiaye", "awa@ex.org", "", "", "kandé", "kaolack1  wadieukhtou")])
        self.assertEqual(User.objects.get(email="awa@ex.org").daara, self.kande)

    def test_nom_unique_sans_ldd_est_affecte(self):
        self.importer([("Awa", "Ndiaye", "awa@ex.org", "", "", "KANDE", "")])
        self.assertEqual(User.objects.get(email="awa@ex.org").daara, self.kande)

    def test_homonyme_sans_ldd_cree_le_compte_sans_daara(self):
        """« FAWZEYNI » existe sous deux LDD : choisir serait tirer au sort."""
        r = self.importer([("Modou", "Fall", "modou@ex.org", "", "", "FAWZEYNI", "")])
        ligne = r.data["lignes"][0]
        self.assertEqual(ligne["verdict"], Verdict.CREE_SANS_DAARA)
        self.assertIn("précisez la colonne LDD", ligne["motif"])
        self.assertIsNone(User.objects.get(email="modou@ex.org").daara)

    def test_homonyme_avec_sa_ldd_est_affecte(self):
        self.importer([("Modou", "Fall", "modou@ex.org", "", "", "FAWZEYNI", "DIOURBEL RABBI")])
        self.assertEqual(User.objects.get(email="modou@ex.org").daara, self.fawzeyni_diourbel)

    def test_faute_de_frappe_ne_devine_pas_mais_suggere(self):
        r = self.importer([("Awa", "Ndiaye", "awa@ex.org", "", "", "KANDDE", "")])
        ligne = r.data["lignes"][0]
        self.assertEqual(ligne["verdict"], Verdict.CREE_SANS_DAARA)
        self.assertIn("introuvable", ligne["motif"])
        self.assertIn("KANDE (KAOLACK1 WADIEUKHTOU)", ligne["motif"])
        self.assertIsNone(User.objects.get(email="awa@ex.org").daara)

    def test_daara_hors_de_la_ldd_indiquee_n_est_pas_affecte(self):
        r = self.importer([("Awa", "Ndiaye", "awa@ex.org", "", "", "KANDE", "DIOURBEL RABBI")])
        self.assertEqual(r.data["lignes"][0]["verdict"], Verdict.CREE_SANS_DAARA)
        self.assertIn("KAOLACK1 WADIEUKHTOU", r.data["lignes"][0]["motif"])

    def test_ldd_inconnue_n_affecte_rien(self):
        r = self.importer([("Awa", "Ndiaye", "awa@ex.org", "", "", "KANDE", "ZONE FANTOME")])
        self.assertIn("LDD « ZONE FANTOME » introuvable", r.data["lignes"][0]["motif"])

    def test_daara_desactive_n_est_pas_affecte(self):
        Daara.objects.filter(pk=self.kande.pk).update(is_active=False)
        r = self.importer([("Awa", "Ndiaye", "awa@ex.org", "", "", "KANDE", "")])
        self.assertIn("désactivé", r.data["lignes"][0]["motif"])


class LignesEtRolesTests(Socle):
    def test_role_en_francais_est_reconnu(self):
        """Le cas qui échouait sans un mot : « Membre » au lieu de `member`."""
        self.importer([
            ("Awa", "Ndiaye", "awa@ex.org", "", "Membre", "", ""),
            ("Ibou", "Sarr", "ibou@ex.org", "", "Collecteur", "", ""),
            ("Ndeye", "Diop", "ndeye@ex.org", "", "Coordinateur du Daara", "", ""),
        ])
        self.assertEqual(User.objects.get(email="awa@ex.org").role, User.Role.MEMBER)
        self.assertEqual(User.objects.get(email="ibou@ex.org").role, User.Role.COLLECTOR)
        self.assertEqual(User.objects.get(email="ndeye@ex.org").role, User.Role.CHEF_DAARA)

    def test_un_fichier_ne_cree_pas_d_administrateur(self):
        r = self.importer([("Awa", "Ndiaye", "awa@ex.org", "", "Admin", "", "")])
        self.assertEqual(User.objects.get(email="awa@ex.org").role, User.Role.MEMBER)
        self.assertTrue(r.data["lignes"][0]["remarques"])

    def test_chaque_refus_dit_sa_ligne_et_sa_raison(self):
        User.objects.create_user(email="pris@ex.org", password="x")
        r = self.importer([
            ("Awa", "Ndiaye", "pris@ex.org", "", "", "", ""),
            ("", "", "anonyme@ex.org", "", "", "", ""),
            ("Sans", "Contact", "", "", "", "", ""),
        ])
        lignes = r.data["lignes"]
        self.assertEqual([l["verdict"] for l in lignes], [Verdict.REJETE] * 3)
        self.assertEqual([l["ligne"] for l in lignes], [2, 3, 4])
        self.assertIn("Email", lignes[0]["motif"])
        self.assertIn("ni prénom ni nom", lignes[1]["motif"])
        self.assertIn("ni email ni téléphone", lignes[2]["motif"])

    def test_doublon_dans_le_meme_fichier(self):
        r = self.importer([
            ("Awa", "Ndiaye", "awa@ex.org", "", "", "", ""),
            ("Awa", "Bis", "awa@ex.org", "", "", "", ""),
        ])
        self.assertEqual([l["verdict"] for l in r.data["lignes"]],
                         [Verdict.CREE_SANS_DAARA, Verdict.REJETE])

    def test_telephone_saisi_en_nombre(self):
        """Excel range « 771230001 » en nombre ; openpyxl le rend parfois en flottant."""
        self.importer([("Fatou", "Sow", "", 771230001.0, "", "KANDE", "")])
        fatou = User.objects.get(first_name="Fatou")
        self.assertEqual(fatou.phone, "+221771230001")
        self.assertTrue(fatou.check_password(MOT_DE_PASSE_PAR_DEFAUT))
        self.assertTrue(fatou.must_change_password)
        self.assertEqual(fatou.status, User.Status.ACTIVE)

    def test_lignes_vides_ignorees_et_entete_decale(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Liste des membres — octobre"])
        ws.append(["PRENOM", "NOM", "TEL"])
        ws.append([None, None, None])
        ws.append(["Awa", "Ndiaye", "771230001"])
        flux = BytesIO()
        wb.save(flux)
        flux.seek(0)
        r = self.client.post("/api/users/import-excel/", {"file": flux}, format="multipart")
        self.assertEqual(r.data["totaux"]["lignes_vides"], 1)
        self.assertEqual(r.data["lignes"][0]["ligne"], 4)

    def test_fichier_sans_colonnes_reconnues(self):
        r = self.importer([("x",)], entete=("Couleur",))
        self.assertEqual(r.status_code, 400)
        self.assertIn("Format invalide", r.data["message"])

    def test_reserve_aux_administrateurs(self):
        membre = User.objects.create_user(email="m@ex.org", password="x", status=User.Status.ACTIVE)
        self.client.force_authenticate(membre)
        self.assertEqual(self.importer([]).status_code, status.HTTP_403_FORBIDDEN)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_ENABLED=True,
)
class ApercuEtCourrielsTests(Socle):
    def test_l_apercu_ne_cree_rien_et_n_ecrit_a_personne(self):
        with self.captureOnCommitCallbacks(execute=True):
            r = self.importer([("Awa", "Ndiaye", "awa@yessal.org", "", "", "KANDE", "")], apercu=True)
        self.assertEqual(r.data["lignes"][0]["verdict"], Verdict.CREE)
        self.assertTrue(r.data["apercu"])
        self.assertFalse(User.objects.filter(email="awa@yessal.org").exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_l_import_reel_ecrit_apres_la_transaction(self):
        with self.captureOnCommitCallbacks(execute=True) as rappels:
            self.importer([("Awa", "Ndiaye", "awa@yessal.org", "", "", "KANDE", "")])
        self.assertEqual(len(rappels), 1)
        self.assertTrue(User.objects.filter(email="awa@yessal.org").exists())


class AccueilTests(Socle):
    def membre(self, **champs):
        valeurs = dict(email="m@ex.org", password="x", status=User.Status.ACTIVE)
        valeurs.update(champs)
        return User.objects.create_user(**valeurs)

    def test_compte_importe_sans_daara(self):
        u = self.membre(must_change_password=True)
        self.assertEqual(etapes_accueil(u), ["daara", "fiche", "mot_de_passe"])

    def test_le_mot_de_passe_vient_en_dernier_et_clot_l_accueil(self):
        u = self.membre(daara=self.kande, must_change_password=True)
        self.assertEqual(etapes_accueil(u), ["fiche", "mot_de_passe"])
        u.must_change_password = False
        self.assertEqual(etapes_accueil(u), [])

    def test_membre_installe_n_est_pas_renvoye_a_l_accueil(self):
        """Fiche vide, mais Daara et mot de passe en ordre : le rappel suffit."""
        self.assertEqual(etapes_accueil(self.membre(daara=self.kande)), [])

    def test_administrateur_n_a_que_son_mot_de_passe(self):
        u = self.membre(role=User.Role.ADMIN, must_change_password=True)
        self.assertEqual(etapes_accueil(u), ["mot_de_passe"])

    def test_profil_sert_les_etapes(self):
        u = self.membre(must_change_password=True)
        self.client.force_authenticate(u)
        self.assertEqual(self.client.get("/api/profile/").data["onboarding"],
                         ["daara", "fiche", "mot_de_passe"])


class ChoixDuDaaraTests(Socle):
    def setUp(self):
        super().setUp()
        self.membre = User.objects.create_user(
            email="m@ex.org", password="x", status=User.Status.ACTIVE,
            first_name="Awa", last_name="Ndiaye",
        )
        self.client.force_authenticate(self.membre)

    def test_le_membre_choisit_son_daara(self):
        r = self.client.post("/api/profile/daara/", {"daara_id": self.kande.pk}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.membre.refresh_from_db()
        self.assertEqual(self.membre.daara, self.kande)
        self.assertNotIn("daara", r.data["onboarding"])

    def test_une_seule_fois(self):
        self.client.post("/api/profile/daara/", {"daara_id": self.kande.pk}, format="json")
        r = self.client.post("/api/profile/daara/", {"daara_id": self.fawzeyni_medina.pk}, format="json")
        self.assertEqual(r.status_code, status.HTTP_409_CONFLICT)
        self.membre.refresh_from_db()
        self.assertEqual(self.membre.daara, self.kande)

    def test_daara_desactive_ou_inconnu_refuse(self):
        Daara.objects.filter(pk=self.kande.pk).update(is_active=False)
        for valeur in (self.kande.pk, 999999, "abc"):
            r = self.client.post("/api/profile/daara/", {"daara_id": valeur}, format="json")
            self.assertEqual(r.status_code, 400)

    def test_le_coordinateur_est_prevenu(self):
        from comms.models import Notification
        chef = User.objects.create_user(
            email="chef@ex.org", password="x", status=User.Status.ACTIVE,
            role=User.Role.CHEF_DAARA, daara=self.kande,
        )
        self.client.post("/api/profile/daara/", {"daara_id": self.kande.pk}, format="json")
        self.assertTrue(Notification.objects.filter(user=chef, message__contains="Awa Ndiaye").exists())

    def test_le_collecteur_recoit_la_zone_de_son_daara(self):
        User.objects.filter(pk=self.membre.pk).update(role=User.Role.COLLECTOR)
        self.membre.refresh_from_db()
        self.client.force_authenticate(self.membre)
        self.client.post("/api/profile/daara/", {"daara_id": self.kande.pk}, format="json")
        self.assertEqual(list(self.membre.collecte_zones.all()), [self.kaolack])
