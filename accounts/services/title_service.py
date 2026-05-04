from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import TitleRequest, User
from comms.models import Notification


@transaction.atomic
def approve_title_request(request_obj: TitleRequest, admin: User, note: str = "") -> TitleRequest:
    request_obj = TitleRequest.objects.select_related('member', 'title').get(pk=request_obj.pk)

    if request_obj.member.title_change_count >= 1:
        raise ValidationError("Ce membre a déjà modifié son titre. Une seule modification est autorisée.")

    member = request_obj.member
    member.title = request_obj.title
    member.title_change_count = member.title_change_count + 1
    member.title_changed_at = timezone.now()
    member.save(update_fields=['title', 'title_change_count', 'title_changed_at'])

    request_obj.status = TitleRequest.Status.APPROVED
    request_obj.reviewed_by = admin
    request_obj.reviewed_at = timezone.now()
    request_obj.note = note or ""
    request_obj.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'note'])

    Notification.objects.create(
        user=member,
        title="Demande de titre approuvée",
        message=f"Votre demande a été approuvée. Nouveau titre: {request_obj.title.name}.",
    )
    return request_obj


@transaction.atomic
def refuse_title_request(request_obj: TitleRequest, admin: User, note: str = "") -> TitleRequest:
    request_obj.status = TitleRequest.Status.REFUSED
    request_obj.reviewed_by = admin
    request_obj.reviewed_at = timezone.now()
    request_obj.note = note or ""
    request_obj.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'note'])

    Notification.objects.create(
        user=request_obj.member,
        title="Demande de titre refusée",
        message="Votre demande de titre a été refusée. Consultez la note de l'administrateur.",
    )
    return request_obj

