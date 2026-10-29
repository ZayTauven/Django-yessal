# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════════════
L'import des membres depuis un classeur Excel
═══════════════════════════════════════════════════════════════════════════════

Ce module remplace une boucle qui vivait DANS LE NAVIGATEUR
(`UserManagementClient.tsx`, `handleImportExcel`). Éprouvée le 2026-10-29, elle
échouait de trois façons :

── 1. La colonne Daara était lue, puis jetée ─────────────────────────────────
Le navigateur n'envoyait que prénom, nom, courriel, téléphone et rôle. Tous les
comptes importés arrivaient sans Daara, quel que soit le fichier.

── 2. Les refus étaient muets ────────────────────────────────────────────────
Un rôle saisi « Membre » au lieu de `member` était refusé par Django, et
l'écran disait seulement « 3 comptes importés sur 4 ». Ni quelle ligne, ni
pourquoi. Chaque ligne a désormais un verdict et une raison.

── 3. Un nom de Daara ne désigne pas un Daara ────────────────────────────────
Huit noms sont portés par plusieurs Daaras : « INDIVIDUEL » onze fois,
« DAWAMOU CHOUKRY » et « FAWZEYNI » trois fois. Seul le couple (Daara, LDD)
identifie un Daara — c'est la contrainte `unique_together` du modèle.


── La règle : ne jamais deviner ──────────────────────────────────────────────
Un Daara n'est affecté que si le fichier le désigne SANS AMBIGUÏTÉ. Faute de
frappe, homonyme, LDD absente ou inconnue : le compte est créé SANS Daara, et
le membre le choisit lui-même à sa première connexion (voir
`accounts.services.accueil`). Un compte sans Daara se corrige en un geste ; un
compte versé dans le mauvais Daara fausse en silence les totaux, les annuaires
et la collecte. Décision de l'utilisateur, 2026-10-29.

Les noms se comparent comme dans l'import des LDD (`ldd_import._cle`) : sans
casse, sans accents, sans espaces ni ponctuation. Une proposition « vouliez-
vous dire » accompagne un nom introuvable — pour l'administrateur qui relit le
rapport, jamais pour une affectation automatique.


── L'aperçu ──────────────────────────────────────────────────────────────────
`apercu=True` déroule TOUT l'import — validations, contrôles d'unicité, y
compris entre lignes du même fichier — puis annule la transaction. Aucun
compte, aucun courriel. C'est ce qui permet de montrer le rapport avant de
s'engager.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

import openpyxl
from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, transaction

from accounts.models import Daara, LDD, User
from accounts.serializers import UserSerializer, _auteur_de
from accounts.services.accueil import ROLES_COMMUNAUTAIRES
from accounts.services.ldd_import import _cle, _normaliser
from core.mail import send_to_users

#: Le mot de passe que reçoivent tous les comptes importés. Le membre est tenu
#: de le remplacer (`must_change_password`), dernière étape de son accueil.
#: ⚠ Le front l'ANNONCE à l'administrateur avant l'import : toute modification
#: se reporte dans `UserManagementClient.tsx` (`IMPORT_DEFAULT_PASSWORD`).
MOT_DE_PASSE_PAR_DEFAUT = "YessalPassword2024!"

#: Au-delà, le fichier n'est pas une liste de membres mais une erreur de
#: manipulation — et la requête dépasserait le délai du serveur.
LIGNES_MAX = 3000

#: Les en-têtes reconnus, ramenés à leur clé de comparaison. Un fichier saisi à
#: la main dit « Prénom », « PRENOM », « Téléphone » ou « Tel » : tous valent.
ENTETES = {
    "first_name": {"PRENOM", "PRENOMS", "FIRSTNAME"},
    "last_name": {"NOM", "NOMDEFAMILLE", "LASTNAME"},
    "email": {"EMAIL", "COURRIEL", "MAIL", "ADRESSEEMAIL", "ADRESSEMAIL"},
    "phone": {"TELEPHONE", "TEL", "PHONE", "NUMERO", "PORTABLE", "MOBILE"},
    "role": {"ROLE"},
    "daara": {"DAARA"},
    "ldd": {"LDD", "ZONE"},
    "code_ldd": {"CODELDD", "CODE"},
}

