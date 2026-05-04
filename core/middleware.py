from django.utils.deprecation import MiddlewareMixin
from accounts.models import AuditLog

class AuditMiddleware(MiddlewareMixin):
    def process_response(self, request, response):
        # We only log successful state-changing requests by authenticated users
        if request.user.is_authenticated and request.method in ['POST', 'PUT', 'PATCH', 'DELETE']:
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
