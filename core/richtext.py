"""
Assainissement du texte riche.

Depuis que l'éditeur des actualités produit du HTML, `NewsPost.content` n'est
plus du texte : c'est du balisage rendu tel quel par le web
(`dangerouslySetInnerHTML`) et par le mobile. Deux sinks d'injection, alimentés
par un seul champ.

Le contrôle est ici, et pas seulement dans l'éditeur, pour la même raison que
`validators.ValidateUploadSize` : l'API est joignable directement. Un
`PATCH /api/news/posts/<slug>/` avec `content="<img src=x onerror=…>"` ne passe
par aucun éditeur. Et un article vérolé n'atteint pas un lecteur mais tous les
membres, sur les deux plateformes.

La liste blanche est délibérément courte. Elle couvre ce que la barre d'outils
sait produire, rien de plus : ajouter un `<table>` ou un `<iframe>` ici sans
l'ajouter au rendeur mobile donnerait un article correct sur le web et cassé sur
téléphone. Le format doit rester le plus petit dénominateur des deux.

Repli sans `bleach` : le module reste importable et renvoie le texte échappé.
Mieux vaut un article affichant ses balises en clair qu'un serveur qui refuse de
démarrer — ou, pire, qui laisse passer le balisage brut.
"""

from __future__ import annotations

import html
import re

try:
    import bleach

    HAS_BLEACH = True
except ImportError:  # pragma: no cover - dépend de l'environnement
    bleach = None
    HAS_BLEACH = False


#: Balises acceptées dans le corps d'un article.
#:
#: Pas de `<h1>` : le titre de l'article occupe déjà ce niveau sur les deux
#: plateformes, et un second h1 dans le corps casse la structure du document
#: pour les lecteurs d'écran. La barre d'outils commence donc à h2.
ALLOWED_TAGS = [
    "p", "br", "hr",
    "strong", "b", "em", "i", "u", "s", "strike",
    "h2", "h3", "h4",
    "ul", "ol", "li",
    "blockquote",
    "a",
    "code", "pre",
]

#: `target` et `rel` sont VOLONTAIREMENT absents : ils ne sont pas acceptés en
#: entrée, ils sont posés en sortie par `_HardenAnchors`. Un appel direct à
#: l'API ne peut donc pas produire un lien externe sans `noopener`.
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title"],
}

#: `javascript:` et `data:` sont absents, et c'est tout l'intérêt de la liste.
ALLOWED_PROTOCOLS = ["http", "https", "mailto", "tel"]

_TAG_RE = re.compile(r"<[a-zA-Z/!][^>]*>")


def looks_like_html(value: str) -> bool:
    """
    Distingue un article écrit avec l'éditeur d'un article antérieur, saisi en
    texte brut dans l'ancien `<textarea>`.

    Les deux cohabitent dans la base : on ne migre pas le corpus existant, on
    apprend aux rendeurs à reconnaître lequel ils tiennent. Le web garde alors
    `whitespace-pre-wrap`, le mobile garde son `<Text>` — c'est-à-dire
    exactement ce qu'ils faisaient avant.
    """
    return bool(value) and bool(_TAG_RE.search(value))


def sanitize_html(value: str | None) -> str:
    """
    Réduit `value` à la liste blanche.

    Un texte brut traverse sans dommage : sans balise à retirer, `bleach` se
    contente d'échapper les `<` et `&` isolés — ce qui est le comportement
    voulu pour un article de l'ancien format qu'on repasserait par ici.
    """
    if not value:
        return ""

    if not HAS_BLEACH:
        # Sans la dépendance, on refuse tout balisage plutôt que d'en laisser
        # passer une partie. L'article s'affiche en clair : visible, inoffensif.
        return html.escape(value)

    return _CLEANER.clean(value)


if HAS_BLEACH:
    from bleach.html5lib_shim import Filter

    class _HardenAnchors(Filter):
        """
        Pose `target` et `rel` sur les liens sortants.

        Un lien ouvert dans un nouvel onglet donne à la page cible une
        référence `window.opener` sur la nôtre, qui lui suffit à nous
        rediriger. `noopener` coupe cette référence.

        Le filtre tourne APRÈS l'assainisseur dans la chaîne de `Cleaner` :
        il travaille donc sur des balises déjà réduites à la liste blanche, et
        ce qu'il ajoute n'y est plus soumis. C'est ce qui permet de refuser ces
        deux attributs en entrée tout en les garantissant en sortie.
        """

        def __iter__(self):
            for token in Filter.__iter__(self):
                if token.get("type") in ("StartTag", "EmptyTag") and token.get("name") == "a":
                    data = token.setdefault("data", {})
                    href = data.get((None, "href"), "")
                    if href.startswith(("http://", "https://")):
                        data[(None, "target")] = "_blank"
                        data[(None, "rel")] = "noopener noreferrer"
                yield token

    _CLEANER = bleach.Cleaner(
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
        filters=[_HardenAnchors],
    )
