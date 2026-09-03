"""Envoi de courriels aux membres.

═══════════════════════════════════════════════════════════════════════════
Trois contraintes propres à ce produit, avant toute chose
═══════════════════════════════════════════════════════════════════════════

1. **Beaucoup de membres n'ont pas d'adresse e-mail.**
   `User.email` est `null=True` : l'inscription se fait au choix par e-mail OU
   par téléphone, et une part des comptes est créée sur le terrain par un
   collecteur, sans adresse. Un envoi sans destinataire n'est donc PAS une
   erreur — c'est le cas courant. Il est journalisé et ignoré.

   Corollaire : le courriel ne peut jamais être l'unique canal d'une
   information qui compte. Il double la notification en base
   (`comms.Notification`) et la notification push, il ne les remplace pas.

2. **Un échec d'envoi ne doit jamais faire échouer l'action métier.**
   Confirmer un virement, valider une pièce d'identité, approuver un titre :
   ces écritures sont la raison d'être de la requête. Un serveur SMTP
   injoignable ne doit pas les annuler. Toutes les fonctions d'envoi
   attrapent, journalisent, et rendent la main.

3. **Il n'y a pas de file d'attente dans ce projet** (ni Celery, ni RQ).
   Un envoi unitaire part donc dans la requête — acceptable, c'est une
   seconde. Un envoi de masse, lui, ne peut pas : prévenir 500 membres d'un
   changement de date de fête tiendrait la requête plusieurs minutes et
   finirait en délai dépassé. `send_to_users` ouvre UNE connexion SMTP et
   travaille dans un fil séparé. C'est un compromis, pas une architecture :
   au-delà de quelques milliers de destinataires, il faudra une vraie file.

═══════════════════════════════════════════════════════════════════════════
Utilisation
═══════════════════════════════════════════════════════════════════════════

    from core.mail import send_to_user

    send_to_user(donation.donor, 'don_confirme', {
        'donation': donation,
        'campaign': donation.campaign,
    })

`'don_confirme'` est un CODE : il désigne à la fois le sujet (déclaré dans
`SUJETS` plus bas) et la paire de gabarits
`templates/emails/don_confirme.{html,txt}`. Le catalogue complet des courriels
et de leurs variables est dans `docs/EMAILS.md`.
"""

import logging
import re
import threading
from html import unescape
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template.exceptions import TemplateDoesNotExist
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Expressions utilisées par le repli texte (compilées une fois)
# ═══════════════════════════════════════════════════════════════════════════
_RE_BLOCS_INERTES = re.compile(r'<(script|style|head)\b.*?</\1>', re.S | re.I)
_RE_COMMENTAIRES = re.compile(r'<!--.*?-->', re.S)
_RE_LIEN = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
_RE_FIN_DE_BLOC = re.compile(r'</(?:p|tr|div|h[1-6])>|<br\s*/?>', re.I)
# Un <span> porteur de `display:block` est un bloc, quoi qu'en dise son nom :
# les gabarits s'en servent pour les sur-titres, faute de <div> fiable en
# courriel. Sans cette regle, le sur-titre se colle a la valeur qui suit.
_RE_SPAN_BLOC = re.compile(
    r'<span[^>]*display:\s*block[^>]*>(.*?)</span>', re.S | re.I
)
_RE_LIGNES_VIDES = re.compile(r'\n{3,}')


