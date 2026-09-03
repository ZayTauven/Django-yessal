"""
Jeu de données de démonstration.

    python manage.py seed_demo            # crée ou complète
    python manage.py seed_demo --flush    # efface le jeu précédent d'abord

La base de développement était quasi vide : 0 fête, 0 actualité, 0 Ndiguel.
Plusieurs écrans ne pouvaient donc être vus qu'en état vide — l'article à la
une, les cartes média, les pastilles de date, et surtout le tri et la
pagination des tableaux, qui n'ont jamais dépassé une page.

Deux principes :

  · IDEMPOTENTE. Chaque objet est apparié sur une clé naturelle (l'e-mail pour
    un membre, le nom pour une fête). Relancer la commande ne duplique rien et
    ne réécrit pas ce qui a été modifié à la main entre-temps.

  · S'APPUIE SUR L'EXISTANT. Les Daaras et les zones LDD réels ne sont pas
    touchés : les membres créés y sont rattachés. Inventer une hiérarchie
    parallèle aurait rendu le jeu inutilisable pour tester les filtres par
    Daara.

Tous les objets créés portent une marque (`DEMO_TAG`) qui permet à `--flush`
de les retrouver sans risquer d'effacer des données réelles.
"""

from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import Daara, MemberTitle, TitleRequest
from contributions.models import Donation
from events.models import Campaign, CampaignTodo, Fete
from news.models import NewsPost

User = get_user_model()

# Les comptes de démonstration partagent ce domaine : c'est ce qui permet à
# --flush de les distinguer des comptes réels sans se tromper.
DEMO_DOMAIN = "demo.yessal.local"
DEMO_TAG = "[démo]"

# Graine fixe : deux exécutions produisent le même jeu, ce qui rend une
# capture d'écran ou un test reproductible.
RNG = random.Random(2026)

PRENOMS_H = [
    "Amadou", "Moussa", "Cheikh", "Ibrahima", "Modou", "Serigne", "Abdoulaye",
    "Ousmane", "Mamadou", "Babacar", "Alioune", "Pape", "Idrissa", "Souleymane",
]
PRENOMS_F = [
    "Aminata", "Fatou", "Awa", "Khady", "Mariama", "Astou", "Ndeye", "Sokhna",
    "Bineta", "Adama", "Coumba", "Rokhaya",
]
NOMS = [
    "Ndiaye", "Diop", "Fall", "Sow", "Ba", "Gueye", "Sarr", "Mbaye", "Faye",
    "Cissé", "Kane", "Diallo", "Seck", "Thiam", "Niang", "Sy",
]

VILLES = ["Dakar", "Touba", "Thiès", "Saint-Louis", "Kaolack", "Mbour", "Ziguinchor"]
PAYS = ["Sénégal", "Sénégal", "Sénégal", "France", "Italie", "États-Unis"]

TITRES = ["Serigne", "Sokhna", "Djiëwrigne", "Mame"]

FETES = [
    ("Magal de Touba", "annual", -240, "Le grand rassemblement annuel de la confrérie."),
    ("Gamou de Tivaouane", "annual", -120, "Célébration de la naissance du Prophète."),
    ("Ziar de fin d'année", "annual", -30, "Visite collective et prières de clôture."),
    ("Tog Ajumma", "weekly", 5, "Rassemblement hebdomadaire du vendredi."),
    ("Kazu Rajab", "annual", 45, "Commémoration du mois de Rajab."),
    ("Journée du Talibé", "annual", 150, "Journée dédiée aux talibés des Daaras."),
]

# (nom, statut, objectif, jours avant/après l'échéance, description)
NDIGUELS = [
    ("Construction du mur d'enceinte", "active", 5_000_000, 40,
     "Sécuriser l'enceinte du Daara principal avant l'hivernage."),
    ("Réfection de la grande salle", "active", 2_500_000, 75,
     "Toiture et sol de la salle de rassemblement."),
    ("Fournitures scolaires des talibés", "completed", 1_200_000, -20,
     "Cahiers, ardoises et tenues pour la rentrée."),
    ("Forage du puits communautaire", "pending", 8_000_000, 120,
     "Accès à l'eau potable pour le Daara et son voisinage."),
    ("Soutien aux familles", "active", None, 60,
     "Ndiguel sans objectif chiffré : chaque contribution compte."),
]

