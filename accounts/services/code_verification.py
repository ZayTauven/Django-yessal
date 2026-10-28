"""Codes à usage unique, envoyés par courriel, avant une opération sensible.

Le mot de passe ne suffit pas pour certaines opérations : une session laissée
ouverte sur un poste partagé ne doit pas permettre de couper la collecte en
espèces de tout le Kourel. On redemande donc une preuve de possession de la
boîte de l'administrateur.

Règles :
  · six chiffres, tirés par `secrets` ;
  · gardés en empreinte seulement ;
  · valables DIX minutes, cinq essais, une seule utilisation ;
  · un nouveau code annule les précédents pour le même objet.
"""

import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from accounts.models import CodeVerification
from core.mail import send_to_user

logger = logging.getLogger(__name__)

DUREE_VALIDITE = timedelta(minutes=10)
ESSAIS_MAX = 5

# Ce que l'administrateur lit dans le courriel : « Vous avez demandé à … ».
OBJETS = {
    'collecte_suspendre': 'suspendre la collecte en espèces',
    'collecte_reprendre': 'reprendre la collecte en espèces',
}


class CodeRefuse(Exception):
    """Le code est faux, expiré, épuisé ou déjà servi. Le message se montre."""


def emettre(user, objet: str) -> None:
    """Crée un code et l'envoie. Lève `CodeRefuse` si l'envoi est impossible."""
    if objet not in OBJETS:
        raise ValueError(f"Objet de code inconnu : {objet}")
    if not user.email:
        raise CodeRefuse(
            "Votre compte n'a pas d'adresse e-mail : le code ne peut pas vous être envoyé."
        )

    maintenant = timezone.now()
    CodeVerification.objects.filter(user=user, objet=objet, utilise_le__isnull=True).update(
        utilise_le=maintenant,
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    CodeVerification.objects.create(
        user=user, objet=objet, empreinte=make_password(code),
        expire_le=maintenant + DUREE_VALIDITE,
    )

    # Les comptes de démonstration ont des adresses qui n'existent pas
    # (`admin@yessal.local`) : sans ce relais, l'opération serait impossible à
    # essayer en local. JAMAIS hors DEBUG.
    if settings.DEBUG:
        logger.warning("[DEBUG] Code de vérification %s pour %s : %s", objet, user.email, code)

    envoye = send_to_user(user, 'code_verification', {
        'code': code,
        'objet': OBJETS[objet],
        'duree_validite': '10 minutes',
    })
    if not envoye and not settings.DEBUG:
        raise CodeRefuse("Le courriel n'a pas pu partir. Réessayez dans un instant.")


def verifier(user, objet: str, code: str) -> None:
    """Consomme le code s'il est bon. Lève `CodeRefuse` sinon."""
    saisie = (code or '').strip()
    actif = (
        CodeVerification.objects
        .filter(user=user, objet=objet, utilise_le__isnull=True)
        .order_by('-cree_le')
        .first()
    )
    if actif is None:
        raise CodeRefuse("Aucun code en cours : demandez-en un nouveau.")
    if actif.expire_le < timezone.now():
        raise CodeRefuse("Ce code a expiré : demandez-en un nouveau.")
    if actif.tentatives >= ESSAIS_MAX:
        raise CodeRefuse("Trop d'essais : demandez un nouveau code.")

    if not check_password(saisie, actif.empreinte):
        actif.tentatives += 1
        actif.save(update_fields=['tentatives'])
        restants = ESSAIS_MAX - actif.tentatives
        raise CodeRefuse(
            f"Code incorrect. Encore {restants} essai(s)." if restants else
            "Code incorrect. Demandez un nouveau code."
        )

    actif.utilise_le = timezone.now()
    actif.save(update_fields=['utilise_le'])
