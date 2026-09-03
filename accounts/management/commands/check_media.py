"""Recense les références de fichiers dont le fichier n'existe plus.

Un `FileField` ne garde qu'un CHEMIN. Rien ne garantit que le fichier au bout
du chemin existe encore, et Django ne s'en inquiète jamais : la base reste
parfaitement cohérente avec elle-même pendant que le disque s'est vidé.

Cela arrive plus souvent qu'on ne croit :

  · un conteneur recréé alors que `media/` n'est porté par aucun volume — le
    cas qui a motivé cette commande ;
  · un hébergeur au disque éphémère (Render, Fly, Heroku), où chaque
    déploiement repart d'un disque neuf ;
  · une bascule vers un stockage objet sans reprise des fichiers existants.

Le symptôme est toujours le même côté membre : une vignette cassée, sans
explication. Cette commande donne l'inventaire, et sait remettre les champs
concernés à vide pour que l'interface retombe sur ses états vides — qui, eux,
sont soignés.

    python manage.py check_media            # inventaire seul
    python manage.py check_media --clean    # vide les champs orphelins
"""

import os

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db.models import FileField, ImageField


class Command(BaseCommand):
    help = "Recense (et nettoie) les références vers des fichiers absents du disque."

    def add_arguments(self, parser):
        parser.add_argument(
            '--clean',
            action='store_true',
            help=(
                "Vide les champs dont le fichier est introuvable. "
                "Les enregistrements sont conservés : seule la référence part."
            ),
        )

    def handle(self, *args, **options):
        clean = options['clean']
        orphelins = []

        for model in apps.get_models():
            champs = [
                f.name
                for f in model._meta.get_fields()
                if isinstance(f, (FileField, ImageField))
            ]
            if not champs:
                continue

            # `.iterator()` : ces tables portent des images, pas des lignes de
            # log, mais rien n'oblige à charger tout le modèle en mémoire.
            for obj in model.objects.all().iterator():
                for champ in champs:
                    fichier = getattr(obj, champ, None)
                    if not fichier:
                        continue
                    try:
                        existe = fichier.storage.exists(fichier.name)
                    except (NotImplementedError, OSError):
                        # Certains backends de stockage ne savent pas répondre ;
                        # on ne déclare pas orphelin ce qu'on n'a pas pu vérifier.
                        continue
                    if not existe:
                        orphelins.append((model, obj.pk, champ, fichier.name))

        if not orphelins:
            self.stdout.write(self.style.SUCCESS(
                "Aucune référence orpheline : tous les fichiers sont présents."
            ))
            return

        self.stdout.write(self.style.WARNING(
            f"{len(orphelins)} référence(s) pointent vers un fichier absent :"
        ))
        for model, pk, champ, nom in orphelins:
            self.stdout.write(f"  {model._meta.label}.{champ} #{pk} -> {nom}")

        if not clean:
            self.stdout.write("")
            self.stdout.write(
                "Relancer avec --clean pour vider ces champs "
                "(l'interface retombera sur ses états vides)."
            )
            return

        for model, pk, champ, _nom in orphelins:
            model.objects.filter(pk=pk).update(**{champ: ''})

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"{len(orphelins)} référence(s) vidée(s). Les enregistrements sont intacts."
        ))
