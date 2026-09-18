# -*- coding: utf-8 -*-
"""
Importe un ou plusieurs classeurs « DAARA / LDD / CODE LDD » depuis la ligne de
commande.

L'écran d'administration fait la même chose, un fichier à la fois. Cette
commande existe pour deux besoins que l'écran ne couvre pas :

  · **l'essai à blanc.** Par défaut, RIEN N'EST ÉCRIT : la commande affiche ce
    qu'elle ferait et annule la transaction. Un import qui touche quatre cents
    Daaras mérite d'être lu avant d'être subi.
  · **l'ordre des fichiers.** Le jeu de la direction en compte deux, et la
    réconciliation ne raisonne que dans le périmètre du fichier courant. Les
    passer en un seul appel garantit qu'ils s'appliquent dans l'ordre voulu.

    python manage.py importer_ldd fichier.xlsx                  # essai à blanc
    python manage.py importer_ldd fichier.xlsx --appliquer
    python manage.py importer_ldd a.xlsx b.xlsx --mode reconciliation --appliquer
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import LDD, Daara
from accounts.services.ldd_import import ModeImport, importer


class Command(BaseCommand):
    help = "Importe des Daaras et leurs zones territoriales depuis un classeur Excel."

    def add_arguments(self, parser):
        parser.add_argument('fichiers', nargs='+', help="Classeurs .xlsx, dans l'ordre d'application.")
        parser.add_argument(
            '--mode',
            choices=[ModeImport.STRICT, ModeImport.RECONCILIATION],
            default=ModeImport.STRICT,
            help=(
                "strict (défaut) : n'ajoute que ce qui manque. "
                "reconciliation : réaffecte en plus les Daaras dont le rattachement "
                "est manifestement fautif — voir le service pour les garde-fous."
            ),
        )
        parser.add_argument(
            '--appliquer',
            action='store_true',
            help="Écrit réellement. Sans ce drapeau, la transaction est annulée.",
        )
        parser.add_argument('--detail', action='store_true', help="Liste chaque élément.")

    def handle(self, *args, **options):
        fichiers = options['fichiers']
        mode = options['mode']
        appliquer = options['appliquer']
        detail = options['detail']

        avant = (LDD.objects.count(), Daara.objects.count())

        # Une seule transaction pour TOUS les fichiers : si le second échoue,
        # le premier ne reste pas à moitié appliqué — c'est exactement ce que
        # l'ancien import laissait arriver.
        rapports = []
        echec = False
        with transaction.atomic():
            for chemin in fichiers:
                try:
                    flux = open(chemin, 'rb')
                except OSError as e:
                    raise CommandError(f"Fichier illisible : {chemin} ({e})")
                with flux:
                    rapport = importer(flux, mode=mode)
                rapports.append((chemin, rapport))
                if rapport.en_echec:
                    echec = True

            # Les compteurs se lisent AVANT l'annulation, sinon un essai à
            # blanc n'annoncerait jamais que « 27 → 27 » : il montrerait l'état
            # rétabli, pas ce que l'import aurait fait.
            apres = (LDD.objects.count(), Daara.objects.count())

            if echec or not appliquer:
                transaction.set_rollback(True)

        for chemin, rapport in rapports:
            self.stdout.write('')
            self.stdout.write(self.style.MIGRATE_HEADING(f'── {chemin} ──'))
            style = self.style.ERROR if rapport.en_echec else self.style.SUCCESS
            self.stdout.write('  ' + style(rapport.resume()))
            self.stdout.write(
                f'  zones reconnues : {rapport.zones_reconnues}   '
                f'Daaras inchangés : {rapport.daaras_inchanges}'
            )
            self._lister('ERREURS', rapport.erreurs, detail, self.style.ERROR)
            self._lister('lignes ignorées', rapport.lignes_ignorees, detail)
            self._lister('zones créées', rapport.zones_creees, detail)
            self._lister('Daaras réaffectés', rapport.daaras_deplaces, detail)
            self._lister('orthographes alignées', rapport.orthographes_alignees, detail)
            self._lister('Daaras créés', rapport.daaras_crees, detail)
            self._lister('avertissements', rapport.avertissements, detail, self.style.WARNING)

        self.stdout.write('')
        self.stdout.write(f'  zones  : {avant[0]} → {apres[0]}')
        self.stdout.write(f'  Daaras : {avant[1]} → {apres[1]}')

        if echec:
            raise CommandError("Import refusé : rien n'a été écrit.")
        if not appliquer:
            self.stdout.write('')
            self.stdout.write(
                self.style.WARNING(
                    "  ESSAI À BLANC — transaction annulée. Relancez avec --appliquer pour écrire."
                )
            )

    def _lister(self, titre, valeurs, detail, style=None):
        if not valeurs:
            return
        self.stdout.write(f'\n  {titre} ({len(valeurs)})')
        limite = len(valeurs) if detail else 8
        for v in valeurs[:limite]:
            ligne = f'    · {v}'
            self.stdout.write(style(ligne) if style else ligne)
        if len(valeurs) > limite:
            self.stdout.write(f'    … et {len(valeurs) - limite} autres (--detail pour tout voir)')
