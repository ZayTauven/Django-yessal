"""
Normalisation des numéros de téléphone, en E.164.

Le numéro est un IDENTIFIANT de connexion au même titre que l'adresse e-mail,
et il porte une contrainte d'unicité. Deux écritures du même numéro donnaient
donc deux comptes possibles et une connexion impossible : le formulaire
d'inscription proposait « +221 77 000 00 00 », espaces compris, quand l'écran
de connexion émettait « +221770000000 ». Le compte existait ; la recherche,
exacte, ne le retrouvait jamais.

Une seule forme doit atteindre la base, quelle que soit la porte d'entrée.
C'est pourquoi ce module est partagé plutôt que recopié dans chaque
sérialiseur : une deuxième implémentation, c'est une deuxième forme.

═══════════════════════════════════════════════════════════════════════════
E.164, et non « un numéro sénégalais »
═══════════════════════════════════════════════════════════════════════════
Une grande partie des membres vit à l'étranger — c'est pour cela que le
formulaire web porte un sélecteur international. Une normalisation qui
imposerait +221 leur fermerait la porte. `+221` n'est ici que la RÉGION PAR
DÉFAUT : l'indicatif prêté à un numéro saisi en national. Tout indicatif écrit
explicitement est conservé tel quel, et le défaut se règle par
`settings.DEFAULT_PHONE_REGION`.

═══════════════════════════════════════════════════════════════════════════
Une expression régulière, et non `phonenumbers`
═══════════════════════════════════════════════════════════════════════════
La bibliothèque n'est pas dans `requirements.txt`, et le projet est en phase
de déploiement : y ajouter une dépendance n'est pas une décision qui se prend
en passant. Ce qui suit MET EN FORME un numéro ; ça ne certifie pas qu'il
existe. `phonenumbers` saurait dire en plus qu'un « +221 12 34 56 78 » n'est
pas un mobile sénégalais plausible, et connaît les préfixes d'acheminement
propres à chaque pays — un gain réel, à reconsidérer une fois la production
stable.
"""

import re

from django.core.exceptions import ImproperlyConfigured, ValidationError

# Tout ce qu'une main humaine glisse entre les chiffres : espaces, points,
# parenthèses, barres obliques, tirets de toutes largeurs.
#
# `\s` couvre les espaces INSÉCABLES (U+00A0, U+202F) autant que l'espace
# ordinaire : un copier-coller depuis Word ou depuis un iPhone en laisse
# derrière lui, et un numéro qui en contient une n'est plus le même numéro.
#
# Les tirets sont donnés en points de code (U+2010 à U+2015) plutôt que collés
# tels quels : un demi-cadratin et un trait d'union se ressemblent trop pour
# qu'on les distingue dans un éditeur.
_SEPARATEURS = re.compile(r"[\s.()/\\\u2010-\u2015\-]+")

_CHIFFRES = re.compile(r"[0-9]+")

# Un groupe entre parenthèses EN TÊTE de saisie : « (221) 77 000 00 00 ». La
# notation internationale de l'UIT met l'indicatif du pays entre parenthèses
# quand elle ne met pas un « + » ; sans cette lecture, les parenthèses tombent
# avec les autres séparateurs et le numéro reçoit son indicatif une seconde
# fois — « (221) 77… » devenait « +221221 77… ».
#
# La limite connue : en Amérique du Nord, les mêmes parenthèses entourent
# l'indicatif RÉGIONAL, pas celui du pays — « (415) 555-1234 » serait lu comme
# un numéro suisse. Le formulaire web porte un sélecteur international et émet
# du E.164 : cette saisie n'arrive pas par là. Le jour où elle arriverait,
# c'est `phonenumbers` qu'il faudrait, pas une règle de plus.
_INDICATIF_PARENTHESE = re.compile(r"^\s*\(\s*([0-9]{1,4})\s*\)")

# La forme d'arrivée : un « + », puis 6 à 15 chiffres dont le premier n'est
# jamais un zéro — aucun indicatif de pays ne commence par là.
#
# 15 est la borne de la recommandation E.164, indicatif COMPRIS. 6 écarte les
# numéros courts (services, USSD) : ils ne désignent pas un abonné et ne
# peuvent donc pas identifier un compte.
#
# `re.ASCII` est délibéré : sans lui, `\d` accepterait les chiffres arabes ou
# devanagari, qu'aucun opérateur ne saurait composer.
_E164 = re.compile(r"\+[1-9][0-9]{5,14}", re.ASCII)

# Seize caractères au plus, « + » compris : `User.phone` en accepte vingt.
# Aucune migration de schéma n'est nécessaire — vérifié, pas supposé.
LONGUEUR_MAX = 16

# Le repli si le réglage est absent. Le Sénégal est le cas courant, pas une
# règle : voir l'en-tête.
INDICATIF_PAR_DEFAUT = "+221"


