import os

os.environ.setdefault("SECRET_KEY", "dev-only-insecure-key-do-not-use-in-production")

from .base import *  # noqa: F403

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
# 4200 = Angular dev server (easylife-frontend), 3000 kept for other tooling
CORS_ALLOWED_ORIGINS = [
    "http://localhost:4200", "http://127.0.0.1:4200",
    "http://localhost:3000", "http://127.0.0.1:3000",
]
