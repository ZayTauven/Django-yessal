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

    payload = {
        "merchantReference": str(uuid.uuid4()),
        "amount": int(donation.amount),
        "currency": "XOF",
        "country": "SN",
        "paymentReference": payment_reference,
        "successRedirectUrl": f"{public_base}/contributions/success/{donation.id}/",
        "errorRedirectUrl": f"{public_base}/contributions/error/{donation.id}/",
        "customer": {
            "name": f"{donation.donor.first_name} {donation.donor.last_name}",
            "phone": donation.donor.phone or "",
            "email": donation.donor.email,
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
        donation.save(update_fields=["external_ref", "bictorys_ref", "checkout_url", "payment_status"])

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
