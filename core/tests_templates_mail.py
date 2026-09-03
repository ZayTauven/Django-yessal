"""Vérifie que les vingt-six gabarits de courriel rendent correctement.

Ces gabarits sont écrits à la main, en HTML de courriel — tables imbriquées,
styles en ligne, six mille caractères pièce. Leurs trois défauts possibles ont
en commun de ne PAS lever d'exception : ils partent chez le membre, et c'est
lui qui les découvre.

  · **Accolade simple survivante.** Les gabarits ont d'abord été écrits en
    `{prenom}` (la forme de `str.format`). Django ne connaît que `{{ prenom }}`
    et rend l'autre littéralement : « Bonjour {prenom}, ».

  · **Variable citée mais absente du contexte.** Django la remplace par une
    chaîne vide, sans un mot. La phrase garde sa ponctuation et perd son sens :
    « Votre Jëf de  FCFA a bien été enregistré. »

  · **Image en chemin relatif.** Une boîte mail n'a aucun contexte pour
    résoudre `src="illustrations/gift.png"` : l'emplacement reste vide.

Le test rend chaque gabarit avec un contexte réaliste et cherche ces trois
signatures. Il sert aussi de garde-fou au catalogue : un code déclaré dans
`SUJETS` sans gabarit correspondant échoue ici.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase

from core.mail import SUJETS, _contexte_commun, _gabarits_par_code, _rendu
from core.mail_samples import CONTEXTES

# `{x}` non précédé ni suivi d'une accolade : la forme `str.format`, que Django
# laisse telle quelle.
RESTE_SIMPLE = re.compile(r'(?<!\{)\{[a-z_]+\}(?!\})')
VAR_DJANGO = re.compile(r'\{\{\s*([a-z_]+)\s*(?:\|[^}]*)?\}\}')


def _source(code: str) -> str:
    """Source brute du gabarit.

    Le HTML rendu ne peut pas servir à repérer les variables manquantes :
    Django y a déjà remplacé par du vide celles qu'il n'a pas trouvées.
    """
    chemin = _gabarits_par_code()[(code, 'html')]
    return (Path(settings.BASE_DIR) / 'templates' / chemin).read_text(encoding='utf-8')


class GabaritsCourrielTests(TestCase):
    def test_chaque_code_a_son_gabarit(self):
        """Un code annoncé dans SUJETS sans gabarit part en erreur à l'envoi."""
        index = _gabarits_par_code()
        sans_gabarit = sorted(c for c in SUJETS if (c, 'html') not in index)

        self.assertEqual(
            sans_gabarit, [],
            f"codes déclarés sans gabarit HTML : {sans_gabarit}",
        )

    def test_chaque_gabarit_a_son_contexte_de_test(self):
        """Sans contexte, un gabarit ne serait pas couvert par les tests suivants."""
        non_couverts = sorted(set(SUJETS) - set(CONTEXTES))

        self.assertEqual(
            non_couverts, [],
            f"gabarits sans contexte de test : {non_couverts}",
        )

    def test_aucune_accolade_simple_ne_survit(self):
        for code in SUJETS:
            with self.subTest(code=code):
                html, _ = _rendu(code, _contexte_commun(dict(CONTEXTES[code])))
                restes = sorted(set(RESTE_SIMPLE.findall(html)))

                self.assertEqual(
                    restes, [],
                    f"{code} : accolades simples non interprétées par Django — "
                    f"{restes}. Utiliser {{{{ nom }}}}, pas {{nom}}.",
                )

    def test_toute_variable_citee_est_fournie(self):
        """Une variable absente du contexte est rendue en chaîne vide."""
        for code in SUJETS:
            with self.subTest(code=code):
                contexte = _contexte_commun(dict(CONTEXTES[code]))
                citees = set(VAR_DJANGO.findall(_source(code)))
                absentes = sorted(v for v in citees if v not in contexte)

                self.assertEqual(
                    absentes, [],
                    f"{code} : variables citées mais non fournies — {absentes}. "
                    f"Elles laisseraient un trou dans la phrase.",
                )

    def test_aucune_image_en_chemin_relatif(self):
        for code in SUJETS:
            with self.subTest(code=code):
                html, _ = _rendu(code, _contexte_commun(dict(CONTEXTES[code])))
                sources = re.findall(r'src="([^"]+)"', html)
                relatives = [
                    u for u in sources
                    if not u.startswith(('http://', 'https://', 'cid:', 'data:'))
                ]

                self.assertEqual(
                    relatives, [],
                    f"{code} : images en chemin relatif — {relatives}. "
                    f"Une boîte mail ne sait pas les résoudre ; passer par "
                    f"{{{{ illustrations }}}}.",
                )

    def test_la_version_texte_est_exploitable(self):
        """Elle est dérivée du HTML faute de `.txt` : vérifions qu'elle tient."""
        for code in SUJETS:
            with self.subTest(code=code):
                _, texte = _rendu(code, _contexte_commun(dict(CONTEXTES[code])))

                self.assertGreater(
                    len(texte), 150,
                    f"{code} : version texte suspecte ({len(texte)} car.)",
                )
                # Le HTML ne doit pas transparaître dans la version texte.
                self.assertNotIn('<td', texte)
                self.assertNotIn('style=', texte)

    def test_chaque_gabarit_porte_un_texte_d_apercu(self):
        """Ce que la boîte de réception affiche à côté de l'objet.

        Sans lui, elle y met le premier texte trouvé — souvent le nom de
        l'expéditeur répété, qui n'apprend rien à personne.
        """
        for code in SUJETS:
            with self.subTest(code=code):
                source = _source(code)

                self.assertIn(
                    'max-height:0', source,
                    f"{code} : pas de texte d'aperçu (span masqué en tête de body).",
                )


