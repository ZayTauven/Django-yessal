"""
Le pays de résidence d'un membre : un code ISO 3166-1 alpha-2, jamais un nom.

Le champ était un texte libre. La répartition des Jëfs par pays regroupait les
saisies en retirant accents et casse, ce qui réunissait « Sénégal » et
« SENEGAL » mais laissait passer tout le reste : « Senegl » devenait un pays à
part ET comptait dans la part de la diaspora, « Italy » doublait « Italie »,
« Côte d'Ivoire » et « Côte d’Ivoire » faisaient deux lignes. Au 2026-11-02 la
démo n'en contenait encore aucune, mais 242 membres sur 276 n'avaient pas
rempli le champ : l'accueil allait le leur demander.

On stocke donc « SN », « FR », « IT » ; le nom affiché vient de cette table et
d'elle seule. Les formulaires proposent une liste, et le serveur refuse ce
qu'il ne reconnaît pas.

═══════════════════════════════════════════════════════════════════════════
La liste est la même dans les trois dépôts
═══════════════════════════════════════════════════════════════════════════
Elle reprend `yessal-mobile/lib/dial-codes.ts` (le sélecteur d'indicatif),
et `front-web/src/lib/pays.ts` la recopie. Ajouter un pays, c'est ajouter la
ligne aux trois — le serveur d'abord : un code qu'il ne connaît pas est
refusé, quel que soit l'écran qui l'envoie.

═══════════════════════════════════════════════════════════════════════════
Les anciennes saisies restent acceptées
═══════════════════════════════════════════════════════════════════════════
Les applications mobiles déjà installées envoient encore un nom tapé à la
main. `code_pays` reconnaît le nom français de chaque pays, sans accents ni
casse, et quelques graphies courantes (« USA », « Italy », « Ivory Coast »).
Ce qui n'est rien de tout cela est refusé, au lieu de fausser la métrique.
"""

import re
import unicodedata

#: `(code ISO, nom en français)`, dans l'ordre de la liste proposée.
PAYS = [
    # Afrique de l'Ouest — le cœur de la confrérie
    ('SN', "Sénégal"),
    ('ML', "Mali"),
    ('MR', "Mauritanie"),
    ('GM', "Gambie"),
    ('GN', "Guinée"),
    ('GW', "Guinée-Bissau"),
    ('CI', "Côte d'Ivoire"),
    ('BF', "Burkina Faso"),
    ('NE', "Niger"),
    ('TG', "Togo"),
    ('BJ', "Bénin"),
    ('GH', "Ghana"),
    ('NG', "Nigeria"),
    ('SL', "Sierra Leone"),
    ('LR', "Liberia"),
    ('CV', "Cap-Vert"),
    # Reste de l'Afrique
    ('MA', "Maroc"),
    ('DZ', "Algérie"),
    ('TN', "Tunisie"),
    ('LY', "Libye"),
    ('EG', "Égypte"),
    ('CM', "Cameroun"),
    ('GA', "Gabon"),
    ('CG', "Congo"),
    ('CD', "Congo (RDC)"),
    ('TD', "Tchad"),
    ('CF', "Centrafrique"),
    ('GQ', "Guinée équatoriale"),
    ('ST', "Sao Tomé-et-Principe"),
    ('AO', "Angola"),
    ('ZA', "Afrique du Sud"),
    ('KE', "Kenya"),
    ('TZ', "Tanzanie"),
    ('UG', "Ouganda"),
    ('RW', "Rwanda"),
    ('BI', "Burundi"),
    ('ET', "Éthiopie"),
    ('SO', "Somalie"),
    ('DJ', "Djibouti"),
    ('SD', "Soudan"),
    ('MZ', "Mozambique"),
    ('ZM', "Zambie"),
    ('ZW', "Zimbabwe"),
    ('MW', "Malawi"),
    ('BW', "Botswana"),
    ('NA', "Namibie"),
    ('MG', "Madagascar"),
    ('MU', "Maurice"),
    ('KM', "Comores"),
    ('SC', "Seychelles"),
    # Europe
    ('FR', "France"),
    ('IT', "Italie"),
    ('ES', "Espagne"),
    ('PT', "Portugal"),
    ('BE', "Belgique"),
    ('DE', "Allemagne"),
    ('NL', "Pays-Bas"),
    ('GB', "Royaume-Uni"),
    ('IE', "Irlande"),
    ('CH', "Suisse"),
    ('AT', "Autriche"),
    ('LU', "Luxembourg"),
    ('SE', "Suède"),
    ('NO', "Norvège"),
    ('DK', "Danemark"),
    ('FI', "Finlande"),
    ('IS', "Islande"),
    ('PL', "Pologne"),
    ('CZ', "Tchéquie"),
    ('SK', "Slovaquie"),
    ('HU', "Hongrie"),
    ('RO', "Roumanie"),
    ('BG', "Bulgarie"),
    ('GR', "Grèce"),
    ('HR', "Croatie"),
    ('SI', "Slovénie"),
    ('RS', "Serbie"),
    ('AL', "Albanie"),
    ('UA', "Ukraine"),
    ('RU', "Russie"),
    ('TR', "Turquie"),
    ('CY', "Chypre"),
    ('MT', "Malte"),
    ('EE', "Estonie"),
    ('LV', "Lettonie"),
    ('LT', "Lituanie"),
    # Amériques
    ('US', "États-Unis"),
    ('CA', "Canada"),
    ('MX', "Mexique"),
    ('BR', "Brésil"),
    ('AR', "Argentine"),
    ('CL', "Chili"),
    ('CO', "Colombie"),
    ('PE', "Pérou"),
    ('VE', "Venezuela"),
    # Moyen-Orient et Golfe
    ('SA', "Arabie saoudite"),
    ('AE', "Émirats arabes unis"),
    ('QA', "Qatar"),
    ('KW', "Koweït"),
    ('BH', "Bahreïn"),
    ('OM', "Oman"),
    ('JO', "Jordanie"),
    ('LB', "Liban"),
    ('IL', "Israël"),
    ('IQ', "Irak"),
    ('IR', "Iran"),
    ('YE', "Yémen"),
    # Asie et Océanie
    ('CN', "Chine"),
    ('HK', "Hong Kong"),
    ('JP', "Japon"),
    ('KR', "Corée du Sud"),
    ('IN', "Inde"),
    ('PK', "Pakistan"),
    ('BD', "Bangladesh"),
    ('ID', "Indonésie"),
    ('MY', "Malaisie"),
    ('SG', "Singapour"),
    ('TH', "Thaïlande"),
    ('VN', "Viêt Nam"),
    ('PH', "Philippines"),
    ('AU', "Australie"),
    ('NZ', "Nouvelle-Zélande"),
]

