"""Prévient les organisateurs qu'un Ndiguel approche de son échéance.

Seul courriel du catalogue sans déclencheur possible dans une requête : rien
ne « se passe » à J-5 d'une échéance, il faut aller regarder. D'où une commande,
lancée par `cron`.

    # tous les jours à 8 h, heure de Dakar (UTC)
    0 8 * * *  cd /app && python manage.py rappel_echeances

Idempotente dans la journée : un Ndiguel n'est retenu que si son échéance
tombe exactement dans l'un des seuils. Relancer la commande le même jour
renverrait le même message — d'où `--simuler` pour vérifier avant.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from comms.notify import notify
from contributions.models import Donation
from events.models import Campaign

# Jours restants déclenchant un rappel. Trois seuils plutôt qu'un : à J-15 on
# peut encore mobiliser, à J-7 on relance, à J-2 c'est le dernier appel.
SEUILS = (15, 7, 2)


class Command(BaseCommand):
    help = "Envoie un rappel aux organisateurs des Ndiguels proches de l'échéance."

    def add_arguments(self, parser):
        parser.add_argument(
            '--simuler',
            action='store_true',
            help="Affiche les rappels sans rien envoyer.",
        )
        parser.add_argument(
            '--seuils',
            help=f"Jours restants, séparés par des virgules (défaut : {','.join(map(str, SEUILS))}).",
        )

    def handle(self, *args, **options):
        simuler = options['simuler']
        seuils = SEUILS
        if options.get('seuils'):
            try:
                seuils = tuple(int(s) for s in options['seuils'].split(','))
            except ValueError:
                self.stderr.write("--seuils attend des entiers séparés par des virgules.")
                return

        aujourd_hui = timezone.now().date()
        dates_visees = {aujourd_hui + timedelta(days=n): n for n in seuils}

        campagnes = (
            Campaign.objects
            .filter(status=Campaign.Status.ACTIVE, deadline__in=dates_visees.keys())
            .select_related('organizer')
        )

        envoyes = 0
        for campagne in campagnes:
            if not campagne.organizer:
                # Un Ndiguel sans responsable désigné n'a personne à prévenir.
                # C'est en soi une anomalie, mais pas à cette commande de la
                # traiter : elle la signale.
                self.stdout.write(self.style.WARNING(
                    f"  « {campagne.name} » arrive à échéance sans organisateur désigné."
                ))
                continue

            restants = dates_visees[campagne.deadline]
            collecte = (
                Donation.objects
                .filter(campaign=campagne, payment_status=Donation.PaymentStatus.CONFIRMED)
                .aggregate(total=Sum('amount'))['total'] or 0
            )
            objectif = campagne.goal_amount or 0
            pourcentage = round(collecte * 100 / objectif) if objectif else 0

            delai = "demain" if restants == 1 else f"dans {restants} jours"
            ligne = (
                f"  « {campagne.name} » — {delai} · "
                f"{collecte:,.0f}/{objectif:,.0f} FCFA ({pourcentage} %) · "
                f"{campagne.organizer.email or campagne.organizer.phone}"
            ).replace(',', ' ')

            if simuler:
                self.stdout.write(ligne)
                envoyes += 1
                continue

            notify(
                campagne.organizer,
                code='ndiguel_echeance',
                titre=f"Le Ndiguel « {campagne.name} » se termine {delai}",
                message=f"Collecté : {collecte:,.0f} FCFA sur {objectif:,.0f} FCFA ({pourcentage} %).".replace(',', ' '),
                contexte={
                    'campagne': campagne.name,
                    'delai': delai,
                    'collecte': f'{collecte:,.0f}'.replace(',', ' '),
                    'objectif': f'{objectif:,.0f}'.replace(',', ' '),
                    'pourcentage': pourcentage,
                },
            )
            self.stdout.write(ligne)
            envoyes += 1

        if not envoyes:
            self.stdout.write(self.style.SUCCESS(
                "Aucun Ndiguel n'arrive à échéance aux seuils "
                f"{', '.join(f'J-{n}' for n in seuils)}."
            ))
            return

        verbe = "seraient envoyés" if simuler else "envoyés"
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{envoyes} rappel(s) {verbe}."))