# ═══════════════════════════════════════════════════════════════════════════
# Sujets
# ═══════════════════════════════════════════════════════════════════════════
# Centralisés ici plutôt que dans chaque gabarit : un sujet se relit d'un coup
# d'œil quand ils sont côte à côte, et on repère aussitôt les doublons et les
# formulations qui divergent. Le corps, lui, reste dans le gabarit.
#
# Les sujets acceptent les variables du contexte (`{campagne}`), résolues au
# formatage. Une variable absente ne fait pas échouer l'envoi : le sujet est
# alors rendu tel quel (voir `_sujet`).
SUJETS = {
    # ── Compte et accès ────────────────────────────────────────────────────
    'inscription_recue':      "Votre inscription à Yessal Gui a bien été reçue",
    'compte_valide':          "Votre compte Yessal Gui est actif",
    'compte_bloque':          "Votre accès à Yessal Gui a été suspendu",
    'mot_de_passe_oublie':    "Réinitialisez votre mot de passe Yessal Gui",
    'mot_de_passe_provisoire': "Un mot de passe provisoire vous a été attribué",
    'mot_de_passe_modifie':   "Votre mot de passe Yessal Gui a été modifié",

    # ── Documents ──────────────────────────────────────────────────────────
    'document_recu':          "Votre document a bien été reçu",
    'document_valide':        "Votre document a été validé",
    'document_a_corriger':    "Votre document doit être corrigé",
    'document_a_valider':     "Un document attend votre validation",

    # ── Titres ─────────────────────────────────────────────────────────────
    'titre_approuve':         "Votre demande de titre a été approuvée",
    'titre_refuse':           "Votre demande de titre n'a pas été retenue",
    'titre_attribue':         "Un titre vous a été attribué",
    'titre_a_examiner':       "Une demande de titre attend votre examen",

    # ── Jëfs ───────────────────────────────────────────────────────────────
    'jef_enregistre':         "Votre Jëf a bien été enregistré",
    'jef_a_collecter':        "Un Jëf est à collecter",
    'virement_instructions':  "Comment effectuer votre virement",
    'virement_confirme':      "Votre virement a été confirmé",
    'paiement_confirme':      "Votre paiement a été confirmé",
    'paiement_echoue':        "Votre paiement n'a pas abouti",

    # ── Ndiguels et fêtes ──────────────────────────────────────────────────
    'ndiguel_responsable':    "Vous êtes responsable du Ndiguel {campagne}",
    'ndiguel_echeance':       "Le Ndiguel {campagne} se termine bientôt",
    'fete_date_modifiee':     "Nouvelle date pour {fete}",

    # ── Communauté ─────────────────────────────────────────────────────────
    'promotion_collecteur':   "Vous êtes désormais collecteur",
    'invitation_salon':       "Vous êtes invité(e) dans un salon de discussion",
    'annonce':                "{titre}",
}


def _sujet(code: str, contexte: dict) -> str:
    """Sujet du courriel, variables résolues.

    Un `KeyError` sur une variable manquante enverrait un courriel sans sujet,
    ou pas de courriel du tout. On préfère un sujet imparfait à un silence :
    le gabarit brut part tel quel, et l'anomalie est journalisée.
    """
    brut = SUJETS.get(code)
    if not brut:
        logger.error("Code de courriel inconnu : %r", code)
        return "Yessal Gui"
    try:
        return brut.format(**contexte)
    except (KeyError, IndexError):
        logger.warning("Variable absente dans le sujet de %r : %r", code, brut)
        return brut


@lru_cache(maxsize=None)
def _gabarits_par_code() -> dict[tuple[str, str], str]:
    """Associe (code, extension) au chemin de gabarit qui lui correspond.

    Les gabarits sont nommés `A1-inscription_recue.html` : le préfixe reprend
    la référence du catalogue (docs/EMAILS.md), ce qui garde les vingt-six
    fichiers triés par section dans l'explorateur. Le module, lui, ne connaît
    que le code — `inscription_recue`.

    Ce répertoire réconcilie les deux : on scanne une fois le dossier, et on
    accepte aussi bien `<code>.html` que `<préfixe>-<code>.html`. Renommer les
    fichiers pour satisfaire le code aurait sacrifié un classement utile à une
    contrainte technique ; c'est l'inverse qui doit céder.

    Mise en cache : le dossier ne change pas en cours d'exécution. En
    développement, un ajout de gabarit demande donc un redémarrage — le
    rechargement automatique de Django s'en charge.
    """
    dossier = Path(settings.BASE_DIR) / 'templates' / 'emails'
    index: dict[tuple[str, str], str] = {}
    if not dossier.is_dir():
        return index

    for fichier in sorted(dossier.iterdir()):
        if not fichier.is_file() or fichier.suffix not in ('.html', '.txt'):
            continue
        tige = fichier.stem
        if tige.startswith('_'):  # coques et partiels
            continue
        ext = fichier.suffix.lstrip('.')
        # Nom exact, puis nom préfixé : `A1-inscription_recue` -> `inscription_recue`.
        index.setdefault((tige, ext), f'emails/{fichier.name}')
        if '-' in tige:
            code = tige.split('-', 1)[1]
            index.setdefault((code, ext), f'emails/{fichier.name}')
    return index


