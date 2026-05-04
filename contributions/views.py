from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Donation, DonationArchive
from .serializers import DonationSerializer


class DonationArchiveSerializerMixin:
    def _archive_serializer(self, archive):
        return {
            'id': archive.id,
            'name': archive.name,
            'description': archive.description,
            'created_by': archive.created_by_id,
            'created_at': archive.created_at,
            'total_amount': archive.total_amount,
            'total_count': archive.total_count,
        }


class DonationViewSet(DonationArchiveSerializerMixin, viewsets.ModelViewSet):
    serializer_class = DonationSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.OrderingFilter, filters.SearchFilter]
    ordering_fields = ['created_at', 'amount']
    search_fields = ['donor__first_name', 'donor__last_name', 'donor__email', 'donor__phone', 'amount']

    def get_queryset(self):
        user = self.request.user
        base = Donation.objects.select_related('campaign', 'donor', 'collector', 'target_daara', 'archive_id')

        if user.role == 'admin':
            return base

        if user.role == 'collector':
            # Multi-Daara collection: no daara restriction.
            return base.filter(collector=user)

        if user.role == 'chef_daara':
            if user.daara_id:
                return base.filter(donor__daara=user.daara, archive_id__isnull=True)
            return Donation.objects.none()

        return base.filter(donor=user, archive_id__isnull=True)

    def perform_create(self, serializer):
        user = self.request.user
        extra_kwargs = {}

        if user.role == 'collector':
            extra_kwargs['collector'] = user

        donor = serializer.validated_data.get('donor')
        if not donor:
            donor = user
            extra_kwargs['donor'] = donor

        if donor.daara_id:
            extra_kwargs['target_daara_id'] = donor.daara_id

        serializer.save(**extra_kwargs)

    @action(detail=True, methods=['post'])
    def pay(self, request, pk=None):
        donation = self.get_object()
        payment_method = request.data.get('payment_method')
        wire_reference = request.data.get('wire_reference', '').strip()
        if not payment_method:
            return Response({'error': 'Méthode de paiement requise.'}, status=400)

        if payment_method == Donation.PaymentMethod.VIREMENT:
            if not wire_reference:
                return Response({'error': 'Référence de virement requise.'}, status=400)
            donation.payment_method = Donation.PaymentMethod.VIREMENT
            donation.payment_status = Donation.PaymentStatus.PENDING_WIRE
            donation.wire_reference = wire_reference
            donation.save(update_fields=['payment_method', 'payment_status', 'wire_reference', 'updated_at'])
            return Response({'status': 'pending_wire', 'detail': 'Virement déclaré, en attente de confirmation admin.'})

        if payment_method not in ['orange_money', 'wave', 'visa', 'mastercard', 'bictorys']:
            return Response({'error': 'Méthode de paiement non supportée.'}, status=400)

        try:
            from .services.bictorys import initiate_bictorys_payment

            result = initiate_bictorys_payment(donation, payment_method)
            return Response(result)
        except Exception as e:
            return Response({'error': str(e)}, status=400)

    @action(detail=False, methods=['post'], url_path='admin/create-archive', permission_classes=[permissions.IsAdminUser])
    def create_archive(self, request):
        name = (request.data.get('name') or '').strip()
        description = (request.data.get('description') or '').strip()
        if not name:
            return Response({'detail': 'Le nom de l’archive est requis.'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            visible = Donation.objects.filter(archive_id__isnull=True, payment_status=Donation.PaymentStatus.CONFIRMED)
            if not visible.exists():
                return Response({'detail': 'Aucune contribution à archiver.'}, status=status.HTTP_400_BAD_REQUEST)

            totals = visible.aggregate(total_amount=Sum('amount'), total_count=Count('id'))
            archive = DonationArchive.objects.create(
                name=name,
                description=description,
                created_by=request.user,
                total_amount=totals['total_amount'] or 0,
                total_count=totals['total_count'] or 0,
            )
            visible.update(archive_id=archive.id)

        return Response(self._archive_serializer(archive), status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['get'], url_path='admin/archives', permission_classes=[permissions.IsAdminUser])
    def list_archives(self, request):
        archives = DonationArchive.objects.all().order_by('-created_at')
        return Response([self._archive_serializer(a) for a in archives])

    @action(detail=True, methods=['get'], url_path='admin/archive-donations', permission_classes=[permissions.IsAdminUser])
    def archive_donations(self, request, pk=None):
        donations = Donation.objects.filter(archive_id=pk).select_related('donor', 'campaign', 'collector')
        return Response(DonationSerializer(donations, many=True).data)

    @action(detail=False, methods=['get'], url_path='admin/pending-wire', permission_classes=[permissions.IsAdminUser])
    def pending_wire(self, request):
        qs = Donation.objects.filter(payment_status=Donation.PaymentStatus.PENDING_WIRE).order_by('-created_at')
        return Response(DonationSerializer(qs, many=True).data)

    @action(detail=True, methods=['post'], url_path='admin/confirm-wire', permission_classes=[permissions.IsAdminUser])
    def confirm_wire(self, request, pk=None):
        donation = self.get_object()
        if donation.payment_status != Donation.PaymentStatus.PENDING_WIRE:
            return Response({'detail': 'Ce don n’est pas en attente de virement.'}, status=status.HTTP_400_BAD_REQUEST)

        donation.payment_status = Donation.PaymentStatus.CONFIRMED
        donation.validated_by = request.user
        donation.validated_at = timezone.now()
        donation.save(update_fields=['payment_status', 'validated_by', 'validated_at', 'updated_at'])
        return Response(DonationSerializer(donation).data)

