"""
Le hacheur des mots de passe PROVISOIRES.

Un import Excel crée des centaines de comptes d'un coup, chacun avec son propre
mot de passe aléatoire. Au coût du hacheur par défaut — 0,44 s, 1,2 million
d'itérations — 260 lignes tenaient près de deux minutes, et gunicorn tuait la
requête à trente secondes. C'est ce coût qui avait conduit à un mot de passe
COMMUN, haché une seule fois : connu de tous, il ouvrait chaque compte importé.

Les itérations de PBKDF2 ralentissent qui devine un mot de passe humain dans
une base volée. Un provisoire tiré par `secrets` sur 12 caractères porte près
de 60 bits d'entropie : vingt mille itérations suffisent à le rendre hors de
portée. Et il ne dure pas : Django rehache avec le hacheur par défaut à la
première connexion réussie, et le membre doit le remplacer à son accueil.
"""

from django.contrib.auth.hashers import PBKDF2PasswordHasher


class ProvisoirePBKDF2PasswordHasher(PBKDF2PasswordHasher):
    algorithm = "pbkdf2_provisoire"
    iterations = 20_000
