# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════════════
L'import des zones territoriales et de leurs Daaras
═══════════════════════════════════════════════════════════════════════════════

Ce module remplace une boucle de vingt lignes qui vivait dans
`DaaraViewSet.import_excel`. Elle échouait de trois façons distinctes sur les
fichiers de référence de la direction, et chacune est traitée ici.


── 1. Elle mourait sur la ligne de totaux ────────────────────────────────────
`LDD DSMF SÉNÉGAL_CODE LDD.xlsx` se termine par une ligne fusionnée sur toute
la largeur (`A399:D399`) qui porte le décompte : « 397 … 31 ». Lue comme une
ligne de données, elle donne un nom de Daara de 177 caractères, que Postgres
refuse :

    DataError: value too long for type character varying(100)

pandas ne voit pas les fusions : il rend la valeur dans la première colonne et
`None` ailleurs. On lit donc avec openpyxl, qui expose `merged_cells`, et toute
ligne fusionnée sur trois colonnes ou plus est écartée comme un intertitre.


── 2. Elle n'avait AUCUNE transaction ────────────────────────────────────────
En autocommit, les 397 lignes déjà traitées étaient acquises quand la 398e
levait. L'utilisateur voyait « échec de l'import », rafraîchissait, et trouvait
ses données en place : le pire des deux mondes, car plus rien ne dit ce qui est
entré et ce qui manque. Tout se joue désormais dans un `atomic()`.


── 3. Elle résolvait la zone sur le SEUL code ────────────────────────────────
`get_or_create(code=...)` sur un code que le métier partage entre plusieurs
zones (voir le docstring du modèle `LDD`) : la deuxième zone d'un code était
rendue comme étant la première. Quatre zones ont ainsi disparu et quarante-six
Daaras ont changé de territoire en silence. La clé est le couple (code, nom).


── Les deux modes ────────────────────────────────────────────────────────────
`STRICT` n'ajoute que ce qui manque. Il ne déplace jamais rien : c'est le mode
d'un import de routine, où un rattachement inattendu doit être SIGNALÉ, pas
corrigé d'autorité.

`RECONCILIATION` répare en plus les dégâts de l'ancien import — mais seulement
là où la réponse est certaine : un Daara ne se déplace que s'il porte un nom
que le fichier n'attribue qu'à UNE zone, et qu'un seul Daara le porte en base.
Sinon on crée, et le doublon reste visible pour un arbitrage humain.

Cette prudence n'est pas théorique : les fichiers attribuent « DAWAMOU
CHOUKRY » à KAOLACK2 ET à MADINATOU SALAM, et « FAWZEYNI » à MEDINA/FASS ET à
SERIGNE BETHIO MOY DEUG. Déplacer sur la foi du nom seul choisirait au hasard.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

import openpyxl
from django.db import transaction

from accounts.models import LDD, Daara

#: Colonnes attendues, dans n'importe quel ordre.
COLONNES = ("DAARA", "LDD", "CODE LDD")

#: Gabarits des champs, repris du modèle. Une valeur plus longue est une erreur
#: de ligne — jamais un 500.
LIMITES = {"DAARA": 100, "LDD": 100, "CODE LDD": 10}

#: Au-delà de cette largeur, une cellule fusionnée n'est pas une donnée mais un
#: intertitre ou une ligne de totaux.
LARGEUR_FUSION_INTERTITRE = 3


class ModeImport:
    STRICT = "strict"
    RECONCILIATION = "reconciliation"