def _indicatif_par_defaut(region=None) -> str:
    """L'indicatif prêté à un numéro saisi sans le sien.

    On tolère « +221 », « 221 » et « 00221 » dans le réglage : une valeur
    d'environnement écrite de bonne foi ne doit pas faire échouer chaque
    inscription du pays. En revanche un réglage qui n'est pas un indicatif du
    tout est une erreur de configuration, pas une saisie utilisateur — elle se
    signale bruyamment plutôt que de fabriquer des numéros faux en silence.
    """
    from django.conf import settings

    brut = region if region is not None else getattr(
        settings, "DEFAULT_PHONE_REGION", INDICATIF_PAR_DEFAUT
    )
    compact = _SEPARATEURS.sub("", str(brut or "")).lstrip("+")
    if compact.startswith("00"):
        compact = compact[2:]

    if not compact or not _CHIFFRES.fullmatch(compact) or compact.startswith("0"):
        raise ImproperlyConfigured(
            "DEFAULT_PHONE_REGION doit être un indicatif de pays, par exemple "
            f"« +221 ». Valeur reçue : {brut!r}."
        )
    return "+" + compact


def normalize_phone(value, *, region=None):
    """Ramène une saisie humaine à sa forme E.164 canonique.

    « +221 77 000 00 00 », « 77 000 00 00 », « 00221770000000 » et
    « (221) 77-000-00-00 » donnent tous « +221770000000 » ;
    « +33 6 12 34 56 78 » donne « +33612345678 ».

    Renvoie None pour une saisie vide. Le numéro est facultatif tant qu'une
    adresse e-mail est présente, et « pas de numéro » doit rester NULL en
    base : la chaîne vide entrerait en collision avec elle-même sur la
    contrainte d'unicité dès le deuxième compte sans numéro.

    Lève `ValidationError` — celle de Django, que DRF sait traduire en 400 sur
    le bon champ — quand la saisie ne peut pas devenir un numéro.
    """
    if value is None:
        return None

    # Les parenthèses de tête se lisent AVANT d'être effacées : elles portent
    # un sens que la suite du traitement ne saurait plus retrouver.
    saisie = _INDICATIF_PARENTHESE.sub(r"+\1", str(value), count=1)

    compact = _SEPARATEURS.sub("", saisie).strip()
    if not compact:
        return None

    if compact.startswith("+"):
        chiffres = compact[1:]
    elif compact.startswith("00"):
        # Le préfixe international composé « à la main » : 00 vaut +. Traité
        # avant le cas national, sinon « 00221770000000 » recevrait un
        # indicatif de plus par-dessus celui qu'il porte déjà.
        chiffres = compact[2:]
    else:
        # Numéro national : on lui prête l'indicatif par défaut.
        #
        # Le zéro de tête éventuel — celui de « 06 12 34 56 78 » — est un
        # préfixe d'acheminement INTERNE au pays. Aucun numéro E.164 ne
        # commence par zéro après son indicatif : le garder fabriquerait un
        # numéro qui n'existe pas. On n'en retire qu'un seul, pour ne pas
        # amputer une saisie qui en aligne plusieurs.
        national = compact[1:] if compact.startswith("0") else compact
        chiffres = _indicatif_par_defaut(region)[1:] + national

    if not _CHIFFRES.fullmatch(chiffres):
        raise ValidationError(
            "Le numéro de téléphone ne doit contenir que des chiffres, "
            "précédés de l'indicatif du pays.",
            code="invalid_phone",
        )

    normalise = "+" + chiffres
    if not _E164.fullmatch(normalise):
        raise ValidationError(
            "Le numéro de téléphone doit comporter entre 6 et 15 chiffres, "
            "indicatif du pays compris.",
            code="invalid_phone",
        )
    return normalise


def normalize_phone_quietly(value, *, region=None):
    """Comme `normalize_phone`, mais renvoie None au lieu de lever.

    Pour les chemins où l'échec n'a pas à être expliqué :

      · la connexion, qui ne doit jamais dire à un inconnu POURQUOI sa saisie
        ne correspond à rien — ce serait lui apprendre ce qui existe ;
      · la migration de données, qui doit signaler une ligne rétive sans
        interrompre un déploiement.
    """
    try:
        return normalize_phone(value, region=region)
    except ValidationError:
        return None


def looks_like_phone(value) -> bool:
    """Vrai si la saisie a la forme d'un numéro, pas d'une adresse.

    L'écran de connexion accepte les deux dans le même champ. Une adresse
    passée au normalisateur en ressortirait défigurée, ou refusée : il faut
    savoir à quoi on a affaire AVANT de normaliser.

    Le test est volontairement grossier — des chiffres, des séparateurs, un
    « + » éventuel — parce qu'il ne décide de rien : il choisit seulement s'il
    vaut la peine d'essayer.
    """
    texte = str(value or "").strip()
    if not texte or "@" in texte:
        return False

    compact = _SEPARATEURS.sub("", texte)
    if compact.startswith("+"):
        compact = compact[1:]
    return bool(compact) and bool(_CHIFFRES.fullmatch(compact))


def phone_digits(value):
    """Les chiffres seuls d'une saisie, pour une recherche « contient ».

    La recherche de membre travaille sur des FRAGMENTS : « 77 000 » n'est pas
    un numéro et ne peut pas être normalisé. Mais les numéros sont désormais
    stockés sans séparateur — retirer ceux de la saisie suffit à faire se
    rencontrer les deux.

    Renvoie None quand la saisie n'a rien d'un numéro, pour qu'une recherche
    par nom ne devienne pas une recherche par chiffres.
    """
    if not looks_like_phone(value):
        return None

    compact = _SEPARATEURS.sub("", str(value).strip()).lstrip("+")
    return compact or None