#: Les rôles qu'un fichier peut attribuer, sous les noms qu'on y écrit.
#: `admin` n'y est pas : un classeur ne crée pas d'administrateur.
ROLES = {
    User.Role.MEMBER: {"MEMBER", "MEMBRE", "TALUBE", "TALIBE"},
    User.Role.CHEF_DAARA: {
        "CHEFDAARA", "CHEF", "CHEFDUDAARA", "COORDINATEUR", "COORDINATEURDUDAARA",
    },
    User.Role.COLLECTOR: {"COLLECTOR", "COLLECTEUR"},
    User.Role.TUTELLE: {"TUTELLE"},
}


class Verdict:
    CREE = "cree"
    CREE_SANS_DAARA = "cree_sans_daara"
    REJETE = "rejete"


@dataclass
class Ligne:
    """Le sort d'une ligne du fichier, dit à l'administrateur."""

    ligne: int
    nom: str
    identifiant: str
    verdict: str
    role: str = User.Role.MEMBER
    daara: str | None = None
    #: Pourquoi le Daara n'a pas été affecté, ou pourquoi la ligne est refusée.
    motif: str | None = None
    remarques: list[str] = field(default_factory=list)

    def en_dict(self) -> dict:
        return {
            "ligne": self.ligne,
            "nom": self.nom,
            "identifiant": self.identifiant,
            "verdict": self.verdict,
            "role": self.role,
            "daara": self.daara,
            "motif": self.motif,
            "remarques": self.remarques,
        }


@dataclass
class Rapport:
    apercu: bool = False
    lignes: list[Ligne] = field(default_factory=list)
    lignes_vides: int = 0
    #: Un défaut qui empêche de lire le fichier : rien n'est tenté.
    erreur: str | None = None

    def _compte(self, verdict: str) -> int:
        return sum(1 for l in self.lignes if l.verdict == verdict)

    def resume(self) -> str:
        if self.erreur:
            return self.erreur
        crees = self._compte(Verdict.CREE) + self._compte(Verdict.CREE_SANS_DAARA)
        sans = self._compte(Verdict.CREE_SANS_DAARA)
        rejetes = self._compte(Verdict.REJETE)
        verbe = "seront créés" if self.apercu else "créés"
        bouts = [f"{crees} compte(s) {verbe}"]
        if sans:
            bouts.append(f"dont {sans} sans Daara")
        if rejetes:
            bouts.append(f"{rejetes} ligne(s) refusée(s)")
        return ", ".join(bouts) + "."

    def en_dict(self) -> dict:
        return {
            "success": self.erreur is None,
            "apercu": self.apercu,
            "message": self.resume(),
            "mot_de_passe": MOT_DE_PASSE_PAR_DEFAUT,
            "totaux": {
                "lignes": len(self.lignes),
                "crees": self._compte(Verdict.CREE),
                "crees_sans_daara": self._compte(Verdict.CREE_SANS_DAARA),
                "rejetes": self._compte(Verdict.REJETE),
                "lignes_vides": self.lignes_vides,
            },
            "lignes": [l.en_dict() for l in self.lignes],
        }


# ─── Lecture ──────────────────────────────────────────────────────────────────


def _texte(valeur) -> str:
    """Une cellule en texte. Un nombre entier saisi dans Excel sort en `771230001.0`."""
    if isinstance(valeur, float) and valeur.is_integer():
        valeur = int(valeur)
    return _normaliser(valeur)


def lire_feuille(fichier) -> tuple[list[dict], str | None]:
    """Rend (lignes, erreur). Chaque ligne est un dict des champs reconnus, plus `ligne`."""
    try:
        classeur = openpyxl.load_workbook(fichier, data_only=True, read_only=True)
    except Exception:
        return [], "Fichier illisible : seuls les classeurs Excel (.xlsx) sont acceptés."

    toutes = list(classeur.worksheets[0].iter_rows(values_only=True))
    if not toutes:
        return [], "Le fichier ne contient aucune ligne."

    # L'en-tête n'est pas forcément en première ligne : un titre la précède souvent.
    index_entete, colonnes = None, {}
    for i, ligne in enumerate(toutes[:10]):
        trouvees = {}
        for j, cellule in enumerate(ligne):
            cle = _cle(_texte(cellule))
            for champ, alias in ENTETES.items():
                if cle in alias and champ not in trouvees:
                    trouvees[champ] = j
        identite = {"first_name", "last_name"} & trouvees.keys()
        contact = {"email", "phone"} & trouvees.keys()
        if identite and contact:
            index_entete, colonnes = i, trouvees
            break

    if index_entete is None:
        return [], (
            "Format invalide : il faut au moins une colonne « Prénom » ou « Nom », et une "
            "colonne « Email » ou « Téléphone », dans les dix premières lignes."
        )

    lignes = []
    for numero, brute in enumerate(toutes[index_entete + 1:], start=index_entete + 2):
        valeurs = {
            champ: _texte(brute[j]) if j < len(brute) else ""
            for champ, j in colonnes.items()
        }
        valeurs["ligne"] = numero
        lignes.append(valeurs)

    if len(lignes) > LIGNES_MAX:
        return [], f"Le fichier compte {len(lignes)} lignes : {LIGNES_MAX} au plus par import."
    return lignes, None