def _rendu(code: str, contexte: dict) -> tuple[str, str]:
    """Renvoie (html, texte).

    La version texte est obligatoire : certains clients de messagerie la
    préfèrent, et un courriel qui n'a QUE du HTML est un signal de pourriel.
    Si aucun `.txt` n'existe, elle est dérivée du HTML — dégradé, mais
    toujours mieux que rien.
    """
    index = _gabarits_par_code()

    chemin_html = index.get((code, 'html'))
    if not chemin_html:
        raise TemplateDoesNotExist(f'emails/{code}.html')
    html = render_to_string(chemin_html, contexte)

    chemin_txt = index.get((code, 'txt'))
    texte = render_to_string(chemin_txt, contexte) if chemin_txt else _texte_depuis_html(html)
    return html, texte


def _texte_depuis_html(html: str) -> str:
    """Version texte de repli, quand aucun `.txt` n'accompagne le gabarit.

    `strip_tags` seul ne suffit pas sur du HTML de courriel : la mise en page
    par tableaux imbriques laisse des dizaines de lignes vides et des blocs
    d'indentation entre deux mots. Le resultat est illisible — et c'est ce que
    voient les clients regles en texte seul, ainsi que les filtres anti-spam.

    On retire d'abord ce qui n'est pas du contenu (styles, scripts, entetes
    conditionnels Outlook), on transforme les liens en « libelle (URL) » pour
    ne pas perdre les adresses, puis on resserre les blancs.

    Un vrai `.txt` reste preferable : ce repli garde le sens, pas la forme.
    """
    texte = _RE_BLOCS_INERTES.sub(' ', html)
    texte = _RE_COMMENTAIRES.sub(' ', texte)

    # Un lien perdrait son URL en passant par strip_tags.
    texte = _RE_LIEN.sub(
        # Espace en tete : sans lui, le lien se colle au mot precedent
        # (« copiez ce lienhttps://... »).
        lambda m: f' {strip_tags(m.group(2)).strip()} ({m.group(1)}) ',
        texte,
    )

    # Les fins de bloc deviennent des sauts de ligne, sinon deux paragraphes
    # se retrouvent colles en une seule phrase.
    texte = _RE_SPAN_BLOC.sub(lambda m: f'\n{m.group(1)}\n', texte)
    texte = _RE_FIN_DE_BLOC.sub('\n', texte)

    texte = unescape(strip_tags(texte))
    texte = '\n'.join(ligne.strip() for ligne in texte.splitlines())
    texte = _RE_LIGNES_VIDES.sub('\n\n', texte)
    return texte.strip()


def _contexte_commun(contexte: dict | None) -> dict:
    """Variables disponibles dans TOUS les gabarits, sans les passer à chaque appel."""
    complet = {
        'base_url': settings.BASE_URL,
        # Base absolue des images : un chemin relatif ne se résout pas dans
        # une boîte mail. Voir EMAIL_ASSETS_URL dans settings.
        'illustrations': settings.EMAIL_ASSETS_URL,
        'nom_expediteur': settings.EMAIL_FROM_NAME,
        'annee': __import__('datetime').date.today().year,
    }
    complet.update(contexte or {})
    return complet


def _expediteur() -> str:
    return f'{settings.EMAIL_FROM_NAME} <{settings.DEFAULT_FROM_EMAIL}>'


