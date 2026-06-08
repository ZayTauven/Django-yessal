"""
URL configuration for core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.http import JsonResponse
from django.urls import path, include, re_path
from django.conf import settings
from django.views.static import serve as serve_media

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('accounts.urls')),
    path('api/events/', include('events.urls')),
    path('api/contributions/', include('contributions.urls')),
    path('api/comms/', include('comms.urls')),
    path('api/news/', include('news.urls')),
    path('health/', lambda request: JsonResponse({'status': 'ok'})),
]

# Sert les médias uploadés via Django tant qu'on n'utilise pas de stockage objet (S3).
# NB : on n'utilise PAS le helper static() car il renvoie [] quand DEBUG=False.
# La vue serve() fonctionne quel que soit DEBUG. Acceptable pour la démo ;
# sur Render, le disque reste éphémère (uploads perdus au redéploiement) → S3/R2 pour la persistance.
if not settings.USE_S3:
    _media_prefix = settings.MEDIA_URL.lstrip('/')
    urlpatterns += [
        re_path(rf'^{_media_prefix}(?P<path>.*)$', serve_media, {'document_root': settings.MEDIA_ROOT}),
    ]
