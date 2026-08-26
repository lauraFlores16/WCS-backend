"""Utilidades comunes de las vistas — puerto de las cabeceras de rutas/index.js.

Convención de respuestas (idéntica al backend Node que se reemplaza, para que
el frontend no tuviera que cambiar de contrato):

    { "ok": true,  "datos": ..., "procedencia": {...} }
    { "ok": false, "error": "mensaje para el usuario", "servicio": "Open-Meteo" }
"""
from __future__ import annotations

import json
from typing import Any

from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpRequest, JsonResponse


class SiproJSONEncoder(DjangoJSONEncoder):
    """Además de fechas/UUID/Decimal (que ya maneja Django), tolera escalares
    y arreglos de numpy que puedan colarse desde el motor del autómata."""

    def default(self, o: Any):
        try:
            import numpy as np
            if isinstance(o, np.integer):
                return int(o)
            if isinstance(o, np.floating):
                return float(o)
            if isinstance(o, np.ndarray):
                return o.tolist()
            if isinstance(o, (set, frozenset)):
                return list(o)
        except ImportError:
            pass
        return super().default(o)


def bien(datos: Any, **extra) -> JsonResponse:
    cuerpo = {"ok": True, "datos": datos, **extra}
    return JsonResponse(cuerpo, safe=False, encoder=SiproJSONEncoder)


def mal(error: Exception, estado_por_defecto: int = 500) -> JsonResponse:
    estado = getattr(error, "estado_http", estado_por_defecto)
    cuerpo = {
        "ok": False,
        "error": str(error) or "Error inesperado",
        "servicio": getattr(error, "servicio", None),
        "reintentar_en_s": getattr(error, "reintentar_en_s", None),
    }
    return JsonResponse(cuerpo, status=estado, encoder=SiproJSONEncoder)


def error_simple(mensaje: str, estado: int = 400) -> JsonResponse:
    return JsonResponse({"ok": False, "error": mensaje}, status=estado)


def cuerpo_json(request: HttpRequest) -> dict:
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