ACTUALITES = [
    ("Retour sur le Magal 2026",
     "Le Magal de cette année a rassemblé plus de fidèles que jamais. "
     "Retour en images sur trois journées de ferveur, d'accueil et de partage, "
     "portées par les Daaras venus de tout le pays et de la diaspora.",
     "Trois journées de ferveur, portées par les Daaras du pays et de la diaspora.",
     True),
    ("Le forage du puits est lancé",
     "Les travaux ont démarré la semaine dernière. Le forage desservira le Daara "
     "et les concessions voisines, mettant fin à plusieurs kilomètres de marche "
     "quotidienne pour l'eau potable.",
     "Les travaux ont démarré : le puits desservira le Daara et son voisinage.",
     True),
    ("Nouvelle organisation des collectes",
     "À compter du mois prochain, chaque Daara désigne deux collecteurs "
     "référents. Les contributions en espèces passeront par eux, et seront "
     "consignées le jour même sur la plateforme.",
     "Deux collecteurs référents par Daara, et une saisie le jour même.",
     True),
    ("Préparatifs du Gamou — appel aux volontaires",
     "L'organisation du Gamou cherche des volontaires pour l'accueil, la "
     "restauration et la logistique. Rapprochez-vous de votre chef de Daara.",
     "Appel aux volontaires pour l'accueil, la restauration et la logistique.",
     False),
]

PAYMENT_METHODS = [
    Donation.PaymentMethod.ORANGE_MONEY,
    Donation.PaymentMethod.WAVE,
    Donation.PaymentMethod.BICTORYS,
    Donation.PaymentMethod.VIREMENT,
    Donation.PaymentMethod.MANUAL,
]


# Tâches d'un Ndiguel. Volontairement concrètes : ce sont celles qu'un chef de
# Daara écrirait vraiment — repérer un fournisseur, réunir le comité, rendre
# compte. Un jeu de démonstration qui dit « Tâche 1, Tâche 2 » ne permet pas de
# juger de la lisibilité d'un écran.
TODOS = [
    "Réunir le comité d'organisation",
    "Établir le devis auprès des fournisseurs",
    "Annoncer le Ndiguel aux Daaras concernés",
    "Désigner les collecteurs de terrain",
    "Ouvrir le registre des versements",
    "Rendre compte à la tutelle",
]