def _message(destinataire: str, code: str, contexte: dict) -> EmailMultiAlternatives:
    html, texte = _rendu(code, contexte)
    message = EmailMultiAlternatives(
        subject=_sujet(code, contexte),
        body=texte,
        from_email=_expediteur(),
        to=[destinataire],
        reply_to=[settings.EMAIL_REPLY_TO] if settings.EMAIL_REPLY_TO else None,
    )
    message.attach_alternative(html, 'text/html')
    return message


def send_to_user(user, code: str, contexte: dict | None = None) -> bool:
    """Envoie un courriel à un membre. Renvoie True si le message est parti.

    Ne lève jamais. Un membre sans adresse, un gabarit manquant ou un serveur
    SMTP muet donnent False et une ligne de journal — jamais une exception qui
    remonterait annuler l'action métier en cours.
    """
    adresse = (getattr(user, 'email', '') or '').strip()
    if not adresse:
        # Cas courant, pas une anomalie : une partie des membres est inscrite
        # par téléphone. En DEBUG seulement, pour ne pas noyer la production.
        logger.debug("Courriel %r ignoré : le membre #%s n'a pas d'adresse.",
                     code, getattr(user, 'pk', '?'))
        return False

    contexte = _contexte_commun(contexte)
    contexte.setdefault('user', user)
    contexte.setdefault('prenom', (getattr(user, 'first_name', '') or '').strip())

    if not settings.EMAIL_ENABLED:
        logger.info("EMAIL_ENABLED=False — courriel %r NON envoyé à %s.", code, adresse)
        return False

    try:
        _message(adresse, code, contexte).send(fail_silently=False)
        logger.info("Courriel %r envoyé à %s.", code, adresse)
        return True
    except Exception:
        logger.exception("Échec d'envoi du courriel %r à %s.", code, adresse)
        return False


def send_to_users(users, code: str, contexte_pour=None, contexte: dict | None = None) -> None:
    """Envoi de masse, hors requête.

    `contexte_pour(user)` permet de personnaliser par destinataire ; à défaut,
    `contexte` est partagé par tous.

    Le travail part dans un fil séparé sur UNE connexion SMTP : autrement, la
    requête qui déplace la date d'une fête attendrait l'envoi de plusieurs
    centaines de messages. Ce fil n'est pas supervisé — s'il meurt, personne
    ne le relance. C'est acceptable pour un rappel de date ; ce ne le serait
    pas pour un reçu de paiement, qui doit passer par `send_to_user`.
    """
    destinataires = [
        (u, (getattr(u, 'email', '') or '').strip())
        for u in users
    ]
    destinataires = [(u, a) for u, a in destinataires if a]

    if not destinataires:
        logger.info("Courriel de masse %r : aucun destinataire avec adresse.", code)
        return

    if not settings.EMAIL_ENABLED:
        logger.info("EMAIL_ENABLED=False — %d courriel(s) %r NON envoyés.",
                    len(destinataires), code)
        return

    def _travail():
        envoyes = 0
        try:
            connexion = get_connection()
            connexion.open()
        except Exception:
            logger.exception("Courriel de masse %r : connexion SMTP impossible.", code)
            return

        try:
            for user, adresse in destinataires:
                ctx = _contexte_commun(
                    contexte_pour(user) if contexte_pour else dict(contexte or {})
                )
                ctx.setdefault('user', user)
                ctx.setdefault('prenom', (getattr(user, 'first_name', '') or '').strip())
                try:
                    message = _message(adresse, code, ctx)
                    message.connection = connexion
                    message.send(fail_silently=False)
                    envoyes += 1
                except Exception:
                    # Une adresse invalide ne doit pas arrêter les 499 autres.
                    logger.warning("Courriel %r : échec pour %s.", code, adresse,
                                   exc_info=True)
        finally:
            try:
                connexion.close()
            except Exception:
                pass
            logger.info("Courriel de masse %r : %d/%d envoyé(s).",
                        code, envoyes, len(destinataires))

    threading.Thread(target=_travail, name=f'mail-{code}', daemon=True).start()