class LiensVersLeFrontTests(TestCase):
    """Les liens des courriels doivent mener quelque part.

    Quatre boutons pointaient vers des routes inventées — `/dashboard/titles`,
    `/dashboard/documents`, `/dashboard/collections` — qui n'existent pas dans
    l'application Next. Le membre recevait donc un message l'invitant à agir,
    et tombait sur « cette page n'existe pas » en cliquant.

    Rien ne signalait l'erreur : côté backend le gabarit est valide, côté front
    la route est simplement absente. Seul un test qui regarde les DEUX peut
    l'attraper. Il est ignoré si le dépôt front n'est pas à côté — le backend
    doit rester testable seul.
    """

    FRONT = Path(settings.BASE_DIR).parent / 'front-web' / 'src' / 'app'

    @classmethod
    def _routes_du_front(cls) -> set[str]:
        """Routes réelles, déduites de l'emplacement des `page.tsx`.

        Les groupes `(dashboard)` ne participent pas à l'URL ; les segments
        dynamiques `[id]` sont réduits à un joker.
        """
        routes = set()
        for page in cls.FRONT.rglob('page.tsx'):
            segments = page.relative_to(cls.FRONT).parent.parts
            gardes = [s for s in segments if not (s.startswith('(') and s.endswith(')'))]
            gardes = ['*' if s.startswith('[') else s for s in gardes]
            routes.add('/' + '/'.join(gardes) if gardes else '/')
        return routes

    def test_chaque_lien_mene_a_une_route_existante(self):
        if not self.FRONT.is_dir():
            self.skipTest("le dépôt front-web n'est pas présent à côté du backend")

        routes = self._routes_du_front()
        dossier = Path(settings.BASE_DIR) / 'templates' / 'emails'
        motif = re.compile(r'href="\{\{\s*base_url\s*\}\}([^"]*)"')

        morts = []
        for gabarit in sorted(dossier.glob('*.html')):
            for chemin in set(motif.findall(gabarit.read_text(encoding='utf-8'))):
                chemin = chemin.split('?')[0].rstrip('/')
                if not chemin:  # lien vers l'accueil
                    continue
                # Un segment dynamique du front accepte n'importe quelle valeur.
                candidat = chemin
                if candidat not in routes:
                    parts = candidat.strip('/').split('/')
                    joker = '/' + '/'.join(parts[:-1] + ['*'])
                    if joker not in routes:
                        morts.append(f'{gabarit.name} → {chemin}')

        self.assertEqual(
            morts, [],
            'liens de courriel vers des routes inexistantes :\n  '
            + '\n  '.join(morts),
        )
