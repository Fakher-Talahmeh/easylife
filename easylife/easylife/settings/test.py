import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-use-only-in-tests")

from .base import *  # noqa: F403

SECRET_KEY = "test-secret-key-not-for-production-use-only-in-tests"
DEBUG = False
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
# Throttling is exercised in dedicated tests; keep other tests deterministic.
REST_FRAMEWORK = {  # noqa: F405
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_CLASSES": [],
}
WHATSAPP_BACKEND = "locmem"
