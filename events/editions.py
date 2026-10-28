"""Les éditions d'une série : les créer à temps, les annoncer, les confirmer.

═══════════════════════════════════════════════════════════════════════════
Ce qui protège de l'heure et des doublons
═══════════════════════════════════════════════════════════════════════════
  · Rien ne se passe « à minuit pile ». Une édition est créée PLUSIEURS JOURS
    avant son ouverture, avec ses dates (`opens_on`, `deadline`). C'est le
    serveur qui la fait passer d'« À venir » à « En cours » en comparant ces
    dates au jour de Dakar (`Campaign.get_effective_status`) : même si le
    planificateur saute un jour, le Ndiguel s'ouvre à l'heure.
  · Une seule édition par série et par date (contrainte en base) : relancer
    la génération, ou la lancer depuis deux processus, ne crée rien de plus.
  · Une édition dont la fenêtre est déjà close n'est jamais créée après coup :
    on ne rattrape pas un Toggou passé.
  · Le jour vient toujours de l'appelant (`aujourd_hui`), jamais d'une horloge
    lue ici : les fonctions se testent à n'importe quelle date.
"""

import logging
from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Campaign, Fete
from .occurrences import date_longue, fenetre, occurrence_en_cours, prochaine

logger = logging.getLogger(__name__)

# Combien de jours avant son OUVERTURE une édition est créée. Assez pour que
# l'administration retouche le propos et la photo de la semaine ; pour un
# hégirien, assez pour attendre l'annonce de la date et la confirmer.
AVANCE = {
    Fete.Recurrence.WEEKLY: 3,
    Fete.Recurrence.QUARTERLY: 30,
    Fete.Recurrence.ANNUAL: 60,
    Fete.Recurrence.ANNUAL_HIJRI: 60,
    Fete.Recurrence.NONE: 60,
}


def nom_edition(fete, occurrence: date) -> str:
    return f"{fete.name} — {date_longue(occurrence)}"


def creer_edition(fete, occurrence: date) -> tuple[Campaign, bool]:
    """L'édition de cette occurrence : celle qui existe, ou une nouvelle.

    Rend `(édition, créée)`. Reprend de la série la description et l'objectif ;
    l'administration les retouche ensuite pour CETTE édition.
    """
    existante = Campaign.objects.filter(fete=fete, occurrence_date=occurrence).first()
    if existante:
        return existante, False

    ouverture, cloture = fenetre(fete, occurrence)
    hegirien = fete.recurrence == Fete.Recurrence.ANNUAL_HIJRI
    try:
        with transaction.atomic():
            edition = Campaign.objects.create(
                name=nom_edition(fete, occurrence),
                description=fete.description,
                goal_amount=fete.objectif_par_defaut,
                fete=fete,
                occurrence_date=occurrence,
                opens_on=ouverture,
                deadline=cloture,
                status=Campaign.Status.ACTIVE,
                a_confirmer=hegirien,
            )
    except IntegrityError:
        # Un autre processus vient de la créer : c'est la sienne qui compte.
        return Campaign.objects.get(fete=fete, occurrence_date=occurrence), False

    logger.info("Édition créée : %s (ouverture %s, clôture %s).", edition.name, ouverture, cloture)
    if hegirien:
        from comms.notify import administrateurs, notify_many

        notify_many(
            administrateurs(),
            code='edition_a_confirmer',
            titre=f"Date à confirmer : {fete.name}",
            message=(
                f"La prochaine édition de « {fete.name} » est estimée au "
                f"{date_longue(occurrence)} (calendrier hégirien). Confirmez ou "
                "corrigez la date selon l'annonce : les membres ne la voient pas avant."
            ),
            courriel=False,
        )
    return edition, True


def generer(aujourd_hui: date) -> list[Campaign]:
    """Crée les éditions qui doivent exister à ce jour. Rend celles créées."""
    creees = []
    for fete in Fete.objects.filter(is_active=True, auto_editions=True):
        occurrence = occurrence_en_cours(fete, aujourd_hui)
        if not occurrence:
            continue
        ouverture, _ = fenetre(fete, occurrence)
        if aujourd_hui < ouverture - timedelta(days=AVANCE.get(fete.recurrence, 3)):
            continue
        edition, creee = creer_edition(fete, occurrence)
        if creee:
            creees.append(edition)
    return creees


def preparer_prochaine(fete, aujourd_hui: date) -> tuple[Campaign | None, bool]:
    """Le bouton « Préparer la prochaine édition », sans attendre le planificateur.

    Prépare l'occurrence dont la fenêtre court encore, ou à défaut la suivante.
    """
    occurrence = occurrence_en_cours(fete, aujourd_hui)
    if not occurrence:
        return None, False
    if Campaign.objects.filter(fete=fete, occurrence_date=occurrence).exists():
        suivante = prochaine(fete, occurrence + timedelta(days=1))
        if suivante:
            occurrence = suivante
    return creer_edition(fete, occurrence)


def confirmer_date(edition: Campaign, occurrence: date) -> Campaign:
    """L'administration confirme (ou corrige) la date d'une édition hégirienne."""
    if not edition.fete_id:
        raise ValueError("Ce Ndiguel n'appartient à aucune série.")
    autre = Campaign.objects.filter(fete=edition.fete, occurrence_date=occurrence).exclude(pk=edition.pk)
    if autre.exists():
        raise ValueError("Une autre édition de cette série porte déjà cette date.")
    ouverture, cloture = fenetre(edition.fete, occurrence)
    edition.occurrence_date = occurrence
    edition.opens_on = ouverture
    edition.deadline = cloture
    edition.name = nom_edition(edition.fete, occurrence)
    edition.a_confirmer = False
    edition.save(update_fields=['occurrence_date', 'opens_on', 'deadline', 'name', 'a_confirmer', 'updated_at'])
    return edition


def annoncer(aujourd_hui: date) -> list[Campaign]:
    """Prévient tous les membres des éditions ouvertes et pas encore annoncées.

    Notification dans l'application et push, JAMAIS de courriel : un Toggou
    chaque semaine ferait un envoi de masse hebdomadaire, et Gmail plafonne
    autour de 500 destinataires par jour. Une édition ouverte hier et pas
    annoncée (planificateur arrêté) l'est aujourd'hui : elle est encore
    ouverte, l'information sert encore.
    """
    from accounts.models import User
    from comms.notify import notify_many

    annoncees = []
    candidates = Campaign.objects.filter(
        fete__isnull=False, occurrence_date__isnull=False,
        ouverture_annoncee_le__isnull=True, a_confirmer=False,
    ).select_related('fete')
    for edition in candidates:
        if edition.get_effective_status(aujourd_hui) != Campaign.Status.ACTIVE:
            continue
        # Marquée AVANT l'envoi : un second planificateur ne l'annonce pas deux fois.
        marquees = Campaign.objects.filter(pk=edition.pk, ouverture_annoncee_le__isnull=True).update(
            ouverture_annoncee_le=timezone.now(),
        )
        if not marquees:
            continue
        notify_many(
            User.objects.filter(status=User.Status.ACTIVE),
            code='edition_ouverte',
            titre=f"{edition.fete.name} : la collecte est ouverte",
            message=(
                f"Le Ndiguel « {edition.name} » est ouvert jusqu'au "
                f"{date_longue(edition.deadline)}. Faites votre Jëf depuis l'application."
            ),
            courriel=False,
            push=True,
        )
        annoncees.append(edition)
        logger.info("Ouverture annoncée : %s.", edition.name)
    return annoncees
