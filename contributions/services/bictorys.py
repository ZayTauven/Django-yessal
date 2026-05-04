import uuid
import requests
from django.conf import settings
from django.core.exceptions import ValidationError

# Bictorys Configuration (should be in settings.py)
BICTORYS_BASE_URL = getattr(settings, 'BICTORYS_BASE_URL', 'https://api.test.bictorys.com')
BICTORYS_API_KEY  = getattr(settings, 'BICTORYS_PUBLIC_KEY', 'test_key')
BASE_URL = getattr(settings, 'BASE_URL', 'http://127.0.0.1:8000')

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
    payment_reference = f"don_{donation.id}_{uuid.uuid4().hex[:6]}"
    payment_type = PAYMENT_TYPE_MAP.get(payment_method)

    payload = {
        "merchantReference": str(uuid.uuid4()),
        "amount": int(donation.amount),
        "currency": "XOF",
        "country": "SN",
        "paymentReference": payment_reference,
        "successRedirectUrl": f"{BASE_URL}/contributions/success/{donation.id}/",
        "errorRedirectUrl": f"{BASE_URL}/contributions/error/{donation.id}/",
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

    url = f"{BICTORYS_BASE_URL}/pay/v1/charges"
    if payment_type:
        url += f"?payment_type={payment_type}"

    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": BICTORYS_API_KEY,
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
        print(f"Bictorys Error: {str(e)}")
        raise ValidationError(f"Erreur lors de l'initiation du paiement Bictorys: {str(e)}")