# ─── Résolution du Daara ──────────────────────────────────────────────────────


class IndexDaaras:
    """Tous les Daaras et toutes les LDD, chargés une fois et indexés sur leur clé."""

    def __init__(self):
        self.par_nom: dict[str, list[Daara]] = {}
        for d in Daara.objects.select_related("ldd"):
            self.par_nom.setdefault(_cle(d.name), []).append(d)
        self.ldds = list(LDD.objects.all())

    @staticmethod
    def libelle(d: Daara) -> str:
        return f"{d.name} ({d.ldd.name})"

    def suggestions(self, nom: str) -> list[str]:
        proches = difflib.get_close_matches(_cle(nom), self.par_nom.keys(), n=3, cutoff=0.75)
        return [self.libelle(d) for cle in proches for d in self.par_nom[cle]][:3]

    def resoudre(self, nom: str, ldd: str, code: str) -> tuple[Daara | None, str | None]:
        """Rend (Daara, None) si le fichier n'en désigne qu'un ; sinon (None, motif)."""
        if not nom:
            return None, "aucun Daara indiqué"

        candidats = self.par_nom.get(_cle(nom), [])
        if not candidats:
            motif = f"Daara « {nom} » introuvable"
            proches = self.suggestions(nom)
            if proches:
                motif += " — vouliez-vous dire " + ", ".join(f"« {p} »" for p in proches) + " ?"
            return None, motif

        if ldd or code:
            zones = [
                z for z in self.ldds
                if (not ldd or _cle(ldd) in (_cle(z.name), _cle(z.code)))
                and (not code or _cle(code) == _cle(z.code))
            ]
            if not zones:
                return None, f"LDD « {ldd or code} » introuvable"
            ids = {z.pk for z in zones}
            dans_la_zone = [d for d in candidats if d.ldd_id in ids]
            if not dans_la_zone:
                return None, (
                    f"« {nom} » n'existe pas dans la LDD « {ldd or code} » — on le trouve sous "
                    + ", ".join(f"« {d.ldd.name} »" for d in candidats)
                )
            candidats = dans_la_zone

        if len(candidats) > 1:
            if ldd or code:
                return None, (
                    f"« {nom} » existe en double dans cette LDD ("
                    + ", ".join(f"#{d.pk}" for d in candidats) + ") — doublon à arbitrer"
                )
            return None, (
                f"« {nom} » existe dans {len(candidats)} LDD ("
                + ", ".join(d.ldd.name for d in candidats[:4])
                + ("…" if len(candidats) > 4 else "")
                + ") — précisez la colonne LDD"
            )

        daara = candidats[0]
        if not daara.is_active:
            return None, f"le Daara « {self.libelle(daara)} » est désactivé"
        return daara, None


# ─── Application ──────────────────────────────────────────────────────────────


def _role(saisi: str) -> tuple[str, str | None]:
    """Le rôle à créer, et une remarque si la saisie n'a pas été suivie."""
    if not saisi:
        return User.Role.MEMBER, None
    cle = _cle(saisi)
    for role, alias in ROLES.items():
        if cle in alias:
            return role, None
    if cle in {"ADMIN", "ADMINISTRATEUR"}:
        return User.Role.MEMBER, "un import ne crée pas d'administrateur : compte créé en membre"
    return User.Role.MEMBER, f"rôle « {saisi} » inconnu : compte créé en membre"


