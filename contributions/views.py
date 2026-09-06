from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from comms.notify import administrateurs, nom_de, notify
from core.mail import send_to_user

from .models import Donation, DonationArchive
from .serializers import DonationSerializer


def montant_lisible(valeur) -> str:
    """« 150000 » → « 150 000 », avec une espace insécable.

    Une espace ordinaire laisserait un client de messagerie couper la ligne
    entre « 150 » et « 000 » — un montant coupé en deux se lit de travers, et
    sur un reçu de paiement c'est le chiffre qui compte.
    """
    try:
        entier = int(round(float(valeur)))
    except (TypeError, ValueError):
        return str(valeur)
    return f'{entier:,}'.replace(',', '\u00a0')


def _iban_exploitable(iban) -> bool:
    """Un IBAN sur lequel on peut réellement virer.

    ⚠ Le contrôle « non vide » ne suffit pas. `BANK_IBAN` vaut aujourd'hui
    « SN28 XXXX XXXX XXXX XXXX XXXX XXX » — le gabarit d'exemple, hérité des
    valeurs de repli du tableau de bord. Servi tel quel, il s'affiche comme une
    donnée véritable : un membre debout devant son guichet recopierait des X.

    Un gabarit non rempli est PIRE qu'une absence, parce qu'il a l'air d'une
    réponse. On exige donc de la substance : au moins dix chiffres, ce qu'aucun
    gabarit n'a et qu'aucun IBAN réel n'a en dessous (le format sénégalais en
    porte vingt-quatre après « SN »).
    """
    if not iban:
        return False
    return sum(c.isdigit() for c in str(iban)) >= 10


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

        # 1. Portée autorisée par le rôle.
        #
        # ═══════════════════════════════════════════════════════════════════
        # 🔴 L'ARCHIVAGE EFFAÇAIT L'HISTORIQUE PERSONNEL DES MEMBRES
        # ═══════════════════════════════════════════════════════════════════
        # `archive_id__isnull=True` s'appliquait aussi à la vue d'un membre sur
        # SES PROPRES dons. Or `create_archive` pose `archive_id` sur TOUS les
        # dons confirmés d'un coup : une seule opération d'administration
        # vidait donc l'historique confirmé de chaque membre de la confrérie,
        # définitivement, sans que personne l'ait voulu.
        #
        # Le symptôme se lisait sur les captures du 2026-09-06 — « Total
        # confirmé : 0 FCFA · 0 Jëfs confirmés » à côté de deux Jëfs en attente
        # bien présents : le filtre par statut n'y était pour rien, les
        # confirmés avaient simplement été archivés.
        #
        # L'archive est un instrument de COMPTABILITÉ, pas une suppression. Ce
        # qu'un membre a donné lui appartient : il le voit archivé ou non. On
        # ne garde l'exclusion que là où elle a un sens — le registre de
        # travail d'un chef de Daara, dont les livres clos sortent.
        # ═══════════════════════════════════════════════════════════════════
        if user.role == 'admin':
            queryset = base
        elif user.role == 'collector':
            # Multi-Daara collection: no daara restriction.
            #
            # ⚠ Le filtre était `collector=user` SEUL : un collecteur voyait ce
            # qu'il avait encaissé pour autrui et JAMAIS ce qu'il avait donné
            # lui-même. Il est talibé avant d'être collecteur ; ses propres
            # Jëfs sont les siens.
            queryset = base.filter(Q(collector=user) | Q(donor=user))
        elif user.role == 'chef_daara':
            if user.daara_id:
                # Registre du Daara : les livres clos en sortent, mais jamais
                # les Jëfs du chef lui-même.
                queryset = base.filter(
                    Q(donor__daara=user.daara, archive_id__isnull=True) | Q(donor=user)
                )
            else:
                return Donation.objects.none()
        elif user.role == 'tutelle':
            # RG007 : « un compte de tutelle n'a accès qu'à la consultation de
            # ses dons ». Il n'en fait aucun lui-même — les Jëfs faits EN SON
            # NOM portent le tuteur en `donor` et sa fiche en `beneficiary`.
            # Le filtre `donor=user` ne lui rendait donc rien du tout.
            queryset = base.filter(
                Q(donor=user) | Q(beneficiary__linked_user=user)
            )
        else:
            queryset = base.filter(donor=user)

        # 2. Filtre demandé par l'appelant, APPLIQUÉ APRÈS la portée du rôle.
        #
        # L'ordre n'est pas une commodité d'écriture : il est la garantie qu'un
        # paramètre d'URL ne peut jamais ÉLARGIR ce qu'un rôle a le droit de
        # voir. Un chef de Daara qui demande `?user_id=` d'un membre d'un autre
        # Daara obtient une liste vide, pas les dons de ce membre.
        #
        # Le paramètre était envoyé par la fiche membre du front
        # (`getUserDonations`) depuis toujours, mais n'était lu nulle part : la
        # fiche d'un membre affichait donc les dons de TOUT le réseau — 61 Jëfs
        # et 1 933 000 FCFA attribués à une seule personne.
        donor_id = self.request.query_params.get('user_id')
        if donor_id:
            try:
                queryset = queryset.filter(donor_id=int(donor_id))
            except (TypeError, ValueError):
                return Donation.objects.none()

        return queryset

    def perform_create(self, serializer):
        """Rattache le Jëf, puis en accuse réception à son auteur.

        Le rattachement (collecteur, donateur, Daara bénéficiaire) précède
        l'envoi : le courriel annonce ce qui est enregistré, il ne doit pas
        partir avant que ce soit vrai.
        """
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

        donation = serializer.save(**extra_kwargs)

        # Aucun message ne partait à la création : le membre validait son Jëf
        # et n'en gardait aucune trace écrite. Pour un don, c'est le minimum.
        send_to_user(donation.donor, 'jef_enregistre', {
            'donation': donation,
            'montant': montant_lisible(donation.amount),
            'campagne': donation.campaign.name if donation.campaign else '',
            'mode_paiement': donation.get_payment_method_display(),
            'statut': donation.get_payment_status_display(),
        })

    @action(detail=False, methods=['get'], url_path='bank-account')
    def bank_account(self, request):
        """Les coordonnées du compte à créditer par virement.

        Elles n'existaient nulle part en lecture : Django ne les envoyait que
        dans le courriel `virement_instructions`, déclenché APRÈS la
        déclaration, et le tableau de bord les relisait dans ses propres
        variables d'environnement Next. Le mobile n'avait donc aucune source —
        il demandait la référence d'un virement dont il n'avait jamais montré
        la destination, et un membre sans adresse e-mail ne pouvait l'obtenir
        par aucun chemin.

        Réservé aux membres authentifiés : un IBAN d'association n'est pas un
        secret, mais il n'a rien à faire sur une route ouverte — c'est
        exactement la donnée qu'on recopie dans une fausse page de collecte.

        Renvoie 503 plutôt qu'un objet de chaînes vides quand la configuration
        manque. Afficher « IBAN : » suivi de rien conduit un membre à virer sur
        un compte qu'il aura deviné.
        """
        banque = getattr(settings, 'BANK_ACCOUNT', {}) or {}
        if not _iban_exploitable(banque.get('iban')) or not banque.get('account_name'):
            return Response(
                {'detail': "Les coordonnées bancaires ne sont pas configurées."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({
            'bank_name': banque.get('bank_name', ''),
            'iban': banque.get('iban', ''),
            'bic': banque.get('bic', ''),
            'account_name': banque.get('account_name', ''),
            'reference_format': banque.get('reference_format', ''),
        })

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

            # Les coordonnées bancaires par écrit : c'est le courriel le plus
            # rouvert du produit — devant un guichet, parfois plusieurs jours
            # après. Sans lui, le membre doit les redemander.
            banque = getattr(settings, 'BANK_ACCOUNT', {}) or {}
            send_to_user(donation.donor, 'virement_instructions', {
                'donation': donation,
                'montant': montant_lisible(donation.amount),
                'campagne': donation.campaign.name if donation.campaign else '',
                'banque': banque.get('bank_name', ''),
                'titulaire': banque.get('account_name', ''),
                'iban': banque.get('iban', ''),
                'bic': banque.get('bic', ''),
                'reference': donation.wire_reference,
            })
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

        notify(
            donation.donor,
            code='virement_confirme',
            titre='Virement confirmé',
            message=f'Votre virement de {montant_lisible(donation.amount)} FCFA a été confirmé.',
            contexte={
                'donation': donation,
                'montant': montant_lisible(donation.amount),
                'campagne': donation.campaign.name if donation.campaign else '',
                'date_confirmation': timezone.localtime(donation.validated_at).strftime('%d/%m/%Y'),
            },
        )
        return Response(DonationSerializer(donation).data)

