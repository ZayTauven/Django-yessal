import hmac
import json
import logging
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.conf import settings
from .models import Donation

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def bictorys_webhook(request):
    """
    Point d'entrée des notifications de paiement Bictorys.

    ═══════════════════════════════════════════════════════════════════════
    Cette vue confirme des encaissements. Elle doit refuser par défaut.
    ═══════════════════════════════════════════════════════════════════════
    L'authentification s'écrivait « si un secret est configuré, le vérifier ».
    Or `BICTORYS_WEBHOOK_SECRET` n'était lu nulle part dans settings.py :
    `getattr()` renvoyait None, la condition était fausse, et le contrôle
    sautait entièrement. Cet endpoint — public, exempté de CSRF — acceptait
    donc n'importe quel POST anonyme et marquait le don visé comme encaissé.

    Deux corrections :

      · La clé absente FERME l'accès au lieu de l'ouvrir. Un webhook qu'on ne
        sait pas authentifier ne vaut pas mieux qu'un webhook ouvert.
      · La comparaison passe par `hmac.compare_digest`, insensible au temps :
        `!=` s'arrête au premier octet différent et laisse deviner le secret
        caractère par caractère.
    """
    expected_secret = getattr(settings, 'BICTORYS_WEBHOOK_SECRET', '')
    if not expected_secret:
        logger.error(
            "Webhook Bictorys appelé alors que BICTORYS_WEBHOOK_SECRET n'est "
            "pas configuré : appel rejeté."
        )
        return HttpResponseForbidden("Webhook not configured")

    received_secret = request.headers.get("X-Secret-Key", "")
    if not hmac.compare_digest(received_secret, expected_secret):
        # On ne journalise pas la valeur reçue : elle finirait dans les logs.
        logger.warning("Signature de webhook Bictorys invalide (rejeté).")
        return HttpResponseForbidden("Invalid secret")

    # 2. Parse Payload
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    # 3. Check Required Fields
    required_fields = ["id", "status", "amount", "paymentReference"]
    if not all(field in payload for field in required_fields):
        return HttpResponseBadRequest("Missing required fields")

    payment_ref = payload.get("paymentReference")
    status = payload.get("status", "").lower()
    bictorys_id = payload.get("id")
    amount_received = payload.get("amount")

    # 4. Find Donation
    try:
        donation = Donation.objects.get(external_ref=payment_ref)
    except Donation.DoesNotExist:
        logger.error(f"Donation not found for reference: {payment_ref}")
        # We return 200 to Bictorys to stop retries, but log the error
        return HttpResponse(status=200)

    # 5. Idempotency Check
    if donation.payment_status == Donation.PaymentStatus.CONFIRMED:
        return HttpResponse(status=200)

    # 6. Update Status
    if status in ("succeeded", "authorized"):
        # Le montant vient d'un tiers : un champ non numérique ferait lever
        # int() et renverrait une 500, que Bictorys réessaierait en boucle.
        try:
            amount_received = int(float(amount_received))
        except (TypeError, ValueError):
            logger.warning(
                "Montant illisible dans le webhook du don %s : %r",
                donation.id, amount_received,
            )
            return HttpResponseBadRequest("Invalid amount")

        # Verify amount if possible (Bictorys amount is in XOF)
        if amount_received >= int(donation.amount):
            donation.payment_status = Donation.PaymentStatus.CONFIRMED
            donation.bictorys_ref = bictorys_id
            donation.save(update_fields=["payment_status", "bictorys_ref"])
            
            # Update campaign total
            if hasattr(donation.campaign, 'update_collected_amount'):
                donation.campaign.update_collected_amount()
            
            logger.info(f"Donation {donation.id} confirmed via Bictorys webhook")
        else:
            logger.warning(f"Amount mismatch for donation {donation.id}: expected {donation.amount}, received {amount_received}")
            donation.payment_status = Donation.PaymentStatus.FAILED
            donation.save(update_fields=["payment_status"])

    elif status == "failed":
        donation.payment_status = Donation.PaymentStatus.FAILED
        donation.save(update_fields=["payment_status"])
        logger.info(f"Donation {donation.id} marked as FAILED via Bictorys webhook")

    return HttpResponse(status=200)