def _erreurs_en_texte(erreurs: dict) -> str:
    """Les erreurs de DRF ramenées à une phrase : « email : Un compte… »."""
    libelles = {"email": "Email", "phone": "Téléphone", "first_name": "Prénom", "last_name": "Nom"}
    morceaux = []
    for champ, messages in erreurs.items():
        texte = " ".join(str(m) for m in (messages if isinstance(messages, list) else [messages]))
        morceaux.append(texte if champ == "non_field_errors" else f"{libelles.get(champ, champ)} : {texte}")
    return " ; ".join(morceaux)


def importer(fichier, *, request, apercu: bool = False) -> Rapport:
    """Crée les comptes du fichier. Chaque ligne réussit ou échoue seule.

    Toute la passe tient dans UNE transaction : un imprévu (base injoignable)
    annule tout, et l'aperçu l'annule exprès. Chaque ligne a en outre son point
    de sauvegarde, pour qu'un refus n'emporte pas les lignes voisines.
    """
    rapport = Rapport(apercu=apercu)
    lignes, erreur = lire_feuille(fichier)
    if erreur:
        rapport.erreur = erreur
        return rapport

    index = IndexDaaras()
    crees: list[User] = []
    # Haché UNE fois pour tout le fichier : tous les comptes importés reçoivent
    # ce même mot de passe, connu de l'administrateur et à changer au premier
    # accès. Un hachage par ligne coûtait 0,44 s ; 260 lignes dépassaient le
    # délai de gunicorn (30 s), aperçu compris, et la requête mourait. Le
    # même sel pour tous ne révèle rien de plus : le mot de passe est commun.
    hache = make_password(MOT_DE_PASSE_PAR_DEFAUT)

    with transaction.atomic():
        for brute in lignes:
            prenom, nom = brute.get("first_name", ""), brute.get("last_name", "")
            email, telephone = brute.get("email", ""), brute.get("phone", "")

            if not any(v for k, v in brute.items() if k != "ligne"):
                rapport.lignes_vides += 1
                continue

            resultat = Ligne(
                ligne=brute["ligne"],
                nom=f"{prenom} {nom}".strip(),
                identifiant=email or telephone,
                verdict=Verdict.REJETE,
            )
            rapport.lignes.append(resultat)

            if not prenom and not nom:
                resultat.motif = "ni prénom ni nom"
                continue
            if not email and not telephone:
                resultat.motif = "ni email ni téléphone : le membre ne pourrait pas se connecter"
                continue

            role, remarque = _role(brute.get("role", ""))
            resultat.role = role
            if remarque:
                resultat.remarques.append(remarque)

            daara, motif = None, None
            if role in ROLES_COMMUNAUTAIRES:
                daara, motif = index.resoudre(
                    brute.get("daara", ""), brute.get("ldd", ""), brute.get("code_ldd", ""),
                )

            serializer = UserSerializer(
                data={
                    "first_name": prenom,
                    "last_name": nom,
                    "email": email or None,
                    "phone": telephone or None,
                    "role": role,
                    "password": MOT_DE_PASSE_PAR_DEFAUT,
                },
                # Les courriels partent en un seul envoi groupé, après la
                # transaction : un par compte, en série, ferait dépasser le
                # délai de la requête dès quelques centaines de lignes.
                context={"request": request, "courriel_differe": True, "mot_de_passe_hache": hache},
            )
            if not serializer.is_valid():
                resultat.motif = _erreurs_en_texte(serializer.errors)
                continue

            try:
                with transaction.atomic():
                    user = serializer.save(status=User.Status.ACTIVE, daara=daara)
            except IntegrityError:
                resultat.motif = "un compte existe déjà avec cet email ou ce téléphone"
                continue

            crees.append(user)
            if daara is not None:
                resultat.verdict = Verdict.CREE
                resultat.daara = index.libelle(daara)
            elif role not in ROLES_COMMUNAUTAIRES:
                resultat.verdict = Verdict.CREE
            else:
                resultat.verdict = Verdict.CREE_SANS_DAARA
                resultat.motif = motif

        if apercu:
            transaction.set_rollback(True)
        elif crees:
            auteur = _auteur_de(UserSerializer(context={"request": request}))
            transaction.on_commit(
                lambda: send_to_users(crees, "mot_de_passe_provisoire", contexte={"auteur": auteur})
            )

    return rapport