class Command(BaseCommand):
    help = "Peuple la base avec un jeu de démonstration cohérent et réexécutable."

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Efface le jeu de démonstration précédent avant de le recréer.",
        )
        parser.add_argument(
            "--donations",
            type=int,
            default=60,
            help="Nombre de Jëfs à générer (défaut : 60).",
        )

    # ── Entrée ──────────────────────────────────────────────────────────────

    @transaction.atomic
    def handle(self, *args, **options):
        if options["flush"]:
            self._flush()

        daaras = list(Daara.objects.all()[:12])
        if not daaras:
            self.stderr.write(
                self.style.ERROR(
                    "Aucun Daara en base. Importez d'abord les Daaras (écran "
                    "« Gestion des Daaras » ou fichier Excel) : le jeu de "
                    "démonstration s'appuie sur les structures réelles."
                )
            )
            return

        titles = self._seed_titles()
        members, chefs, collectors = self._seed_members(daaras, titles)
        fetes = self._seed_fetes()
        campaigns = self._seed_campaigns(fetes, daaras, chefs + collectors)
        self._seed_todos(campaigns)
        self._seed_donations(campaigns, members, collectors, options["donations"])
        self._seed_news(chefs[:1] or members[:1])
        self._seed_pending(members, titles)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Jeu de démonstration en place."))
        self.stdout.write(
            "  Relancez la commande sans crainte : rien ne sera dupliqué.\n"
            "  `--flush` efface uniquement les objets de démonstration."
        )

    # ── Nettoyage ───────────────────────────────────────────────────────────

    def _flush(self):
        """
        Efface le jeu précédent — et lui seul.

        Les Jëfs et les Ndiguels partent en premier : ils référencent des
        membres, et une suppression en cascade risquerait d'emporter des
        contributions réelles rattachées aux mêmes Ndiguels.
        """
        demo_users = User.objects.filter(email__endswith=f"@{DEMO_DOMAIN}")

        counts = {
            "Jëfs": Donation.objects.filter(donor__in=demo_users).delete()[0],
            "Tâches": CampaignTodo.objects.filter(campaign__name__in=[n for n, *_ in NDIGUELS]).delete()[0],
            "Ndiguels": Campaign.objects.filter(name__in=[n for n, *_ in NDIGUELS]).delete()[0],
            "fêtes": Fete.objects.filter(name__in=[n for n, *_ in FETES]).delete()[0],
            "actualités": NewsPost.objects.filter(title__in=[t for t, *_ in ACTUALITES]).delete()[0],
            "membres": demo_users.delete()[0],
        }

        for label, n in counts.items():
            self.stdout.write(f"  – {n} objets supprimés ({label})")

    # ── Titres honorifiques ─────────────────────────────────────────────────

    def _seed_titles(self) -> list[MemberTitle]:
        titles = []
        for name in TITRES:
            title, _ = MemberTitle.objects.get_or_create(
                name=name, defaults={"is_active": True}
            )
            titles.append(title)
        self.stdout.write(f"Titres      : {len(titles)}")
        return titles

    # ── Membres ─────────────────────────────────────────────────────────────

    def _seed_members(self, daaras, titles):
        """
        Crée les membres et renvoie (tous, chefs, collecteurs).

        L'e-mail sert de clé naturelle : `get_or_create` sur ce champ rend la
        commande relançable, et laisse intactes les modifications faites à la
        main sur un compte existant.
        """
        members, chefs, collectors = [], [], []

        # Rattrapage des adresses créées AVANT le passage à `slugify`.
        #
        # Sans lui, la commande cesserait d'être rejouable sur une base déjà
        # peuplée : `get_or_create(email=...)` chercherait la nouvelle forme,
        # ne trouverait pas l'ancienne, et créerait vingt-cinq doublons — ou
        # buterait sur l'unicité du téléphone. On corrige donc l'existant avant
        # d'apparier, en ne touchant qu'aux comptes de démonstration.
        for user in User.objects.filter(email__endswith=f"@{DEMO_DOMAIN}"):
            local, _, domain = user.email.partition("@")
            if local.isascii():
                continue
            head, _, tail = local.rpartition(".")
            fixed = f"{slugify(head)}.{slugify(tail)}@{domain}"
            if fixed != user.email and not User.objects.filter(email=fixed).exists():
                User.objects.filter(pk=user.pk).update(email=fixed)

        # Deux chefs, trois collecteurs, le reste en talibés.
        roster = (
            [("chef_daara", 2), ("collector", 3), ("member", 20)]
        )

        index = 0
        for role, count in roster:
            for _ in range(count):
                index += 1
                is_female = RNG.random() < 0.4
                first = RNG.choice(PRENOMS_F if is_female else PRENOMS_H)
                last = RNG.choice(NOMS)
                # `slugify` et non `.lower()` : les prénoms sénégalais portent
                # des accents — Cissé, Ndèye, Sène — et « babacar.cissé23@… »
                # n'est pas une adresse valide. Les comptes de démonstration
                # étaient donc injoignables et refusés par tout formulaire qui
                # valide l'e-mail.
                email = f"{slugify(first)}.{slugify(last)}{index}@{DEMO_DOMAIN}"

                user, created = User.objects.get_or_create(
                    email=email,
                    defaults={
                        "first_name": first,
                        "last_name": last,
                        # Numéros du bloc de test 77 9xx : jamais attribués.
                        "phone": f"+2217790{index:05d}",
                        "role": role,
                        # Quelques comptes restent « à valider » pour que la file
                        # d'administration ne soit pas vide.
                        "status": "pending" if index % 9 == 0 else "active",
                        "daara": RNG.choice(daaras),
                        "city": RNG.choice(VILLES),
                        "residence_country": RNG.choice(PAYS),
                        "gender": "female" if is_female else "male",
                        "title": RNG.choice(titles) if RNG.random() < 0.3 else None,
                        "birth_date": timezone.now().date()
                        - timedelta(days=RNG.randint(6570, 22000)),
                    },
                )
                if created:
                    user.set_password("Demo2026!")
                    user.save(update_fields=["password"])

                members.append(user)
                if role == "chef_daara":
                    chefs.append(user)
                elif role == "collector":
                    collectors.append(user)

        self.stdout.write(
            f"Membres     : {len(members)} "
            f"({len(chefs)} chefs, {len(collectors)} collecteurs)"
        )
        return members, chefs, collectors

    # ── Fêtes ───────────────────────────────────────────────────────────────

    def _seed_fetes(self) -> list[Fete]:
        today = timezone.now().date()
        fetes = []

        for name, recurrence, offset_days, description in FETES:
            fete, _ = Fete.objects.get_or_create(
                name=name,
                defaults={
                    "description": description,
                    "date": today + timedelta(days=offset_days),
                    "recurrence": recurrence,
                    "is_active": True,
                },
            )
            fetes.append(fete)

        passees = sum(1 for _, _, d, _ in FETES if d < 0)
        self.stdout.write(
            f"Fêtes       : {len(fetes)} ({passees} passées, "
            f"{len(FETES) - passees} à venir)"
        )
        return fetes

    # ── Ndiguels ────────────────────────────────────────────────────────────

    def _seed_campaigns(self, fetes, daaras, organizers) -> list[Campaign]:
        today = timezone.now().date()
        campaigns = []

        for i, (name, status, goal, deadline_offset, description) in enumerate(NDIGUELS):
            campaign, _ = Campaign.objects.get_or_create(
                name=name,
                defaults={
                    "description": description,
                    "goal_amount": Decimal(goal) if goal else None,
                    "deadline": today + timedelta(days=deadline_offset),
                    "status": status,
                    "fete": fetes[i % len(fetes)],
                    "daara": RNG.choice(daaras),
                    "organizer": organizers[i % len(organizers)] if organizers else None,
                    "organizer_assigned_at": timezone.now(),
                },
            )
            campaigns.append(campaign)

        self.stdout.write(f"Ndiguels    : {len(campaigns)}")
        return campaigns

    def _seed_todos(self, campaigns):
        """Des tâches, en proportion de l'avancement de chaque Ndiguel.

        Sans elles, la colonne « Avancement » de Performance des Ndiguels
        affichait « 0 / 0 tâches » sur chaque ligne et le KPI annonçait
        « 0 sur 0 au total » : une fonctionnalité entière invérifiable, et un
        écran qui donnait à croire que personne ne faisait rien.

        La part achevée suit le statut : un Ndiguel terminé a toutes ses tâches
        faites, un Ndiguel à venir n'en a aucune. C'est ce qui rend l'écran
        lisible — sinon l'avancement n'a aucun rapport avec l'état affiché à
        côté, et on ne sait plus lequel croire.
        """
        created = 0

        for campaign in campaigns:
            if campaign.todos.exists():
                continue

            count = RNG.randint(4, len(TODOS))
            titles = TODOS[:count]

            # Les statuts de `Campaign.Status` : pending, active, completed,
            # inactive. Pas « upcoming » — le libellé « À venir » de l'interface
            # traduit `pending`.
            if campaign.status == Campaign.Status.COMPLETED:
                done = count
            elif campaign.status in (Campaign.Status.PENDING, Campaign.Status.INACTIVE):
                done = 0
            else:
                done = RNG.randint(1, max(1, count - 1))

            for i, title in enumerate(titles):
                CampaignTodo.objects.create(
                    campaign=campaign,
                    title=title,
                    is_completed=i < done,
                )
                created += 1

        total = CampaignTodo.objects.count()
        self.stdout.write(f"Tâches      : {created} créées ({total} au total)")

    # ── Jëfs ────────────────────────────────────────────────────────────────

    def _seed_donations(self, campaigns, members, collectors, target: int):
        """
        Répartit les Jëfs sur six mois et sur tous les moyens de paiement.

        L'étalement dans le temps n'est pas cosmétique : c'est lui qui permet de
        vérifier le tri par date, les graphiques d'évolution et les regroupements
        mensuels, invérifiables sur un jeu créé d'un bloc.
        """
        existing = Donation.objects.filter(donor__in=members).count()
        if existing >= target:
            self.stdout.write(f"Jëfs        : {existing} (déjà en place)")
            return

        now = timezone.now()
        to_create = target - existing
        created = 0

        for i in range(to_create):
            campaign = RNG.choice(campaigns)
            donor = RNG.choice(members)
            method = RNG.choice(PAYMENT_METHODS)

            # Une minorité reste en attente ou en échec : les écrans de suivi et
            # la file des virements doivent avoir de quoi montrer.
            roll = RNG.random()
            if roll < 0.78:
                status = Donation.PaymentStatus.CONFIRMED
            elif roll < 0.90:
                status = Donation.PaymentStatus.PENDING
            elif roll < 0.96:
                status = Donation.PaymentStatus.PENDING_WIRE
            else:
                status = Donation.PaymentStatus.FAILED

            donation = Donation.objects.create(
                campaign=campaign,
                donor=donor,
                target_daara=donor.daara,
                amount=Decimal(RNG.choice([2_000, 5_000, 10_000, 15_000, 25_000, 50_000, 100_000])),
                payment_method=method,
                payment_status=status,
                is_anonymous=RNG.random() < 0.12,
                wire_reference=(
                    f"VIR-{RNG.randint(10000, 99999)}"
                    if method == Donation.PaymentMethod.VIREMENT
                    else ""
                ),
                collector=(
                    RNG.choice(collectors)
                    if method == Donation.PaymentMethod.MANUAL and collectors
                    else None
                ),
            )

            # `created_at` est en auto_now_add : il faut le réécrire après coup
            # pour étaler les dons dans le temps.
            Donation.objects.filter(pk=donation.pk).update(
                created_at=now - timedelta(days=RNG.randint(0, 180), hours=RNG.randint(0, 23))
            )
            created += 1

        self.stdout.write(f"Jëfs        : {created} créés (total {existing + created})")

    # ── Actualités ──────────────────────────────────────────────────────────

    def _seed_news(self, authors):
        author = authors[0] if authors else None
        now = timezone.now()
        count = 0

        for i, (title, content, excerpt, published) in enumerate(ACTUALITES):
            # Appariement par SLUG et non par titre. Le titre est modifiable
            # depuis l'interface : un article renommé n'était plus reconnu, le
            # seed tentait de le recréer, et se heurtait à la contrainte
            # d'unicité du slug — qui, lui, ne bouge pas. La commande cessait
            # alors d'être rejouable dès que quelqu'un avait touché à un titre.
            post, created = NewsPost.objects.get_or_create(
                slug=slugify(title),
                defaults={
                    "title": title,
                    "content": content,
                    "excerpt": excerpt,
                    "is_published": published,
                    "created_by": author,
                },
            )
            if created:
                # Publications échelonnées : l'article à la une doit être le plus
                # récent, pas le premier de la liste.
                NewsPost.objects.filter(pk=post.pk).update(
                    created_at=now - timedelta(days=i * 12 + 2),
                    published_at=(now - timedelta(days=i * 12 + 2)) if published else None,
                )
                count += 1

        self.stdout.write(f"Actualités  : {count} créées (sur {len(ACTUALITES)})")

    # ── Files d'attente ─────────────────────────────────────────────────────

    def _seed_pending(self, members, titles):
        """
        Demandes de titres en attente, pour que l'onglet « À traiter » du
        Pilotage et celui d'« Utilisateurs et rôles » aient de la matière.
        """
        if not titles:
            return

        count = 0
        for i, member in enumerate(members[5:9]):
            """
            La correspondance porte sur le MEMBRE seul, et le titre est choisi
            par index — pas par tirage.

            Avec `get_or_create(member=…, title=RNG.choice(titles))`, le second
            passage tirait un autre titre, ne retrouvait donc pas la demande
            existante, et en créait une deuxième pour le même membre. Le produit
            n'autorise de toute façon qu'une demande à la fois : « une seule
            modification de titre est autorisée ».
            """
            _, created = TitleRequest.objects.get_or_create(
                member=member,
                status="pending",
                defaults={"title": titles[i % len(titles)]},
            )
            if created:
                count += 1

        pending_accounts = sum(1 for m in members if m.status == "pending")
        self.stdout.write(
            f"En attente  : {count} demandes de titres, "
            f"{pending_accounts} comptes à valider"
        )
