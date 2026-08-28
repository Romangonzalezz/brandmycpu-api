"""
Configuración Django para el proyecto BrandMyCPU.

Toda la configuración sensible sale de variables de entorno vía python-decouple.
PostgreSQL es la única base de datos soportada (no se usa SQLite en producción).
"""
from pathlib import Path

import dj_database_url
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent

# ── Seguridad ──────────────────────────────────────────────────────────────
SECRET_KEY = config('DJANGO_SECRET_KEY', default='django-insecure-cambiar-en-produccion')

DEBUG = config('DEBUG', default=False, cast=bool)

ALLOWED_HOSTS = config(
    'ALLOWED_HOSTS', default='localhost,127.0.0.1,.railway.app'
).split(',')

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
# soluciona el patrón Railway: un único DATABASE_URL. Sin SQLite.
DATABASES = {
    'default': dj_database_url.parse(
        config(
            'DATABASE_URL',
            default='postgres://postgres:postgres@localhost:5432/brandmycpu',
        ),
        conn_max_age=600,
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
MEDIA_ROOT = BASE_DIR / 'media'

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

# ── DataFast ────────────────────────────────────────────────────────────────
# Clave de la Payments API (df_...). Vacía = no se reporta nada.
DATAFAST_API_KEY = config('DATAFAST_API_KEY', default='')

# Objetivo de recaudación en centavos de dólar ($800 = salir de iGPU)
SPOT_GOAL = config('SPOT_GOAL', default=80000, cast=int)