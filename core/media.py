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

import mimetypes
import os
import posixpath
import re
import time

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.utils._os import safe_join
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.http import http_date
from django.views.static import serve

DOSSIERS_PRIVES = ('documents/', 'chat_files/')
FENETRE = 12 * 3600
_SEL = 'yessal.media-prive'
_PLAGE = re.compile(r'^bytes=(\d*)-(\d*)$')
_BLOC = 64 * 1024


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


def _tranche(chemin, debut, longueur):
    with open(chemin, 'rb') as fichier:
        fichier.seek(debut)
        while longueur > 0:
            bloc = fichier.read(min(_BLOC, longueur))
            if not bloc:
                return
            longueur -= len(bloc)
            yield bloc


def _reponse_partielle(request, path, document_root):
    """Une portion de fichier (206), ou None pour laisser `serve` tout renvoyer.

    `serve` ignore l'en-tête `Range` : avancer dans une vidéo obligeait à la
    télécharger jusque-là, et le lecteur de l'iPhone refuse une vidéo servie
    sans portions. Une demande de plusieurs plages reçoit le fichier entier, ce
    que la norme autorise.
    """
    plage = _PLAGE.match(request.META.get('HTTP_RANGE', ''))
    if request.method not in ('GET', 'HEAD') or not plage:
        return None
    try:
        chemin = safe_join(document_root, path)
    except ValueError:
        return None
    if not os.path.isfile(chemin):
        return None

    infos = os.stat(chemin)
    taille = infos.st_size
    # `If-Range` : si le fichier a changé depuis la première portion, on repart
    # du fichier entier plutôt que de coller deux versions.
    condition = request.META.get('HTTP_IF_RANGE')
    if taille == 0 or (condition and condition != http_date(infos.st_mtime)):
        return None

    debut, fin = plage.groups()
    if not debut and not fin:
        return None
    if debut:
        premier = int(debut)
        dernier = min(int(fin), taille - 1) if fin else taille - 1
    else:
        premier = max(taille - int(fin), 0)
        dernier = taille - 1 if int(fin) > 0 else -1
    if premier >= taille or dernier < premier:
        reponse = HttpResponse(status=416)
        reponse['Content-Range'] = f'bytes */{taille}'
        return reponse

    longueur = dernier - premier + 1
    type_mime, _encodage = mimetypes.guess_type(chemin)
    reponse = StreamingHttpResponse(
        _tranche(chemin, premier, longueur),
        status=206,
        content_type=type_mime or 'application/octet-stream',
    )
    reponse['Content-Length'] = str(longueur)
    reponse['Content-Range'] = f'bytes {premier}-{dernier}/{taille}'
    reponse['Last-Modified'] = http_date(infos.st_mtime)
    return reponse


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
    racine = document_root or settings.MEDIA_ROOT
    response = _reponse_partielle(request, path, racine) or serve(request, path, document_root=racine)
    response['Accept-Ranges'] = 'bytes'
    if est_prive(path):
        # Ni proxy, ni cache partagé : le lien expire, la copie ne doit pas
        # lui survivre ailleurs que chez qui l'a reçu.
        response['Cache-Control'] = 'private, max-age=3600'
    return response
