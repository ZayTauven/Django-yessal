"""
Les médias privés : pièces d'identité et pièces jointes de la messagerie.

`/media/` est servi sans authentification — c'est voulu pour les avatars, les
bannières de Ndiguel, les images d'actualité. Mais `documents/` contient des
cartes d'identité, et `chat_files/` les fichiers échangés dans les salons :
rangés sous leur nom d'origine (`IMG-20261012-WA0003.jpg`, le nommage de
WhatsApp), ils se devinaient en balayant dates et compteurs. Et un lien
diffusé une fois restait valable pour toujours.

Ces deux dossiers ne se servent plus que sur un lien SIGNÉ. La signature
couvre le chemin et une date d'expiration ; elle naît dans `storage.url()`,
donc dans chaque réponse d'API qui expose le fichier — à un membre qui a le
droit de lire cette réponse. Aucun client n'a à changer : il reçoit une URL
et la charge, comme avant.

L'échéance est arrondie à la FENÊTRE : pendant douze heures, le même fichier
garde la même URL. Sans cet arrondi, chaque appel d'API produirait une URL
neuve, et le navigateur comme l'application rechargeraient l'image à chaque
écran. Un lien reste donc valable entre 12 et 24 heures.
"""

import posixpath
import time

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.http import Http404
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.static import serve

DOSSIERS_PRIVES = ('documents/', 'chat_files/')
FENETRE = 12 * 3600
_SEL = 'yessal.media-prive'


def est_prive(chemin: str) -> bool:
    return chemin.lstrip('/').startswith(DOSSIERS_PRIVES)


def _signature(chemin: str, echeance: int) -> str:
    return salted_hmac(_SEL, f'{chemin}:{echeance}').hexdigest()[:32]


def signer(chemin: str, maintenant: float | None = None) -> str:
    """La chaîne de requête qui ouvre `chemin` jusqu'à la fin de la fenêtre suivante."""
    maintenant = time.time() if maintenant is None else maintenant
    echeance = (int(maintenant) // FENETRE + 2) * FENETRE
    return f'e={echeance}&s={_signature(chemin, echeance)}'


def signature_valide(chemin: str, echeance: str | None, signature: str | None) -> bool:
    if not echeance or not signature or not echeance.isdigit():
        return False
    if int(echeance) < time.time():
        return False
    return constant_time_compare(signature, _signature(chemin, int(echeance)))


class StockageMedias(FileSystemStorage):
    """Le stockage local, qui signe les URL des dossiers privés."""

    def url(self, name):
        url = super().url(name)
        if name and est_prive(name):
            url = f'{url}?{signer(name.lstrip("/"))}'
        return url


def servir_media(request, path, document_root=None):
    """`django.views.static.serve`, sauf pour un dossier privé sans signature valide.

    404 et non 403 : un fichier privé ne doit pas même confirmer qu'il existe.

    Le chemin est normalisé AVANT le contrôle, comme `serve` le fera ensuite :
    sinon `avatars/../documents/cni.jpg` passait pour un avatar.
    """
    path = posixpath.normpath(path).lstrip('/')
    if est_prive(path) and not signature_valide(
        path, request.GET.get('e'), request.GET.get('s'),
    ):
        raise Http404
    response = serve(request, path, document_root=document_root or settings.MEDIA_ROOT)
    if est_prive(path):
        # Ni proxy, ni cache partagé : le lien expire, la copie ne doit pas
        # lui survivre ailleurs que chez qui l'a reçu.
        response['Cache-Control'] = 'private, max-age=3600'
    return response
