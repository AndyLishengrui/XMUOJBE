# Local PostgreSQL settings for development
import os
from utils.shortcuts import get_env

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'HOST': get_env('POSTGRES_HOST', '127.0.0.1'),
        'PORT': get_env('POSTGRES_PORT', '5435'),
        'NAME': get_env('POSTGRES_DB', 'onlinejudge'),
        'USER': get_env('POSTGRES_USER', 'onlinejudge'),
        'PASSWORD': get_env('POSTGRES_PASSWORD', 'onlinejudge')
    }
}

# Use local memory cache (no Redis needed for local dev)
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'unique-snowflake',
    }
}

SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"