@dataclass
class Rapport:
    """Ce que l'import a fait, dit sans arrondi."""

    mode: str = ModeImport.STRICT
    zones_creees: list[str] = field(default_factory=list)
    zones_reconnues: int = 0
    daaras_crees: list[str] = field(default_factory=list)
    daaras_deplaces: list[str] = field(default_factory=list)
    orthographes_alignees: list[str] = field(default_factory=list)
    daaras_inchanges: int = 0
    lignes_ignorees: list[str] = field(default_factory=list)
    avertissements: list[str] = field(default_factory=list)
    erreurs: list[str] = field(default_factory=list)

    @property
    def en_echec(self) -> bool:
        return bool(self.erreurs)

    def resume(self) -> str:
        if self.en_echec:
            return f"Import refusé : {len(self.erreurs)} ligne(s) invalide(s). Rien n'a été enregistré."
        bouts = []
        if self.zones_creees:
            bouts.append(f"{len(self.zones_creees)} zone(s) créée(s)")
        if self.daaras_crees:
            bouts.append(f"{len(self.daaras_crees)} Daara(s) créé(s)")
        if self.daaras_deplaces:
            bouts.append(f"{len(self.daaras_deplaces)} Daara(s) réaffecté(s)")
        if self.orthographes_alignees:
            bouts.append(f"{len(self.orthographes_alignees)} orthographe(s) alignée(s)")
        if not bouts:
            return "Rien à modifier : le fichier correspond déjà à la base."
        return "Import réussi — " + ", ".join(bouts) + "."

    def en_dict(self) -> dict:
        return {
            "success": not self.en_echec,
            "mode": self.mode,
            "message": self.resume(),
            "zones_creees": self.zones_creees,
            "zones_reconnues": self.zones_reconnues,
            "daaras_crees": self.daaras_crees,
            "daaras_deplaces": self.daaras_deplaces,
            "orthographes_alignees": self.orthographes_alignees,
            "daaras_inchanges": self.daaras_inchanges,
            "lignes_ignorees": self.lignes_ignorees,
            "avertissements": self.avertissements,
            "erreurs": self.erreurs,
        }


def _normaliser(valeur) -> str:
    """Une cellule ramenée à son texte : espaces internes réduits, bords nus."""
    if valeur is None:
        return ""
    return " ".join(str(valeur).split())


def _cle(nom: str) -> str:
    """La forme sur laquelle DEUX NOMS SE COMPARENT — jamais celle qu'on affiche.

    🔴 Comparer les chaînes brutes fabrique des jumeaux. Le fichier du Sénégal
    contient « NGABOU PEKK BI  MEDINA » avec DEUX espaces ; la lecture les
    réduit à un, et un appariement littéral ne reconnaissait plus le Daara déjà
    en base. L'import en créait un second, dans la même zone, au nom
    typographiquement différent et humainement identique. Constaté le
    2026-09-18 — le doublon a été créé puis résorbé.

    On ignore donc ce qui ne distingue pas deux Daaras aux yeux d'un lecteur :
    la casse, les espaces, la ponctuation et les accents. « INDIVIDUEL » et
    « individuel » sont le même Daara ; c'est aussi ce que fait la collation de
    Postgres quand il trie cette liste.
    """
    sans_accent = "".join(
        c for c in unicodedata.normalize("NFD", nom) if unicodedata.category(c) != "Mn"
    )
    return "".join(c for c in sans_accent.upper() if c.isalnum())


