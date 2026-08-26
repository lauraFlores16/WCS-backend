"""Punto de entrada ASGI (opcional; el proyecto es 100% síncrono, pero algunos
hosts (Render, Railway, etc.) prefieren servir por ASGI)."""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_asgi_application()
