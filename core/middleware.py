import os

from django.utils.deprecation import MiddlewareMixin
from accounts.models import AuditLog


class PublicHostMiddleware(MiddlewareMixin):
    """Force le Host utilisé pour construire les URLs absolues.

    En conteneur, le front appelle le backend via le DNS interne
    (``http://backend:8000``) : sans cela, ``build_absolute_uri()`` renvoie des
    URLs de médias en ``backend:8000``, injoignables depuis le navigateur.
    Renseigner ``PUBLIC_HOST`` (ex. ``localhost:8000``) corrige les avatars,
    documents et images d'événements.
    """

    def process_request(self, request):
        public_host = os.environ.get('PUBLIC_HOST')
        if public_host:
            request.META['HTTP_HOST'] = public_host

# Chemins dont les POST ne sont PAS des actes de gestion.
#
# Le journal d'audit enregistrait indistinctement toute requête POST/PUT/PATCH/
# DELETE réussie. Or l'interface en émet en permanence sans qu'aucune décision
# humaine soit prise : chaque ouverture d'une page de messagerie signe un
# `POST /api/comms/pusher/auth/` (la poignée de main du temps réel), et chaque
# message lu un `POST /api/comms/<id>/read/`.
#
# Résultat : trente-cinq entrées de journal dont trois seulement disaient
# quelque chose, et « Création sur comms (/api/comms/pusher/auth/) » répété à
# l'infini. Un registre d'audit qu'on ne peut pas lire ne protège personne.
#
# On écarte donc ces chemins techniques. Le critère est volontairement étroit —
# une liste explicite plutôt qu'un motif large : mieux vaut journaliser un peu
# trop que de laisser passer, sans s'en apercevoir, une action qui compte.
AUDIT_IGNORED_SUFFIXES = (
    '/pusher/auth/',   # poignée de main du temps réel
    '/read/',          # accusé de lecture d'un message
    '/auth/refresh/',  # renouvellement de jeton
)


class AuditMiddleware(MiddlewareMixin):
    @staticmethod
    def _is_noise(path: str) -> bool:
        return any(path.endswith(suffix) for suffix in AUDIT_IGNORED_SUFFIXES)

    def process_response(self, request, response):
        # We only log successful state-changing requests by authenticated users
        if (
            request.user.is_authenticated
            and request.method in ['POST', 'PUT', 'PATCH', 'DELETE']
            and not self._is_noise(request.path)
        ):
            if 200 <= response.status_code < 300:
                # Basic logging of the request
                try:
                    # Action is the method + path
                    action = f"{request.method} {request.path}"
                    # Try to extract entity from path
                    path_parts = request.path.strip('/').split('/')
                    entity = path_parts[1] if len(path_parts) > 1 else None
                    
                    method_labels = {
                        'POST': 'Création',
                        'PUT': 'Mise à jour (Complète)',
                        'PATCH': 'Modification',
                        'DELETE': 'Suppression'
                    }
                    method_label = method_labels.get(request.method, request.method)
                    description = f"{method_label} sur {entity or 'système'} ({request.path})"
                    
                    AuditLog.objects.create(
                        user=request.user,
                        action=action,
                        entity=entity,
                        description=description,
                        metadata={
                            "status_code": response.status_code,
                            "query_params": dict(request.GET),
                            "method": request.method,
                        }
                    )
                except Exception as e:
                    # Don't break the response if logging fails
                    print(f"Audit logging failed: {e}")
        
        return response