def lire_feuille(fichier) -> tuple[list[dict], list[str], list[str]]:
    """Rend (lignes, ignorées, erreurs) à partir du classeur.

    Une « ligne » est un dict {ligne, daara, ldd, code}, le report vers le bas
    déjà appliqué : dans ces fichiers, la zone n'est portée que par la première
    ligne de son groupe, les suivantes héritant par cellule fusionnée.
    """
    ignorees: list[str] = []
    erreurs: list[str] = []

    try:
        classeur = openpyxl.load_workbook(fichier, data_only=True, read_only=False)
    except Exception as e:  # fichier corrompu, format inattendu…
        return [], [], [f"Fichier illisible : {e}"]

    feuille = classeur.worksheets[0]

    # Les lignes fusionnées sur toute la largeur ne portent pas de données.
    intertitres = {
        plage.min_row
        for plage in feuille.merged_cells.ranges
        if plage.max_col - plage.min_col + 1 >= LARGEUR_FUSION_INTERTITRE
    }

    toutes = list(feuille.iter_rows(values_only=True))
    if not toutes:
        return [], [], ["Le fichier ne contient aucune ligne."]

    # L'en-tête n'est pas forcément en première ligne : on la cherche.
    index_entete, colonnes = None, {}
    for i, ligne in enumerate(toutes[:10]):
        trouvees = {}
        for j, cellule in enumerate(ligne):
            titre = _normaliser(cellule).upper()
            if titre in COLONNES:
                trouvees[titre] = j
        if set(COLONNES).issubset(trouvees):
            index_entete, colonnes = i, trouvees
            break

    if index_entete is None:
        return [], [], [
            "Format invalide : les colonnes « DAARA », « LDD » et « CODE LDD » "
            "sont introuvables dans les dix premières lignes."
        ]

    lignes: list[dict] = []
    ldd_courant, code_courant = "", ""

    for decalage, brute in enumerate(toutes[index_entete + 1 :], start=index_entete + 2):
        if decalage in intertitres:
            apercu = _normaliser(brute[colonnes["DAARA"]] if colonnes["DAARA"] < len(brute) else "")
            ignorees.append(f"ligne {decalage} — ligne de totaux ou intertitre : « {apercu[:60]} »")
            continue

        def cellule(nom):
            j = colonnes[nom]
            return _normaliser(brute[j]) if j < len(brute) else ""

        daara, ldd, code = cellule("DAARA"), cellule("LDD"), cellule("CODE LDD")

        # Report vers le bas : une cellule vide hérite du groupe au-dessus.
        if ldd:
            ldd_courant = ldd
        if code:
            code_courant = code

        if not daara:
            if ldd or code:
                ignorees.append(f"ligne {decalage} — zone « {ldd or code} » sans Daara")
            continue

        if not ldd_courant or not code_courant:
            erreurs.append(
                f"ligne {decalage} — « {daara} » n'est rattaché à aucune zone : "
                "aucune ligne au-dessus ne porte de LDD ni de CODE LDD."
            )
            continue

        for nom, valeur in (("DAARA", daara), ("LDD", ldd_courant), ("CODE LDD", code_courant)):
            if len(valeur) > LIMITES[nom]:
                erreurs.append(
                    f"ligne {decalage} — {nom} fait {len(valeur)} caractères "
                    f"(maximum {LIMITES[nom]}) : « {valeur[:60]}… »"
                )
                break
        else:
            lignes.append(
                {"ligne": decalage, "daara": daara, "ldd": ldd_courant, "code": code_courant}
            )

    return lignes, ignorees, erreurs


