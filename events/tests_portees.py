"""
Les règles de portée que personne ne gardait — Ndiguels et Jëfs.

Cinq règles métier tenues jusqu'ici par les interfaces seules, donc par rien :
le serveur acceptait ce qu'aucun écran ne proposait, et masquait ce que la
règle donne. Toutes issues de la relecture du 2026-09-06 avec le commanditaire,
contre `AGENTS/tools/05_use_cases_regles.md`.

1. `test_archivage_nefface_pas_lhistorique` — `create_archive` posait
   `archive_id` sur tous les dons confirmés, et la portée des membres excluait
   les archivés : une opération de comptabilité vidait l'historique personnel
   de toute la confrérie.
2. `test_collecteur_voit_aussi_les_siens` — un collecteur ne voyait QUE ses
   encaissements, jamais ses propres dons. Il est talibé avant d'être
   collecteur.
3. `test_tutelle_voit_ce_qu_on_donne_pour_elle` — RG007. Le filtre `donor=user`
   ne rendait rien : un compte de tutelle ne donne pas, on donne POUR lui.
4. `test_seul_ladmin_lance_un_ndiguel` — UC-06 ne porte qu'un acteur.
   `CAMPAIGN_CREATOR_ROLES` en autorisait quatre.
5. `test_organisateur_porte_son_propre_daara` — `daara` est un CIBLAGE ;
   l'organisateur vient d'où il veut. Le mobile affichait l'un pour l'autre.
"""
from datetime import date, timedelta
from django.test import TestCase
from rest_framework.test import APIClient
from accounts.models import User, Daara, LDD, Tutelle
from contributions.models import Donation, DonationArchive
from events.models import Campaign


class Portees(TestCase):
    def setUp(self):
        ldd = LDD.objects.create(code="LDD1", name="Diourbel")
        self.daara = Daara.objects.create(name="KANDE", ldd=ldd)
        self.autre = Daara.objects.create(name="GENDARMERIE", ldd=ldd)

        def creer(email, role, daara):
            return User.objects.create_user(
                email=email, password="Motdepasse1!", role=role, daara=daara,
                first_name=role, last_name="Test", is_active=True,
            )

        self.admin = creer("a@t.sn", "admin", self.daara)
        self.talibe = creer("m@t.sn", "member", self.daara)
        self.collecteur = creer("c@t.sn", "collector", self.daara)
        self.chef = creer("ch@t.sn", "chef_daara", self.daara)
        self.pupille = creer("t@t.sn", "tutelle", self.daara)

        self.camp = Campaign.objects.create(
            name="Ndiguel", deadline=date.today() + timedelta(days=30), status="active",
        )
        self.tut = Tutelle.objects.create(
            tutor=self.talibe, first_name="Sokhna", last_name="Aida",
            relation="fille", linked_user=self.pupille,
        )

    def cli(self, u):
        c = APIClient(); c.force_authenticate(user=u); return c

    def nb(self, u):
        d = self.cli(u).get("/api/contributions/").data
        return len(d.get("results", d)) if isinstance(d, dict) else len(d)

    def test_archivage_nefface_pas_lhistorique(self):
        don = Donation.objects.create(campaign=self.camp, donor=self.talibe,
                                      amount=5000, payment_status="confirmed")
        self.assertEqual(self.nb(self.talibe), 1)
        arch = DonationArchive.objects.create(name="2025", created_by=self.admin,
                                              total_amount=5000, total_count=1)
        Donation.objects.filter(pk=don.pk).update(archive_id=arch)
        self.assertEqual(self.nb(self.talibe), 1, "le talibé a perdu son Jëf archivé")

    def test_collecteur_voit_aussi_les_siens(self):
        Donation.objects.create(campaign=self.camp, donor=self.talibe,
                                collector=self.collecteur, amount=1000)
        Donation.objects.create(campaign=self.camp, donor=self.collecteur, amount=7000)
        self.assertEqual(self.nb(self.collecteur), 2, "le collecteur ne voit pas son propre Jëf")

    def test_tutelle_voit_ce_qu_on_donne_pour_elle(self):
        Donation.objects.create(campaign=self.camp, donor=self.talibe,
                                beneficiary=self.tut, amount=2000)
        self.assertEqual(self.nb(self.pupille), 1, "le compte de tutelle ne voit rien (RG007)")

    def test_seul_ladmin_lance_un_ndiguel(self):
        charge = {"name": "Test", "deadline": str(date.today() + timedelta(days=10)),
                  "status": "active"}
        for u, attendu in [(self.talibe, 403), (self.collecteur, 403),
                           (self.chef, 403), (self.admin, 201)]:
            r = self.cli(u).post("/api/events/campaigns/", charge, format="json")
            self.assertEqual(r.status_code, attendu, f"{u.role} -> {r.status_code}")

    def test_organisateur_porte_son_propre_daara(self):
        orga = User.objects.create_user(email="o@t.sn", password="Motdepasse1!",
                                        role="member", daara=self.autre,
                                        first_name="Pape", last_name="Kane", is_active=True)
        self.camp.daara = self.daara      # ciblage
        self.camp.organizer = orga        # organisateur d'un AUTRE Daara
        self.camp.save()
        d = self.cli(self.talibe).get(f"/api/events/campaigns/{self.camp.id}/").data
        self.assertEqual(d["daara_name"], "KANDE")
        self.assertEqual(d["organizer_daara_name"], "GENDARMERIE")
