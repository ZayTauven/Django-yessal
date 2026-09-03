"""Authentification JWT vérifiant la génération du jeton.

Un JWT se valide par lui-même : le serveur n'a aucune trace des sessions
ouvertes. Un jeton reste donc utilisable jusqu'à son expiration — une heure ici
— même après un changement de mot de passe. Autrement dit, révoquer l'accès de
quelqu'un ne le déconnectait pas.

C'est un problème dans le cas précis qui motive une réinitialisation : un membre
qui soupçonne qu'un autre utilise son compte demande à un administrateur de
changer son mot de passe. Sans ce qui suit, l'intrus gardait la main pendant
l'heure suivante — et pouvait s'y maintenir en rafraîchissant son jeton.

`User.token_version` porte un numéro de génération. Chaque jeton émis l'embarque
dans son `tv`. Ici, on refuse tout jeton dont le numéro ne correspond plus à
celui du compte. Incrémenter le compteur invalide donc d'un coup toutes les
sessions, sur tous les appareils, sans table de révocation.

Le contrôle ne coûte aucune requête supplémentaire : `get_user` chargeait déjà
l'utilisateur depuis la base.

⚠️ CE MODULE RESTE VOLONTAIREMENT MINIMAL EN IMPORTS.

Il est désigné par `DEFAULT_AUTHENTICATION_CLASSES`, et DRF le résout
paresseusement — parfois au beau milieu de l'import d'un autre module. S'il
importe à son tour quelque chose d'assez lourd (les vues de SimpleJWT, par
exemple), Python le trouve à moitié construit et lève un « does not define a
VersionedJWTAuthentication attribute », qui ne dit rien du vrai problème.

La vue de rafraîchissement versionnée vit donc dans `token_views.py`, pas ici.
"""

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed

TOKEN_VERSION_CLAIM = 'tv'


class VersionedJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)

        # Un jeton sans `tv` a été émis avant la mise en place du mécanisme.
        # On le traite comme la génération 0, ce qui laisse les sessions
        # existantes valides jusqu'à leur expiration naturelle plutôt que de
        # déconnecter tout le monde au déploiement.
        token_version = validated_token.get(TOKEN_VERSION_CLAIM, 0)

        if token_version != user.token_version:
            raise AuthenticationFailed(
                "Votre session a été fermée. Reconnectez-vous.",
                code='token_revoked',
            )

        return user
