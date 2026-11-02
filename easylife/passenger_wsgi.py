"""Passenger entry point for cPanel Python applications."""

import os
import sys


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "easylife.settings.prod")

from easylife.wsgi import application  # noqa: E402
