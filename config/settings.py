"""
Configuración Django para el proyecto BrandMyCPU.

Toda la configuración sensible sale de variables de entorno vía python-decouple.
PostgreSQL es la única base de datos soportada (no se usa SQLite en producción).
"""
from pathlib import Path

import dj_database_url
from decouple import config
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# ── Seguridad ──────────────────────────────────────────────────────────────
INSECURE_SECRET = 'django-insecure-cambiar-en-produccion'
SECRET_KEY = config('DJANGO_SECRET_KEY', default=INSECURE_SECRET)

DEBUG = config('DEBUG', default=False, cast=bool)

if not DEBUG and SECRET_KEY == INSECURE_SECRET:
    raise ImproperlyConfigured(
        'DJANGO_SECRET_KEY sigue en el valor de ejemplo. Generá una y seteala '
        'en el servicio antes de desplegar.'
    )

ALLOWED_HOSTS = config(
    'ALLOWED_HOSTS', default='localhost,127.0.0.1,.railway.app'
).split(',')

# El TLS lo termina el proxy de Railway y a Django le llega HTTP plano. Sin
# esto `request.build_absolute_uri()` arma los logo_url de los sponsors en
# http://, y el frontend vive en https: contenido mixto que el navegador
# bloquea o tiene que actualizar por su cuenta, un salto extra por logo.
#
# Sólo es seguro porque el contenedor no es alcanzable salvo por ese proxy,
# que reescribe la cabecera. Expuesto directo, cualquiera falsearía el header
# y Django creería que una request en claro llegó por https.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ── Aplicaciones ───────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Terceros
    'rest_framework',
    'corsheaders',

    # Propias
    'spots',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# ── Base de datos: PostgreSQL ───────────────────────────────────────────────
# Un único DATABASE_URL, el patrón de Railway. Sin SQLite.
#
# Sin la variable NO caemos a localhost en producción: eso hace que un deploy
# mal configurado muera con un traceback de psycopg contra 127.0.0.1 en vez de
# decir qué falta.
DATABASE_URL = config('DATABASE_URL', default='')
if not DATABASE_URL:
    if not DEBUG:
        raise ImproperlyConfigured(
            'Falta DATABASE_URL. En Railway: agregá un servicio Postgres al '
            'proyecto y en las Variables de este servicio creá '
            'DATABASE_URL = ${{Postgres.DATABASE_URL}} (referencia, no el '
            'valor copiado, así rota sola si Railway cambia la credencial).'
        )
    DATABASE_URL = 'postgres://postgres:postgres@localhost:5432/brandmycpu'

DATABASES = {
    'default': dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=600,
        # La red privada de Railway no usa SSL. Poné DB_SSL=True sólo si
        # conectás por la URL pública.
        ssl_require=config('DB_SSL', default=False, cast=bool),
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# ── Archivos estáticos y media (logos) ──────────────────────────────────────
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'

# El disco de un contenedor es efímero: sin volumen, cada deploy borra el logo
# de todos los sponsors que pagaron. Railway inyecta RAILWAY_VOLUME_MOUNT_PATH
# cuando hay uno montado, así que basta con montarlo para que esto lo tome.
#
# `manage.py seed_media` copia ahí lo que venga versionado en el repo: un
# volumen se monta vacío y tapa el directorio que traía la imagen.
MEDIA_ROOT = Path(
    config('MEDIA_ROOT', default='')
    or config('RAILWAY_VOLUME_MOUNT_PATH', default='')
    or BASE_DIR / 'media'
)

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── CORS (django-cors-headers) ──────────────────────────────────────────────
CORS_ALLOWED_ORIGINS = config(
    'CORS_ORIGINS',
    default='http://localhost:5173,http://localhost:8080',
).split(',')
CORS_ALLOW_CREDENTIALS = False

# ── Django REST Framework ───────────────────────────────────────────────────
REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': ('rest_framework.renderers.JSONRenderer',),
    'DEFAULT_PARSER_CLASSES': (
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.MultiPartParser',
        'rest_framework.parsers.FormParser',
    ),
}

# ── Logging ─────────────────────────────────────────────────────────────────
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'json': {'format': '%(asctime)s %(levelname)s %(name)s %(message)s'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'json'},
    },
    'root': {'handlers': ['console'], 'level': 'INFO'},
}

# ── DodoPayments ────────────────────────────────────────────────────────────
DODO_API_KEY = config('DODO_API_KEY', default='')
# Id del producto pay-what-you-want en el dashboard de Dodo
DODO_PRODUCT_ID = config(
    'DODO_PRODUCT_ID', default='pdt_0NmNXr1VG164viZhNZCXL'
)
# Secreto del webhook (formato whsec_<base64>)
DODO_WEBHOOK_SECRET = config('DODO_WEBHOOK_SECRET', default='')
# 'test' | 'live'
DODO_SERVER = config('DODO_SERVER', default='test')
# URL a la que Dodo redirige a quien paga (poseída por el frontend)
DODO_RETURN_URL = config('DODO_RETURN_URL', default='http://localhost:5173')

# ── Modo demo (grabar video, probar el flujo sin cobrar) ────────────────────
# Salta DodoPayments y da el spot por pagado. Confirma spots sin plata de por
# medio, así que queda encerrado detrás de DEBUG: en un server real esto sería
# regalar lugares en el vidrio.
FAKE_PAYMENTS = config('FAKE_PAYMENTS', default=False, cast=bool)
if FAKE_PAYMENTS and not DEBUG:
    raise ImproperlyConfigured(
        'FAKE_PAYMENTS confirma spots sin cobrar. Sólo se permite con '
        'DEBUG=True, nunca en un entorno desplegado.'
    )

# True = da por bueno cualquier post de X sin verificarlo. Para ver el flujo del
# giveaway en local, donde no hay un post real que citar.
#
# Misma guarda dura que FAKE_PAYMENTS, y por una razón más fuerte: verificar el
# post es lo único que separa siete lugares gratis de siete bots. Desplegar esto
# encendido regala el vidrio entero.
FAKE_TWEETS = config('FAKE_TWEETS', default=False, cast=bool)
if FAKE_TWEETS and not DEBUG:
    raise ImproperlyConfigured(
        'FAKE_TWEETS reparte lugares gratis sin comprobar el post. Sólo se '
        'permite con DEBUG=True, nunca en un entorno desplegado.'
    )

# ── DataFast ────────────────────────────────────────────────────────────────
# Clave de la Payments API (df_...). Vacía = no se reporta nada.
DATAFAST_API_KEY = config('DATAFAST_API_KEY', default='')

# Objetivo de recaudación en centavos de dólar ($800 = salir de iGPU)
SPOT_GOAL = config('SPOT_GOAL', default=80000, cast=int)

# Sal para el hash de IP de los clicks. Sin ella el hash de una IPv4 se
# revierte por fuerza bruta sobre 4 mil millones de direcciones, o sea que no
# es un hash.
IP_HASH_SALT = config('IP_HASH_SALT', default=SECRET_KEY)

# Cuántos lugares se regalan, en total. Cero por defecto a propósito: una
# campaña que terminó se apaga bajando esto, y un deploy sin configurar no
# empieza a repartir vidrio gratis por su cuenta.
GIVEAWAY_SEATS = config('GIVEAWAY_SEATS', default=0, cast=int)

# El handle que el post tiene que mencionar para pagar un lugar gratis. Sin
# arroba. Una mención SÍ vuelve en la respuesta de oEmbed, a diferencia de un
# enlace, que X reescribe a t.co y deja de ser comprobable.
X_HANDLE = config('X_HANDLE', default='romg_dev')