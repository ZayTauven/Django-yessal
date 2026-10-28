"""Crée les éditions des Ndiguels récurrents, puis annonce celles qui s'ouvrent.

Lancée chaque matin à 7 h, heure de Dakar, par le planificateur — et pas à
minuit : l'annonce part en push, elle ne doit réveiller personne. L'ouverture,
elle, n'attend pas cette commande : les dates de l'édition la décident dès
00:00 (voir `events/editions.py`).

    python manage.py editions_ndiguels            # créer et annoncer
    python manage.py editions_ndiguels --simuler  # dire ce qui serait créé
    python manage.py editions_ndiguels --jour 2026-10-29
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from events import editions
from events.models import Campaign, Fete
from events.occurrences import fenetre, occurrence_en_cours


class Command(BaseCommand):
    help = "Crée les éditions des Ndiguels récurrents et annonce celles qui s'ouvrent."

    def add_arguments(self, parser):
        parser.add_argument('--simuler', action='store_true', help="Affiche sans rien créer ni annoncer.")
        parser.add_argument('--jour', help="Jour de référence AAAA-MM-JJ (défaut : aujourd'hui à Dakar).")

    def handle(self, *args, **options):
        jour = timezone.localdate()
        if options.get('jour'):
            try:
                jour = date.fromisoformat(options['jour'])
            except ValueError:
                raise CommandError("--jour attend une date AAAA-MM-JJ.")

        if options['simuler']:
            for fete in Fete.objects.filter(is_active=True, auto_editions=True):
                occ = occurrence_en_cours(fete, jour)
                if not occ:
                    self.stdout.write(f"  {fete.name} : aucune occurrence à venir.")
                    continue
                ouverture, cloture = fenetre(fete, occ)
                existe = Campaign.objects.filter(fete=fete, occurrence_date=occ).exists()
                cree_le = ouverture - timedelta(days=editions.AVANCE.get(fete.recurrence, 3))
                self.stdout.write(
                    f"  {fete.name} : {occ} (collecte du {ouverture} au {cloture}) — "
                    + ("existe déjà" if existe else f"sera créée à partir du {cree_le}")
                )
            return

        creees = editions.generer(jour)
        annoncees = editions.annoncer(jour)
        for e in creees:
            self.stdout.write(f"Créée : {e.name}" + (" (date à confirmer)" if e.a_confirmer else ""))
        for e in annoncees:
            self.stdout.write(f"Annoncée : {e.name}")
        if not creees and not annoncees:
            self.stdout.write("Aucune édition à créer ni à annoncer.")
