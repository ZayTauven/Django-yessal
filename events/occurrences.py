"""Les dates d'une série : quand tombe la prochaine occurrence d'un Événement.

Toutes les fonctions sont PURES — une règle, une date, une réponse — et ne
lisent jamais l'horloge : c'est l'appelant qui dit quel jour on est (le jour de
Dakar, `timezone.localdate()`). Ce qui les rend testables à n'importe quelle
date, et sûres face à un serveur ou un appareil mal réglé.

═══════════════════════════════════════════════════════════════════════════
Le calendrier hégirien
═══════════════════════════════════════════════════════════════════════════
Le Magal tombe le 18 Safar. Le calendrier musulman est lunaire : onze jours de
moins que l'année grégorienne. Une récurrence « annuelle » grégorienne place
donc le Magal de 2028 le 22 juillet, quand il tombe vers le 11.

On calcule avec le calendrier TABULAIRE (arithmétique, cycle de 30 ans). Il
s'écarte d'un ou deux jours de l'annonce réelle, qui suit l'observation de la
lune : c'est pourquoi une édition hégirienne est créée « à confirmer », et que
l'administration la valide ou la corrige avant que les membres ne la voient.
"""

import calendar
import math
from datetime import date, timedelta

# Jour julien du 1er Muharram de l'an 1 (16 juillet 622, calendrier civil).
_EPOQUE_HEGIRE = 1948440
# `date.toordinal()` + ce décalage = jour julien.
_ORDINAL_VERS_JULIEN = 1721425

MOIS_HEGIRIENS = (
    'Muharram', 'Safar', 'Rabi al-Awwal', 'Rabi al-Thani', 'Joumada al-Oula',
    'Joumada al-Thania', 'Rajab', 'Chaabane', 'Ramadan', 'Chawwal',
    'Dhou al-Qida', 'Dhou al-Hijja',
)


def _julien_depuis_hegire(annee: int, mois: int, jour: int) -> int:
    return (
        jour + math.ceil(29.5 * (mois - 1)) + (annee - 1) * 354
        + (3 + 11 * annee) // 30 + _EPOQUE_HEGIRE - 1
    )


def vers_hegire(d: date) -> tuple[int, int, int]:
    """Date grégorienne → (année, mois, jour) hégiriens, calendrier tabulaire."""
    jd = d.toordinal() + _ORDINAL_VERS_JULIEN
    annee = (30 * (jd - _EPOQUE_HEGIRE) + 10646) // 10631
    mois = min(12, math.ceil((jd - (29 + _julien_depuis_hegire(annee, 1, 1))) / 29.5) + 1)
    jour = jd - _julien_depuis_hegire(annee, mois, 1) + 1
    return annee, mois, jour


def depuis_hegire(annee: int, mois: int, jour: int) -> date:
    """(année, mois, jour) hégiriens → date grégorienne, calendrier tabulaire."""
    return date.fromordinal(_julien_depuis_hegire(annee, mois, jour) - _ORDINAL_VERS_JULIEN)


def _plus_mois(d: date, mois: int) -> date:
    """Même jour, `mois` plus tard ; un 31 devient le dernier jour du mois court."""
    total = d.month - 1 + mois
    annee, m = d.year + total // 12, total % 12 + 1
    return date(annee, m, min(d.day, calendar.monthrange(annee, m)[1]))


def prochaine(fete, a_partir_du: date) -> date | None:
    """La première occurrence de la série tombant LE `a_partir_du` ou après.

    `None` pour une série sans règle exploitable (non récurrente et déjà
    passée, sans date de référence, ou mois hégirien manquant).
    """
    from .models import Fete

    ref = fete.date
    regle = fete.recurrence

    if regle == Fete.Recurrence.ANNUAL_HIJRI:
        mois, jour = fete.hijri_mois, fete.hijri_jour
        if not (mois and jour) and ref:
            _, mois, jour = vers_hegire(ref)
        if not (mois and jour):
            return None
        annee = vers_hegire(a_partir_du)[0]
        for a in (annee - 1, annee, annee + 1):
            candidate = depuis_hegire(a, mois, jour)
            if candidate >= a_partir_du:
                return candidate
        return None

    if not ref:
        return None
    if regle == Fete.Recurrence.NONE:
        return ref if ref >= a_partir_du else None
    if ref >= a_partir_du:
        return ref

    if regle == Fete.Recurrence.WEEKLY:
        ecart = (a_partir_du - ref).days
        return ref + timedelta(days=math.ceil(ecart / 7) * 7)

    pas = {Fete.Recurrence.QUARTERLY: 3, Fete.Recurrence.ANNUAL: 12}.get(regle)
    if not pas:
        return None
    # Compter depuis la référence (et non de proche en proche) : un 31 ne
    # glisse pas au 30 puis au 28 au fil des trimestres.
    n = 1
    while True:
        candidate = _plus_mois(ref, pas * n)
        if candidate >= a_partir_du:
            return candidate
        n += 1


def fenetre(fete, occurrence: date) -> tuple[date, date]:
    """(ouverture, clôture) de l'édition d'une occurrence, bornes incluses."""
    return (
        occurrence - timedelta(days=fete.ouvre_jours_avant),
        occurrence + timedelta(days=fete.ferme_jours_apres),
    )


def occurrence_en_cours(fete, aujourd_hui: date) -> date | None:
    """L'occurrence dont la fenêtre n'est pas encore close à ce jour.

    C'est celle qu'il faut préparer : la prochaine, ou celle d'aujourd'hui si
    la collecte court encore (ferme N jours APRÈS l'événement).
    """
    return prochaine(fete, aujourd_hui - timedelta(days=fete.ferme_jours_apres))


JOURS = ('lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche')
MOIS = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août',
        'septembre', 'octobre', 'novembre', 'décembre')


def date_longue(d: date) -> str:
    """« vendredi 30 octobre 2026 », sans dépendre de la locale du serveur."""
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]} {d.year}"
