"""Remplace l'ancien mot de passe commun des comptes importés.

Jusqu'au 2026-10-31, chaque compte créé par l'import Excel recevait le même
mot de passe, écrit dans le dépôt et affiché par l'écran d'import. Qui le
connaissait ouvrait tout compte importé dont il savait le téléphone ou le
courriel — collecteurs et coordinateurs compris. L'import donne désormais un
provisoire par compte ; cette commande rattrape les comptes créés avant.

Elle ne touche que les comptes qui portent ENCORE l'ancien mot de passe : un
membre qui l'a déjà remplacé n'est pas concerné. Pour chacun, elle tire un
provisoire, ferme toutes ses sessions ouvertes (un intrus déjà connecté perd
la main), et l'inscrit dans un fichier CSV que l'administrateur transmet de
vive voix, comme à l'import.

    python manage.py regenerer_mots_de_passe_import                    # inventaire seul
    python manage.py regenerer_mots_de_passe_import --appliquer --sortie /tmp/provisoires.csv

⚠ Le CSV contient des mots de passe en clair : le sortir du serveur, le
remettre à l'administrateur, puis l'effacer. Jamais sous `media/`.
"""

import csv
from pathlib import Path

from django.contrib.auth.hashers import check_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from accounts.services.user_import import (
    ANCIEN_MOT_DE_PASSE_COMMUN,
    hacher_provisoire,
    mot_de_passe_provisoire,
)


class Command(BaseCommand):
    help = "Donne un provisoire propre à chaque compte qui porte encore l'ancien mot de passe commun."

    def add_arguments(self, parser):
        parser.add_argument(
            '--appliquer',
            action='store_true',
            help="Remplace les mots de passe. Sans lui, la commande ne fait que compter.",
        )
        parser.add_argument(
            '--sortie',
            help="Chemin du CSV des nouveaux provisoires. Obligatoire avec --appliquer.",
        )

    def handle(self, *args, **options):
        appliquer = options['appliquer']
        sortie = options['sortie']
        if appliquer and not sortie:
            raise CommandError("--appliquer exige --sortie : sans fichier, les provisoires seraient perdus.")
        if sortie and Path(sortie).exists():
            raise CommandError(f"{sortie} existe déjà : choisir un autre chemin.")

        concernes = self._comptes_a_l_ancien_mot_de_passe()
        self.stdout.write(f"{len(concernes)} compte(s) portent encore l'ancien mot de passe commun.")
        if not appliquer or not concernes:
            return

        lignes = []
        with transaction.atomic():
            for user in concernes:
                provisoire = mot_de_passe_provisoire()
                user.password = hacher_provisoire(provisoire)
                user.must_change_password = True
                # Ferme les sessions déjà ouvertes : celles d'un intrus qui se
                # serait servi de l'ancien mot de passe partent avec.
                user.token_version += 1
                user.save(update_fields=['password', 'must_change_password', 'token_version'])
                lignes.append([
                    user.first_name, user.last_name, user.email or '', user.phone or '',
                    user.get_role_display(), provisoire,
                ])

            # Le fichier s'écrit DANS la transaction : s'il échoue, aucun mot
            # de passe n'a changé et personne n'est enfermé dehors.
            with open(sortie, 'x', newline='', encoding='utf-8-sig') as f:
                ecrivain = csv.writer(f, delimiter=';')
                ecrivain.writerow(['Prénom', 'Nom', 'Email', 'Téléphone', 'Rôle', 'Mot de passe provisoire'])
                ecrivain.writerows(lignes)

        self.stdout.write(self.style.SUCCESS(
            f"{len(lignes)} mot(s) de passe remplacé(s), sessions fermées. "
            f"Provisoires dans {sortie} : à remettre à l'administrateur, puis à effacer."
        ))

    @staticmethod
    def _comptes_a_l_ancien_mot_de_passe():
        """Les comptes dont le hachage correspond à l'ancien mot de passe.

        Un import hachait le mot de passe UNE fois pour tout le fichier : tous
        ses comptes partagent le même hachage. On vérifie donc chaque hachage
        distinct une seule fois (0,44 s), pas chaque compte.
        """
        candidats = list(User.objects.filter(must_change_password=True).exclude(password=''))
        verdicts = {}
        concernes = []
        for user in candidats:
            if user.password not in verdicts:
                verdicts[user.password] = check_password(ANCIEN_MOT_DE_PASSE_COMMUN, user.password)
            if verdicts[user.password]:
                concernes.append(user)
        return concernes