NOMS = dict(PAYS)
CHOIX = PAYS

#: Le pays « local » : un membre qui y réside n'est pas de la diaspora.
PAYS_LOCAL = 'SN'

#: Graphies courantes qui ne sont pas le nom français de la table.
_AUTRES_GRAPHIES = {
    'senegale': 'SN',
    'usa': 'US', 'etats unis d amerique': 'US', 'united states': 'US',
    'uk': 'GB', 'angleterre': 'GB', 'united kingdom': 'GB',
    'italy': 'IT', 'italia': 'IT', 'spain': 'ES', 'espana': 'ES',
    'germany': 'DE', 'belgium': 'BE', 'netherlands': 'NL', 'hollande': 'NL',
    'gambia': 'GM', 'mauritania': 'MR', 'morocco': 'MA', 'ivory coast': 'CI',
    'switzerland': 'CH', 'brazil': 'BR', 'china': 'CN',
    'turkey': 'TR', 'turkiye': 'TR', 'rdc': 'CD', 'congo brazzaville': 'CG',
    'congo kinshasa': 'CD', 'emirats': 'AE', 'uae': 'AE',
}


def _cle(texte):
    """« Côte d’Ivoire » → « cote d ivoire » : sans accents, casse ni ponctuation."""
    texte = unicodedata.normalize('NFKD', texte.strip().lower())
    texte = ''.join(c for c in texte if not unicodedata.combining(c))
    return re.sub(r'[^a-z]+', ' ', texte).strip()


_PAR_NOM = {_cle(nom): code for code, nom in PAYS}
_PAR_NOM.update(_AUTRES_GRAPHIES)


def code_pays(valeur):
    """Le code ISO d'une saisie — code ou nom connu —, ou `None` si inconnue.

    « SN », « sn », « Sénégal », « SENEGAL » → « SN ». « Senegl » → `None`.
    """
    if not valeur:
        return None
    brut = str(valeur).strip()
    if brut.upper() in NOMS:
        return brut.upper()
    return _PAR_NOM.get(_cle(brut))


def nom_pays(code):
    """« SN » → « Sénégal ». Un code inconnu est rendu tel quel."""
    if not code:
        return None
    return NOMS.get(code, code)


def est_diaspora(code):
    """Un pays renseigné, et qui n'est pas le Sénégal.

    Un pays NON renseigné compte comme le Sénégal : c'est le cas de la
    majorité des membres, et les classer d'office dans la diaspora gonflerait
    sa part de tout ce que l'on ne sait pas.
    """
    return bool(code) and code != PAYS_LOCAL
