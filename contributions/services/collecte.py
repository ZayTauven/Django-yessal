"""Qui peut collecter quoi, et où.

Trois règles, décidées le 2026-10-28 :

  · un collecteur ne voit que les DEMANDES de ses zones LDD (`User.collecte_zones`) ;
  · un membre ne peut demander une collecte que si un collecteur couvre sa
    zone — sinon l'option disparaît, plutôt que de créer une demande que
    personne ne verra ;
  · quand l'administration suspend la collecte en espèces, plus aucune
    demande ni aucun encaissement ; l'argent déjà encaissé reste à remettre.

Le mode collecteur (enregistrer un Jëf pour autrui) n'est PAS borné aux zones :
un collecteur encaisse chez n'importe quel membre qui le sollicite.
"""

from django.contrib.auth import get_user_model

from accounts.models import PilotageSettings


def collecte_active() -> bool:
    return PilotageSettings.load().collecte_physique_active


def collecteurs_de_zone(ldd_id):
    """Les collecteurs actifs dont les zones couvrent cette LDD."""
    User = get_user_model()
    if not ldd_id:
        return User.objects.none()
    return User.objects.filter(
        role=User.Role.COLLECTOR, status=User.Status.ACTIVE, collecte_zones=ldd_id,
    ).distinct()


def zone_de(user):
    """La LDD d'un membre, par son Daara. `None` s'il n'a pas de Daara."""
    return user.daara.ldd_id if user.daara_id and user.daara else None


def disponibilite(user) -> dict:
    """Ce qu'un membre peut faire en espèces, et pourquoi pas.

    `raison` : `suspendue` (l'administration a coupé), `hors_zone` (aucun
    collecteur ne couvre sa zone, ou il n'a pas de Daara), ou `None`.
    """
    if not collecte_active():
        return {'active': False, 'disponible': False, 'raison': 'suspendue'}
    if not collecteurs_de_zone(zone_de(user)).exists():
        return {'active': True, 'disponible': False, 'raison': 'hors_zone'}
    return {'active': True, 'disponible': True, 'raison': None}


def especes_en_suspens(campaign):
    """L'argent en espèces d'un Ndiguel qui n'a pas fini son circuit.

    Rend `(demandes, encaisses)` : les demandes de collecte encore ouvertes ou
    prises, et les Jëfs encaissés pas encore remis à l'administration.
    """
    from contributions.models import Donation

    base = Donation.objects.filter(campaign=campaign, payment_method=Donation.PaymentMethod.MANUAL)
    return (
        base.filter(payment_status=Donation.PaymentStatus.PENDING),
        base.filter(payment_status=Donation.PaymentStatus.COLLECTED, cash_deposit__isnull=True),
    )


def prevenir_cloture_avec_especes(campaign) -> bool:
    """Prévient les administrateurs qu'un Ndiguel se clôt avec des espèces en route.

    Décision du 2026-10-28 : la clôture ne ferme PAS la porte aux collecteurs —
    ils peuvent encore encaisser et remettre ce qui a été promis ou reçu avant.
    Mais l'administration doit le savoir, sinon ces sommes restent en suspens
    sans que personne ne les réclame. Rend `True` si un message est parti.
    """
    from django.db.models import Sum

    from comms.notify import administrateurs, notify_many
    from contributions.views import montant_lisible

    demandes, encaisses = especes_en_suspens(campaign)
    nb_d, nb_e = demandes.count(), encaisses.count()
    if not nb_d and not nb_e:
        return False

    total = (demandes.aggregate(t=Sum('amount'))['t'] or 0) + (encaisses.aggregate(t=Sum('amount'))['t'] or 0)
    morceaux = []
    if nb_d:
        morceaux.append(f"{nb_d} demande(s) de collecte pas encore encaissée(s)")
    if nb_e:
        morceaux.append(f"{nb_e} Jëf(s) encaissé(s) pas encore remis")
    notify_many(
        administrateurs(),
        code='cloture_especes',
        titre=f"Ndiguel clôturé avec des espèces en route : {campaign.name}",
        message=(
            f"« {campaign.name} » est clôturé alors qu'il reste {' et '.join(morceaux)}, "
            f"soit {montant_lisible(total)} FCFA. Les collecteurs peuvent encore les "
            "encaisser et les remettre."
        ),
        courriel=False,
    )
    return True
