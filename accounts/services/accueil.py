# -*- coding: utf-8 -*-
"""
L'accueil d'un compte créé par un tiers : ce qu'il reste à faire au membre.

Un compte importé ou créé par un administrateur arrive avec un mot de passe que
d'autres connaissent, et parfois sans Daara — l'import n'affecte que ce que le
fichier désigne sans ambiguïté (voir `services.user_import`). À sa première
connexion, le membre complète donc lui-même, dans cet ordre :

    daara         → sa LDD, puis son Daara ;
    fiche         → date de naissance, genre, pays et ville de résidence ;
    mot_de_passe  → le sien, en remplacement du mot de passe attribué.

Le mot de passe vient EN DERNIER, et ce n'est pas un détail : c'est lui qui
éteint `must_change_password`, donc l'accueil. Placé en premier, un membre qui
fermait l'application juste après sortait du parcours avec sa fiche vide.

La liste est calculée ici, une fois, et servie par `UserSerializer.onboarding` :
le mobile et le web lisent la même règle au lieu de la recopier.

Ce n'est PAS la complétude du profil (`lib/profile-completion.ts`, sept
critères dont la photo et la pièce d'identité) : celle-ci reste un rappel
permanent, que l'on peut remettre à plus tard. L'accueil, lui, bloque.
"""

from accounts.models import User

#: Les rôles rattachés à un Daara — les seuls à qui l'on demande d'en choisir un.
ROLES_COMMUNAUTAIRES = (User.Role.MEMBER, User.Role.CHEF_DAARA, User.Role.COLLECTOR)

#: Les champs de la fiche exigés à l'accueil.
CHAMPS_FICHE = ('birth_date', 'gender', 'residence_country', 'city')


def fiche_incomplete(user) -> bool:
    return any(not (getattr(user, champ, None) or '') for champ in CHAMPS_FICHE)


def etapes_accueil(user) -> list[str]:
    """Les étapes qu'il reste, dans l'ordre où elles se font. Vide : accueil terminé."""
    communautaire = user.role in ROLES_COMMUNAUTAIRES
    sans_daara = communautaire and not user.daara_id

    # L'accueil n'existe que pour un compte qui vient d'arriver : mot de passe
    # attribué, ou Daara manquant. Un membre installé dont la fiche est vide
    # n'y est pas renvoyé — le rappel de complétude s'en charge.
    if not (sans_daara or user.must_change_password):
        return []

    etapes = []
    if sans_daara:
        etapes.append('daara')
    if communautaire and fiche_incomplete(user):
        etapes.append('fiche')
    if user.must_change_password:
        etapes.append('mot_de_passe')
    return etapes
