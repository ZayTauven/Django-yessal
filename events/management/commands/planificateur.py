"""Le planificateur : lance les tâches datées à l'heure de Dakar.

═══════════════════════════════════════════════════════════════════════════
Pourquoi pas `cron`
═══════════════════════════════════════════════════════════════════════════
`rappel_echeances` attendait un `cron` que personne n'avait posé : au
2026-10-28, le VPS de démo n'en avait AUCUN, et ni les rappels d'échéance ni
l'alerte « Ndiguel clôturé avec des espèces en route » n'étaient jamais partis.
Un `cron` sur l'hôte s'oublie au prochain serveur, et l'image du backend n'en
contient pas.

Ce processus tourne dans son propre conteneur (service `scheduler`), à partir
de la même image : il suit le code à chaque déploiement, sans rien à
réinstaller.

═══════════════════════════════════════════════════════════════════════════
Ce qui garantit l'heure
═══════════════════════════════════════════════════════════════════════════
  · L'heure est celle du SERVEUR (synchronisé par NTP), lue dans le fuseau
    `Africa/Dakar` (`TIME_ZONE`). L'appareil d'un membre n'intervient jamais.
  · Une tâche quotidienne tourne au plus UNE fois par jour : le passage est
    inscrit en base (`ExecutionPlanifiee`), ligne verrouillée pendant la
    décision. Un redémarrage à 9 h rattrape la tâche de 8 h ; un second
    planificateur lancé par erreur ne la rejoue pas.
  · Une journée entière manquée (serveur arrêté) n'est PAS rejouée le
    lendemain : les rappels sont relatifs au jour même, les rejouer
    enverrait deux fois ceux du jour. Le trou est écrit au journal.

    python manage.py planificateur            # boucle, une vérification par minute
    python manage.py planificateur --une-fois # un seul passage (tests, dépannage)
    python manage.py planificateur --etat     # dernier passage de chaque tâche
"""

import logging
import time as horloge
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import DatabaseError, close_old_connections, transaction
from django.utils import timezone

from events.models import ExecutionPlanifiee

logger = logging.getLogger(__name__)

# Le conteneur se dit vivant tant que ce fichier est récent (voir le
# `healthcheck` du service `scheduler`).
TEMOIN = Path('/tmp/planificateur.vivant')
INTERVALLE = 60  # secondes entre deux vérifications


@dataclass(frozen=True)
class Tache:
    nom: str          # clé en base, et nom de la commande lancée
    heure: time       # heure de Dakar, chaque jour


TACHES = (
    # Rappels J-15/7/2 aux organisateurs, et alerte de clôture avec des
    # espèces encore en route (Ndiguels dont l'échéance était hier).
    Tache('rappel_echeances', time(8, 0)),
)


def passage_du_jour(tache: Tache, maintenant: datetime) -> datetime:
    """L'instant prévu aujourd'hui pour cette tâche, dans le fuseau de Dakar."""
    return timezone.make_aware(datetime.combine(maintenant.date(), tache.heure))


def executer_si_du(tache: Tache, maintenant: datetime | None = None, lancer=call_command) -> bool:
    """Lance la tâche si son passage du jour est échu et pas encore fait.

    Rend True si elle a été lancée. Le passage est inscrit AVANT l'exécution :
    une tâche qui échoue n'est pas relancée en boucle toutes les minutes — un
    courriel envoyé à moitié ne doit pas l'être deux fois. L'échec est écrit
    en base et au journal.
    """
    maintenant = timezone.localtime(maintenant or timezone.now())
    prevu = passage_du_jour(tache, maintenant)
    if maintenant < prevu:
        return False

    with transaction.atomic():
        suivi, _ = ExecutionPlanifiee.objects.select_for_update().get_or_create(nom=tache.nom)
        derniere = suivi.derniere_execution
        if derniere and derniere >= prevu:
            return False
        if derniere and timezone.localtime(derniere).date() < maintenant.date() - timedelta(days=1):
            logger.warning(
                "Tâche %r : aucun passage entre le %s et aujourd'hui, journées non rattrapées.",
                tache.nom, timezone.localtime(derniere).date(),
            )
        suivi.derniere_execution = maintenant
        suivi.dernier_resultat = 'en_cours'
        suivi.dernier_message = ''
        suivi.save()

    logger.info("Tâche %r : lancement (prévue à %s).", tache.nom, tache.heure.strftime('%H:%M'))
    try:
        lancer(tache.nom)
        suivi.dernier_resultat = 'ok'
    except Exception as exc:  # une tâche en échec ne doit pas arrêter les autres
        logger.exception("Tâche %r : échec.", tache.nom)
        suivi.dernier_resultat = 'echec'
        suivi.dernier_message = str(exc)[:2000]
    suivi.save(update_fields=['dernier_resultat', 'dernier_message'])
    return True


class Command(BaseCommand):
    help = "Lance les tâches datées (rappels d'échéance…) à l'heure de Dakar."

    def add_arguments(self, parser):
        parser.add_argument('--une-fois', action='store_true', help="Un seul passage, puis sortie.")
        parser.add_argument('--etat', action='store_true', help="Affiche le dernier passage de chaque tâche.")

    def handle(self, *args, **options):
        if options['etat']:
            suivis = {s.nom: s for s in ExecutionPlanifiee.objects.all()}
            for tache in TACHES:
                s = suivis.get(tache.nom)
                quand = timezone.localtime(s.derniere_execution).strftime('%d/%m/%Y %H:%M') if s and s.derniere_execution else 'jamais'
                self.stdout.write(f"{tache.nom:<20} {tache.heure:%H:%M}  dernier : {quand}  {s.dernier_resultat if s else ''}")
            return

        logger.info("Planificateur démarré (%s) : %s.", timezone.get_current_timezone_name(),
                    ', '.join(f"{t.nom} à {t.heure:%H:%M}" for t in TACHES))
        while True:
            self._passage()
            if options['une_fois']:
                return
            horloge.sleep(INTERVALLE)

    def _passage(self):
        # Le planificateur vit des semaines : une connexion fermée par
        # PostgreSQL entre deux passages ne doit pas le faire tomber, ni une
        # table pas encore migrée au tout premier démarrage.
        close_old_connections()
        for tache in TACHES:
            try:
                executer_si_du(tache)
            except DatabaseError:
                logger.exception("Tâche %r : base indisponible, nouvel essai dans une minute.", tache.nom)
        try:
            TEMOIN.touch()
        except OSError:
            pass
