"""Prévenir un membre : en base, par courriel, d'un seul geste.

═══════════════════════════════════════════════════════════════════════════
Pourquoi un service plutôt que des appels dispersés
═══════════════════════════════════════════════════════════════════════════
Le produit avait déjà des `Notification.objects.create(...)` répartis dans six
fichiers. Y ajouter des `send_to_user(...)` à côté aurait doublé la dispersion,
et surtout : les deux se seraient désynchronisés au premier changement de
formulation. On aurait lu un message dans l'application et un autre dans sa
boîte, pour le même événement.

`notify()` fait les deux, une fois. Le titre et le message de la notification
en base restent explicites — ils sont courts et se lisent dans une liste — et
le courriel reçoit le contexte dont son gabarit a besoin.

═══════════════════════════════════════════════════════════════════════════
Ce que ce module NE fait pas
═══════════════════════════════════════════════════════════════════════════
Il n'échoue jamais. Ni l'écriture en base ni l'envoi ne doivent pouvoir
annuler l'action métier qui les déclenche : confirmer un virement compte plus
que prévenir qu'il est confirmé.

Il n'envoie pas de courriel à qui n'a pas d'adresse — cas courant, la moitié
des comptes sont créés au téléphone. La notification en base, elle, est
toujours écrite : c'est le canal qui atteint tout le monde.
"""

import logging

from django.contrib.auth import get_user_model

from core.mail import send_to_user, send_to_users

logger = logging.getLogger(__name__)


def notify(user, *, code, titre, message, contexte=None, courriel=True):
    """Prévient un membre en base, et par courriel si une adresse existe.

    `code` désigne le gabarit (voir `core.mail.SUJETS` et docs/EMAILS.md).
    `titre` et `message` alimentent la notification en base — garder des
    formulations proches de celles du courriel, ce sont les mêmes faits.

    `courriel=False` pour un événement trop fréquent pour justifier un envoi
    (un message de salon, par exemple) : la notification en base suffit.
    """
    # Import différé : comms.models importe des services qui importent des
    # modèles, et un import de haut niveau referme la boucle.
    from .models import Notification

    if user is None:
        return

    try:
        Notification.objects.create(user=user, title=titre, message=message)
    except Exception:
        logger.exception("Notification non enregistrée pour le membre %s", getattr(user, 'pk', '?'))

    if courriel:
        send_to_user(user, code, contexte or {})


def notify_many(users, *, code, titre, message, contexte=None, contexte_pour=None):
    """Même chose pour un ensemble de destinataires.

    Les notifications partent en un seul `bulk_create` — six cents INSERT
    unitaires sur un changement de date de fête tiendraient la requête. Les
    courriels partent dans un fil séparé, sur une seule connexion SMTP.
    """
    from .models import Notification

    destinataires = [u for u in users if u is not None]
    if not destinataires:
        return

    try:
        Notification.objects.bulk_create(
            [Notification(user=u, title=titre, message=message) for u in destinataires],
            batch_size=500,
        )
    except Exception:
        logger.exception("Notifications de masse non enregistrées (%s)", code)

    send_to_users(destinataires, code, contexte_pour=contexte_pour, contexte=contexte)


def administrateurs():
    """Les administrateurs actifs — destinataires des courriels internes.

    Recalculé à chaque appel plutôt que mis en cache : un administrateur
    nommé ce matin doit recevoir les demandes de cet après-midi.
    """
    User = get_user_model()
    return list(User.objects.filter(role=User.Role.ADMIN, is_active=True))


def nom_de(user) -> str:
    """Nom lisible d'un membre, pour le corps d'un message.

    `get_full_name()` renvoie une chaîne vide quand ni prénom ni nom ne sont
    renseignés — fréquent sur les comptes créés au téléphone en tournée. On
    retombe alors sur ce qui identifie la personne.
    """
    if not user:
        return "—"
    nom = (user.get_full_name() or '').strip()
    return nom or user.email or user.phone or f"membre #{user.pk}"
