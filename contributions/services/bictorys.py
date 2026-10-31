import logging
import uuid

import requests
from django.conf import settings
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# Les réglages se lisent à l'APPEL, pas à l'import
# ═══════════════════════════════════════════════════════════════════════════
# Ces trois constantes étaient figées au chargement du module, avec des replis
# ('test_key', 127.0.0.1) qui masquaient le vrai défaut : aucune des clés
# BICTORYS_* n'était lue par settings.py. La passerelle appelait donc l'API de
# test avec la clé littérale « test_key », et l'échec ne ressemblait pas à une
# erreur de configuration — juste à un paiement refusé.
#
# Les valeurs sont désormais lues dans settings au moment de l'appel : les
# tests peuvent les surcharger, et un déploiement mal configuré échoue avec un
# message qui dit quoi renseigner.

PAYMENT_TYPE_MAP = {
    'orange_money': 'orange_money',
    'wave':         'wave',
    'visa':         None,   # Checkout flow
    'mastercard':   None,   # Checkout flow
}

# Ce que le Jëf retient du moyen choisi : les cartes héritées (`visa`,
# `mastercard`) ne sont plus émises, elles s'enregistrent comme `bictorys`.
PAYMENT_METHOD_RETENU = {
    'visa': 'bictorys',
    'mastercard': 'bictorys',
}

def initiate_bictorys_payment(donation, payment_method: str) -> dict:
    """
    Initiates a Bictorys charge for a donation.
    Returns the Bictorys response including checkoutUrl for cards or direct response for mobile money.
    """
    if not getattr(settings, 'BICTORYS_ENABLED', False):
        raise ValidationError(
            "La passerelle de paiement n'est pas configurée "
            "(BICTORYS_PUBLIC_KEY / BICTORYS_SECRET_KEY manquantes)."
        )

    base_url = settings.BICTORYS_BASE_URL
    api_key = settings.BICTORYS_PUBLIC_KEY
    public_base = settings.BASE_URL

    payment_reference = f"don_{donation.id}_{uuid.uuid4().hex[:6]}"
    payment_type = PAYMENT_TYPE_MAP.get(payment_method)

    # Une demande de collecte (espèces, en attente) que le membre règle
    # finalement en ligne : le collecteur qui s'en chargeait n'est pas le
    # payeur — c'est le membre, devant son téléphone.
    demande_de_collecte = donation.payment_method == donation.PaymentMethod.MANUAL

    # Celui qui PAIE, pas forcément celui qui donne. Quand un collecteur règle
    # par Wave au nom d'un membre, la demande de validation partait sur le
    # téléphone du membre, qui n'était pas là pour l'accepter.
    payeur = donation.donor if demande_de_collecte else (donation.collector or donation.donor)

    payload = {
        "merchantReference": str(uuid.uuid4()),
        "amount": int(donation.amount),
        "currency": "XOF",
        "country": "SN",
        "paymentReference": payment_reference,
        "successRedirectUrl": f"{public_base}/contributions/success/{donation.id}/",
        "errorRedirectUrl": f"{public_base}/contributions/error/{donation.id}/",
        "customer": {
            "name": f"{payeur.first_name} {payeur.last_name}",
            "phone": payeur.phone or "",
            "email": payeur.email,
            "city": "Dakar",
            "country": "SN",
            "locale": "fr-FR",
        },
        "allowUpdateCustomer": False,
    }

    url = f"{base_url}/pay/v1/charges"
    if payment_type:
        url += f"?payment_type={payment_type}"

    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        # Update donation with references
        donation.external_ref = payment_reference
        donation.bictorys_ref = data.get("id")
        donation.checkout_url = data.get("checkoutUrl")
        donation.payment_status = donation.PaymentStatus.PENDING
        # Le moyen RÉELLEMENT employé. Il restait celui de la création : une
        # demande de collecte payée par Wave demeurait « espèces, en attente »,
        # donc dans la file des collecteurs — l'un d'eux pouvait encaisser en
        # main propre un Jëf que Bictorys confirmait ensuite, et suspendre la
        # collecte l'annulait en masse. Les cartes passent par le checkout.
        donation.payment_method = PAYMENT_METHOD_RETENU.get(payment_method, payment_method)
        champs = ["external_ref", "bictorys_ref", "checkout_url", "payment_status",
                  "payment_method", "updated_at"]
        if demande_de_collecte:
            donation.collector = None
            donation.claimed_at = None
            champs += ["collector", "claimed_at"]
        donation.save(update_fields=champs)

        return {
            "success": True,
            "id": data.get("id"),
            "checkout_url": data.get("checkoutUrl"),
            "payment_reference": payment_reference,
            "raw": data
        }

    except requests.exceptions.RequestException as e:
        # `print` n'atterrit dans aucun journal exploitable, et le détail
        # de l'erreur du prestataire n'a pas à remonter jusqu'au donateur.
        logger.exception("Échec d'initiation du paiement Bictorys (don %s)", donation.id)
        raise ValidationError(
            "Le service de paiement est momentanément indisponible. Réessayez dans un instant."
        ) from e
