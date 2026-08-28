from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve


def serve_media(request, path):
    """Sirve los logos subidos.

    Con DEBUG=False Django no sirve MEDIA y WhiteNoise sólo cubre STATIC, así
    que sin esto todo logo de sponsor devuelve 404.

    Aceptamos SVG, y un SVG servido desde este origen puede ejecutar scripts
    en el mismo dominio donde vive /admin/. La CSP lo deja inerte.
    """
    response = serve(request, path, document_root=settings.MEDIA_ROOT)
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    response['X-Content-Type-Options'] = 'nosniff'
    return response


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('spots.urls')),
    re_path(r'^media/(?P<path>.*)$', serve_media, name='media'),
]