@transaction.atomic
def importer(fichier, mode: str = ModeImport.STRICT) -> Rapport:
    """Applique le fichier à la base. Tout ou rien.

    En cas d'erreur de lecture, la transaction est annulée par l'appelant via
    `rapport.en_echec` — on ne lève pas : un fichier mal formé est une réponse
    à rendre à l'utilisateur, pas un incident serveur.
    """
    rapport = Rapport(mode=mode)
    lignes, ignorees, erreurs = lire_feuille(fichier)
    rapport.lignes_ignorees = ignorees
    rapport.erreurs = erreurs

    if erreurs:
        transaction.set_rollback(True)
        return rapport

    # Passe 1 — ce que le fichier affirme, avant d'écrire quoi que ce soit.
    zones_par_daara: dict[str, set[tuple[str, str]]] = {}
    for l in lignes:
        zones_par_daara.setdefault(l["daara"], set()).add((l["code"], l["ldd"]))

    #: Le PÉRIMÈTRE du fichier : les zones dont il parle.
    #:
    #: 🔴 Sans cette borne, la réconciliation déraille dès qu'on importe UN
    #: fichier d'un jeu qui en compte plusieurs. L'essai à blanc l'a montré :
    #: « DSMF THIOFEL » figure au Sénégal sous MADINATOU SALAM et, sous le même
    #: nom, à l'étranger sous KARAMNA PARIS. En important le seul fichier des
    #: étrangers, le nom n'y a qu'une zone et un seul Daara le porte en base :
    #: la règle concluait au rattachement fautif et expédiait un Daara
    #: sénégalais à Paris.
    #:
    #: Un déplacement ne se justifie que DANS le périmètre décrit : si la zone
    #: actuelle d'un Daara ne figure pas dans ce fichier, ce fichier n'a rien à
    #: dire sur elle.
    perimetre = {(l["code"], l["ldd"]) for l in lignes}

    # Passe 2 — application.
    #
    # Tous les Daaras sont chargés UNE fois et indexés sur leur clé de
    # comparaison. Interroger la base à chaque ligne coûtait deux requêtes par
    # ligne — huit cents pour le fichier du Sénégal — et, surtout, ne voyait
    # pas les écarts de graphie.
    par_cle: dict[str, list[Daara]] = {}
    for d in Daara.objects.select_related("ldd"):
        par_cle.setdefault(_cle(d.name), []).append(d)

    cache_zones: dict[tuple[str, str], LDD] = {}

    for l in lignes:
        cle_zone = (l["code"], l["ldd"])
        zone = cache_zones.get(cle_zone)
        if zone is None:
            zone, creee = LDD.objects.get_or_create(
                code=l["code"], name=l["ldd"], defaults={"is_active": True}
            )
            cache_zones[cle_zone] = zone
            if creee:
                rapport.zones_creees.append(f"{zone.code} — {zone.name}")
            else:
                rapport.zones_reconnues += 1

        cle_daara = _cle(l["daara"])
        homonymes = par_cle.setdefault(cle_daara, [])
        dans_la_zone = [d for d in homonymes if d.ldd_id == zone.pk]

        if dans_la_zone:
            # Plusieurs graphies du même nom cohabitent dans la zone : c'est un
            # doublon, et il se signale MÊME si l'une d'elles correspond
            # exactement au fichier — sinon l'anomalie reste invisible à chaque
            # import suivant. Fusionner relève du métier : deux Daaras peuvent
            # porter des membres, et les réunir n'est pas à l'import d'en
            # décider.
            if len(dans_la_zone) > 1:
                rapport.avertissements.append(
                    f"ligne {l['ligne']} — « {l['daara']} » a plusieurs graphies dans "
                    f"{zone.code} {zone.name} : "
                    + ", ".join(f"« {d.name} » (#{d.pk})" for d in dans_la_zone)
                    + " — doublon à arbitrer"
                )

            exact = next((d for d in dans_la_zone if d.name == l["daara"]), None)
            if exact is not None:
                rapport.daaras_inchanges += 1
            elif len(dans_la_zone) == 1 and mode == ModeImport.RECONCILIATION:
                # Même Daara, orthographe différente. Le fichier fait foi.
                jumeau = dans_la_zone[0]
                ancien = jumeau.name
                jumeau.name = l["daara"]
                jumeau.save(update_fields=["name", "updated_at"])
                rapport.orthographes_alignees.append(
                    f"« {ancien} » → « {l['daara']} » ({zone.code} {zone.name})"
                )
            else:
                rapport.daaras_inchanges += 1
            continue

        ailleurs = [d for d in homonymes if d.ldd_id != zone.pk]

        # Le fichier n'attribue ce nom qu'à une zone, et un seul Daara le porte
        # en base, sous une autre : c'est un rattachement à corriger, pas un
        # homonyme. Le déplacer préserve son identifiant, ses membres et ses
        # dons — là où supprimer puis recréer les perdrait.
        reparable = (
            len(zones_par_daara[l["daara"]]) == 1
            and len(ailleurs) == 1
            and (ailleurs[0].ldd.code, ailleurs[0].ldd.name) in perimetre
        )

        if ailleurs and reparable and mode == ModeImport.RECONCILIATION:
            orphelin = ailleurs[0]
            ancienne = orphelin.ldd
            orphelin.ldd = zone
            orphelin.save(update_fields=["ldd", "updated_at"])
            rapport.daaras_deplaces.append(
                f"{orphelin.name} : {ancienne.code} {ancienne.name} → {zone.code} {zone.name}"
            )
            continue

        if ailleurs:
            rapport.avertissements.append(
                f"ligne {l['ligne']} — « {l['daara']} » existe déjà sous "
                + ", ".join(f"{d.ldd.code} {d.ldd.name}" for d in ailleurs)
                + f" ; créé en plus sous {zone.code} {zone.name}"
                + ("" if reparable else " (nom porté par plusieurs zones : arbitrage humain requis)")
            )

        nouveau = Daara.objects.create(name=l["daara"], ldd=zone, is_active=True)
        homonymes.append(nouveau)
        rapport.daaras_crees.append(f"{l['daara']} ({zone.code} {zone.name})")

    return rapport
