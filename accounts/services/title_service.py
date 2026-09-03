from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import TitleRequest, User
from comms.notify import notify


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

    notify(
        member,
        code='titre_approuve',
        titre="Demande de titre approuvée",
        message=f"Votre demande a été approuvée. Nouveau titre : {request_obj.title.name}.",
        contexte={'titre': request_obj.title.name, 'note': note or ''},
    )
    return request_obj


@transaction.atomic
def refuse_title_request(request_obj: TitleRequest, admin: User, note: str = "") -> TitleRequest:
    request_obj.status = TitleRequest.Status.REFUSED
    request_obj.reviewed_by = admin
    request_obj.reviewed_at = timezone.now()
    request_obj.note = note or ""
    request_obj.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'note'])

    # Le refus ne consomme PAS l'unique changement de titre : le membre peut
    # redemander. Il n'a aucun moyen de le savoir si on ne le lui dit pas.
    notify(
        request_obj.member,
        code='titre_refuse',
        titre="Demande de titre refusée",
        message="Votre demande n'a pas été retenue. Vous pouvez en faire une nouvelle.",
        contexte={'titre': request_obj.title.name, 'note': note or ''},
    )
    return request_obj

