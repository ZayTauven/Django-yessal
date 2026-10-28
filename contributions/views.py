from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from comms.notify import administrateurs, nom_de, notify
from core.mail import send_to_user

from .models import CashDeposit, Donation, DonationArchive
from .serializers import CashDepositSerializer, DonationSerializer

User = get_user_model()

# Les rôles qui peuvent enregistrer un Jëf AU NOM d'un autre membre. Le
# contrôle ne vivait que dans les interfaces : n'importe quel compte pouvait
# poster un Jëf avec `donor` = quelqu'un d'autre.
COLLECT_ROLES = {'collector', 'chef_daara', 'admin'}


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


def _accuser_encaissement(donation):
    """Le reçu du membre, à l'instant où un collecteur a son argent en main.

    C'est le moment où le cahier ne protégeait personne : le membre remettait
    ses billets et repartait sans trace. Il est prévenu dans l'application,
    par push, et par courriel s'il a une adresse.
    """
    montant = montant_lisible(donation.amount)
    campagne = donation.campaign.name if donation.campaign else ''
    notify(
        donation.donor,
        code='jef_enregistre',
        titre='Jëf encaissé',
        message=(
            f"{nom_de(donation.collector)} a reçu votre Jëf de {montant} FCFA "
            f"pour « {campagne} ». Il sera confirmé dès sa remise à l'administration."
        ),
        contexte={
            'donation': donation,
            'montant': montant,
            'campagne': campagne,
            'mode_paiement': donation.get_payment_method_display(),
            'statut': f"{donation.get_payment_status_display()} ({nom_de(donation.collector)})",
        },
    )


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
    # Ni PUT, ni PATCH, ni DELETE. Un Jëf ne se corrige pas en réécrivant son
    # montant ni en le faisant disparaître : un administrateur l'annule, avec
    # un motif (`admin/cancel`), et la ligne reste dans le registre. Aucune
    # interface n'appelait ces routes, mais elles étaient ouvertes à tous les
    # rôles sur tout ce que leur portée leur montrait.
    http_method_names = ['get', 'post', 'head', 'options']
    filter_backends = [filters.OrderingFilter, filters.SearchFilter]
    ordering_fields = ['created_at', 'amount']
    search_fields = ['donor__first_name', 'donor__last_name', 'donor__email', 'donor__phone', 'amount']

    def get_queryset(self):
        user = self.request.user
        base = Donation.objects.select_related(
            'campaign', 'donor', 'donor__daara__ldd', 'collector', 'target_daara', 'archive_id',
            'beneficiary',
        )

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
            #
            # Il voit aussi les DEMANDES DE COLLECTE ouvertes. Il en était
            # notifié, mais ne pouvait pas les lire : une demande n'a pas encore
            # de collecteur, le filtre ne la rendait donc à personne.
            # (Restreint aux zones du collecteur au lot « Gestion collecteurs ».)
            queryset = base.filter(
                Q(collector=user)
                | Q(donor=user)
                | Q(
                    payment_method=Donation.PaymentMethod.MANUAL,
                    payment_status=Donation.PaymentStatus.PENDING,
                    collector__isnull=True,
                )
            )
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

        # Les deux files du collecteur, toujours DANS la portée du rôle :
        #   ?a_collecter=1 → demandes de collecte ouvertes
        #   ?ma_caisse=1   → ce que j'ai encaissé et pas encore remis
        if self.request.query_params.get('a_collecter'):
            queryset = queryset.filter(
                payment_method=Donation.PaymentMethod.MANUAL,
                payment_status=Donation.PaymentStatus.PENDING,
                collector__isnull=True,
            ).exclude(donor=user)
        if self.request.query_params.get('ma_caisse'):
            queryset = queryset.filter(
                collector=user,
                payment_status=Donation.PaymentStatus.COLLECTED,
                cash_deposit__isnull=True,
            )

        return queryset

    def perform_create(self, serializer):
        """Rattache le Jëf, puis en accuse réception à son auteur.

        Le rattachement (collecteur, donateur, Daara bénéficiaire) précède
        l'envoi : le courriel annonce ce qui est enregistré, il ne doit pas
        partir avant que ce soit vrai.
        """
        user = self.request.user
        donor = serializer.validated_data.get('donor') or self._donateur_herite() or user

        # Pour autrui : seul un rôle de collecte le peut, et c'est alors
        # TOUJOURS celui qui enregistre qui devient le collecteur. Le champ
        # n'était posé que pour le rôle `collector` : quand un chef de Daara
        # ou un administrateur encaissait, rien ne disait qui tenait l'argent.
        pour_autrui = donor.pk != user.pk
        if pour_autrui and user.role not in COLLECT_ROLES:
            raise PermissionDenied(
                "Seul un collecteur peut enregistrer un Jëf au nom d'un autre membre."
            )

        extra_kwargs = {
            'donor': donor,
            'payment_status': Donation.PaymentStatus.PENDING,
        }
        if pour_autrui:
            extra_kwargs['collector'] = user
        if donor.daara_id:
            extra_kwargs['target_daara_id'] = donor.daara_id

        # Des espèces enregistrées pour autrui sont déjà dans la main du
        # collecteur : encaissées, pas confirmées. Seule la remise à
        # l'administration les confirme. En espèces POUR SOI, c'est une
        # demande de collecte : elle reste `pending`.
        especes_en_main = (
            pour_autrui
            and serializer.validated_data.get('payment_method') == Donation.PaymentMethod.MANUAL
        )
        if especes_en_main:
            extra_kwargs['payment_status'] = Donation.PaymentStatus.COLLECTED
            extra_kwargs['collected_at'] = timezone.now()

        donation = serializer.save(**extra_kwargs)

        if especes_en_main:
            _accuser_encaissement(donation)
            return

        # Aucun message ne partait à la création : le membre validait son Jëf
        # et n'en gardait aucune trace écrite. Pour un don, c'est le minimum.
        send_to_user(donation.donor, 'jef_enregistre', {
            'donation': donation,
            'montant': montant_lisible(donation.amount),
            'campagne': donation.campaign.name if donation.campaign else '',
            'mode_paiement': donation.get_payment_method_display(),
            'statut': donation.get_payment_status_display(),
        })

    def _donateur_herite(self):
        """Le membre désigné par `member_id`, que le mobile envoyait.

        Le mode collecte du mobile nommait le membre `member_id`, champ que le
        sérialiseur ne connaît pas : DRF l'ignorait sans erreur, et chaque
        collecte mobile s'enregistrait AU NOM DU COLLECTEUR. Le mobile envoie
        désormais `donor` ; ce relais sert les versions déjà installées.
        """
        brut = self.request.data.get('member_id')
        if brut in (None, ''):
            return None
        try:
            return User.objects.get(pk=int(brut))
        except (TypeError, ValueError, User.DoesNotExist):
            raise ValidationError({'member_id': "Membre introuvable."})

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

        # Payer, c'est le geste de celui qui donne, ou du collecteur qui paie
        # pour lui. Un chef de Daara voit tous les Jëfs de son Daara : il ne
        # paie pas pour autant à la place de chacun.
        if request.user.role != 'admin' and request.user.pk not in (
            donation.donor_id, donation.collector_id,
        ):
            raise PermissionDenied("Ce Jëf n'est ni le vôtre ni l'un de vos encaissements.")

        # 🔴 Rien ne l'empêchait : rappeler `pay` sur un Jëf CONFIRMÉ le
        # rétrogradait en `pending_wire` ou, via Bictorys, en `pending`. Il
        # sortait alors du total du Ndiguel.
        if donation.payment_status != Donation.PaymentStatus.PENDING:
            return Response(
                {'error': "Ce Jëf n'est plus en attente de paiement."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payment_method = request.data.get('payment_method')
        wire_reference = (request.data.get('wire_reference') or '').strip()
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

    # ── Circuit des espèces : demandé → encaissé → confirmé par remise ─────

    @action(detail=True, methods=['post'])
    def collect(self, request, pk=None):
        """Le collecteur déclare avoir reçu l'argent d'une demande de collecte."""
        if request.user.role not in COLLECT_ROLES:
            raise PermissionDenied("Seul un collecteur peut encaisser un Jëf.")

        visible = self.get_object()
        with transaction.atomic():
            donation = Donation.objects.select_for_update().get(pk=visible.pk)
            if (
                donation.payment_method != Donation.PaymentMethod.MANUAL
                or donation.payment_status != Donation.PaymentStatus.PENDING
            ):
                return Response(
                    {'detail': "Ce Jëf n'attend pas de collecte."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if donation.collector_id and donation.collector_id != request.user.pk:
                return Response(
                    {'detail': f"{nom_de(donation.collector)} s'en charge déjà."},
                    status=status.HTTP_409_CONFLICT,
                )
            donation.collector = request.user
            donation.payment_status = Donation.PaymentStatus.COLLECTED
            donation.collected_at = timezone.now()
            donation.save(update_fields=['collector', 'payment_status', 'collected_at', 'updated_at'])

        _accuser_encaissement(donation)
        return Response(DonationSerializer(donation).data)

    @action(detail=False, methods=['get'], url_path='admin/pending-cash', permission_classes=[permissions.IsAdminUser])
    def pending_cash(self, request):
        """Les espèces encaissées et pas encore remises, par collecteur.

        C'est la « caisse » de chacun : ce qu'il détient pour le Kourel, et
        depuis quand.
        """
        rows = (
            Donation.objects
            .filter(payment_status=Donation.PaymentStatus.COLLECTED, cash_deposit__isnull=True)
            .select_related('collector', 'donor', 'donor__daara__ldd', 'campaign', 'beneficiary')
            .order_by('collected_at', 'pk')
        )
        groupes = {}
        for d in rows:
            g = groupes.setdefault(d.collector_id, {
                'collector': d.collector_id,
                'collector_name': nom_de(d.collector),
                'collector_phone': d.collector.phone if d.collector else '',
                'total_amount': Decimal('0'),
                'count': 0,
                'since': d.collected_at,
                'donations': [],
            })
            g['total_amount'] += d.amount
            g['count'] += 1
            g['donations'].append(DonationSerializer(d).data)
        return Response(list(groupes.values()))

    @action(detail=False, methods=['post'], url_path='admin/confirm-cash', permission_classes=[permissions.IsAdminUser])
    def confirm_cash(self, request):
        """Valide une remise : l'administrateur a compté l'argent.

        Corps : `collector` (id), `donations` (ids), `counted_amount` (le total
        réellement compté), `note` facultative. Tout ou rien : un seul Jëf
        incohérent refuse la remise entière.
        """
        try:
            collector_id = int(request.data.get('collector'))
            ids = {int(i) for i in request.data.get('donations') or []}
            compte = Decimal(str(request.data.get('counted_amount')))
        except (TypeError, ValueError, InvalidOperation):
            return Response(
                {'detail': "Collecteur, Jëfs et montant compté sont requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not ids:
            return Response({'detail': "Aucun Jëf dans la remise."}, status=status.HTTP_400_BAD_REQUEST)

        # Celui qui a encaissé ne valide jamais sa propre remise.
        if collector_id == request.user.pk:
            raise PermissionDenied("Un autre administrateur doit valider votre remise.")

        with transaction.atomic():
            jefs = list(Donation.objects.select_for_update().filter(pk__in=ids))
            if len(jefs) != len(ids):
                return Response({'detail': "Un des Jëfs est introuvable."}, status=status.HTTP_400_BAD_REQUEST)
            for d in jefs:
                if d.payment_status != Donation.PaymentStatus.COLLECTED or d.cash_deposit_id:
                    return Response(
                        {'detail': f"Le Jëf n°{d.pk} n'est pas en attente de remise."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if d.collector_id != collector_id:
                    return Response(
                        {'detail': "Une remise ne couvre que les Jëfs d'un seul collecteur."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            total = sum((d.amount for d in jefs), Decimal('0'))
            if compte != total:
                return Response(
                    {
                        'detail': (
                            f"Le montant compté ({montant_lisible(compte)} FCFA) ne correspond pas "
                            f"au total des Jëfs ({montant_lisible(total)} FCFA)."
                        ),
                        'expected': str(total),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            remise = CashDeposit.objects.create(
                collector_id=collector_id,
                validated_by=request.user,
                total_amount=total,
                total_count=len(jefs),
                note=(request.data.get('note') or '').strip(),
            )
            maintenant = timezone.now()
            Donation.objects.filter(pk__in=ids).update(
                payment_status=Donation.PaymentStatus.CONFIRMED,
                validated_by=request.user,
                validated_at=maintenant,
                cash_deposit=remise,
                updated_at=maintenant,
            )

        reference = f"Remise n°{remise.pk}"
        for d in Donation.objects.filter(pk__in=ids).select_related('donor', 'campaign'):
            notify(
                d.donor,
                code='paiement_confirme',
                titre='Jëf confirmé',
                message=f"Votre Jëf de {montant_lisible(d.amount)} FCFA a été remis à l'administration et confirmé.",
                contexte={
                    'donation': d,
                    'montant': montant_lisible(d.amount),
                    'campagne': d.campaign.name if d.campaign else '',
                    'mode_paiement': d.get_payment_method_display(),
                    'reference': reference,
                },
            )
        notify(
            remise.collector,
            code='remise_validee',
            titre='Remise validée',
            message=(
                f"{reference} validée par {nom_de(request.user)} : {len(jefs)} Jëf(s), "
                f"{montant_lisible(total)} FCFA."
            ),
            courriel=False,
        )
        return Response(CashDepositSerializer(remise).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['get'], url_path='admin/cash-deposits', permission_classes=[permissions.IsAdminUser])
    def cash_deposits(self, request):
        remises = CashDeposit.objects.select_related('collector', 'validated_by')
        return Response(CashDepositSerializer(remises, many=True).data)

    @action(detail=True, methods=['post'], url_path='admin/cancel', permission_classes=[permissions.IsAdminUser])
    def cancel(self, request, pk=None):
        """Annule un Jëf non confirmé. Le motif est obligatoire et reste visible.

        Un Jëf confirmé ne s'annule pas ici : il figure déjà dans un total,
        et parfois dans une remise dont le bordereau deviendrait faux.
        """
        motif = (request.data.get('reason') or '').strip()
        if not motif:
            return Response({'detail': "Le motif d'annulation est requis."}, status=status.HTTP_400_BAD_REQUEST)

        visible = self.get_object()
        annulables = (
            Donation.PaymentStatus.PENDING,
            Donation.PaymentStatus.PENDING_WIRE,
            Donation.PaymentStatus.COLLECTED,
        )
        with transaction.atomic():
            donation = Donation.objects.select_for_update().get(pk=visible.pk)
            if donation.payment_status not in annulables:
                return Response(
                    {'detail': "Seul un Jëf non confirmé peut être annulé."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            donation.payment_status = Donation.PaymentStatus.CANCELLED
            donation.cancelled_by = request.user
            donation.cancelled_at = timezone.now()
            donation.cancellation_reason = motif
            donation.save(update_fields=[
                'payment_status', 'cancelled_by', 'cancelled_at', 'cancellation_reason', 'updated_at',
            ])

        message = (
            f"Votre Jëf de {montant_lisible(donation.amount)} FCFA a été annulé "
            f"par l'administration. Motif : {motif}"
        )
        notify(donation.donor, code='jef_annule', titre='Jëf annulé', message=message, courriel=False)
        if donation.collector_id and donation.collector_id != donation.donor_id:
            notify(
                donation.collector,
                code='jef_annule',
                titre='Jëf annulé',
                message=f"Le Jëf n°{donation.pk} de {nom_de(donation.donor)} a été annulé. Motif : {motif}",
                courriel=False,
            )
        return Response(DonationSerializer(donation).data)

