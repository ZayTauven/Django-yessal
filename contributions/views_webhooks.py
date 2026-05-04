import json
import logging
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.conf import settings
from .models import Donation

logger = logging.getLogger(__name__)

@csrf_exempt
@require_POST
def bictorys_webhook(request):
    """
    Endpoint to receive Bictorys payment notifications.
    """
    # 1. Validate Webhook Secret
    received_secret = request.headers.get("X-Secret-Key")
    expected_secret = getattr(settings, 'BICTORYS_WEBHOOK_SECRET', None)
    
    if expected_secret and received_secret != expected_secret:
        logger.warning(f"Invalid Bictorys webhook secret: {received_secret}")
        return HttpResponseBadRequest("Invalid secret")

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
        # Verify amount if possible (Bictorys amount is in XOF)
        if int(amount_received) >= int(donation.amount):
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
