"""Puerto de backend/motor/temporada.js — ajuste opcional por época seca/lluvias."""
from __future__ import annotations

from datetime import datetime

MESES_SECOS = {4, 5, 6, 7, 8, 9}  # mayo-octubre (0=enero en JS; aquí usamos month-1)

AJUSTES = {
    "seca": {
        "delta_humedad": -0.12, "factor_p_base": 1.25,
        "etiqueta": "Época seca",
        "nota": "Vegetación seca y baja humedad: mayor probabilidad y propagación.",
    },
    "lluvias": {
        "delta_humedad": 0.12, "factor_p_base": 0.7,
        "etiqueta": "Época de lluvias",
        "nota": "Combustible húmedo: menor probabilidad y propagación más lenta.",
    },
}


def temporada_por_fecha(fecha: datetime | None = None) -> str:
    fecha = fecha or datetime.now()
    return "seca" if (fecha.month - 1) in MESES_SECOS else "lluvias"


def resolver_temporada(opciones: dict | None = None) -> dict:
    opciones = opciones or {}
    incluir = opciones.get("incluir", False)
    temporada = opciones.get("temporada", "auto")
    fecha = opciones.get("fecha") or datetime.now()

    if not incluir:
        return {"incluida": False, "temporada": None, "delta_humedad": 0, "factor_p_base": 1,
                "etiqueta": "Sin estacionalidad",
                "nota": "La simulación usa solo la meteorología del momento."}

    efectiva = temporada_por_fecha(fecha) if temporada == "auto" else temporada
    a = AJUSTES.get(efectiva, AJUSTES["seca"])
    return {
        "incluida": True, "temporada": efectiva, "porFecha": temporada == "auto",
        "delta_humedad": a["delta_humedad"], "factor_p_base": a["factor_p_base"],
        "etiqueta": a["etiqueta"], "nota": a["nota"],
    }
