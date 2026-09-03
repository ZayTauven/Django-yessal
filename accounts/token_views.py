"""Rafraîchissement de jeton respectant la révocation.

Séparé de `authentication.py` à dessein : ce module importe les vues et
sérialiseurs de SimpleJWT, ce qui est trop lourd pour un module que DRF résout
paresseusement au chargement des settings (voir l'avertissement là-bas).
"""

from django.contrib.auth import get_user_model

from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from .authentication import TOKEN_VERSION_CLAIM


class VersionedTokenRefreshSerializer(TokenRefreshSerializer):
    """Refuse de rafraîchir un jeton d'une génération périmée.

    Sans cela, la révocation ne tiendrait qu'une heure — et encore : le jeton de
    rafraîchissement, lui, vit un jour. Un intrus qui a été « déconnecté » n'a
    qu'à appeler `/api/auth/refresh/` pour se voir remettre un jeton d'accès
    neuf, et ainsi de suite pendant vingt-quatre heures. La révocation ne serait
    alors qu'une gêne.

    `TokenRefreshView` n'emprunte pas la classe d'authentification — elle valide
    le jeton de rafraîchissement elle-même, sans jamais charger l'utilisateur.
    Le contrôle doit donc être refait ici.
    """

    def validate(self, attrs):
        data = super().validate(attrs)

        refresh = RefreshToken(attrs['refresh'])
        user_id = refresh.get(api_settings.USER_ID_CLAIM)

        User = get_user_model()
        user = User.objects.filter(**{api_settings.USER_ID_FIELD: user_id}).first()
        if user is None:
            raise AuthenticationFailed("Compte introuvable.", code='user_not_found')

        if refresh.get(TOKEN_VERSION_CLAIM, 0) != user.token_version:
            raise AuthenticationFailed(
                "Votre session a été fermée. Reconnectez-vous.",
                code='token_revoked',
            )

        return data


class VersionedTokenRefreshView(TokenRefreshView):
    serializer_class = VersionedTokenRefreshSerializer
