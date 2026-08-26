"""Puerto de backend/motor/alertas_riesgo.js — alertas de riesgo sin necesidad
de simular (probabilidad XGBoost del grid + peligro meteorológico actual)."""
from __future__ import annotations

from datetime import datetime, timezone

from django.conf import settings

from ..servicios.grid import obtener_grid
from ..servicios.meteo import indice_peligro, obtener_meteorologia

PROB_ALTA = 0.75
PROB_MUY_ALTA = 0.90


def calcular_alertas_riesgo() -> list[dict]:
    grid = obtener_grid()
    if not grid:
        return []

    altas = muy_altas = 0
    celda_tope = None
    for c in grid:
        p = c.get("prob_ignicion")
        if p is None:
            continue
        if p >= PROB_ALTA:
            altas += 1
        if p >= PROB_MUY_ALTA:
            muy_altas += 1
        if celda_tope is None or p > celda_tope["prob_ignicion"]:
            celda_tope = c

    peligro = None
    try:
        meteo = obtener_meteorologia(settings.APOLO["lat"], settings.APOLO["lon"])
        peligro = indice_peligro(meteo)
    except Exception:  # noqa: BLE001
        pass

    alertas = []
    ahora = datetime.now(timezone.utc).isoformat()
    puntaje = peligro["puntaje"] if peligro else 0

    if muy_altas > 0 and puntaje >= 0.66:
        alertas.append({
            "origen": "riesgo", "nivel": "roja",
            "mensaje": f"Riesgo extremo: {muy_altas} celdas con probabilidad ≥ {PROB_MUY_ALTA} y peligro "
                       f"meteorológico ALTO ({puntaje * 100:.0f}%).",
            "lat": celda_tope["lat"] if celda_tope else None,
            "lon": celda_tope["lon"] if celda_tope else None, "creada_en": ahora,
        })
    elif altas > 0 and puntaje >= 0.4:
        alertas.append({
            "origen": "riesgo", "nivel": "naranja",
            "mensaje": f"Riesgo elevado: {altas} celdas de alta probabilidad con peligro meteorológico "
                       f"MEDIO ({puntaje * 100:.0f}%).",
            "lat": celda_tope["lat"] if celda_tope else None,
            "lon": celda_tope["lon"] if celda_tope else None, "creada_en": ahora,
        })
    elif altas > 0:
        alertas.append({
            "origen": "riesgo", "nivel": "amarilla",
            "mensaje": f"{altas} celdas con probabilidad de ignición ≥ {PROB_ALTA} en el municipio.",
            "lat": celda_tope["lat"] if celda_tope else None,
            "lon": celda_tope["lon"] if celda_tope else None, "creada_en": ahora,
        })

    if peligro and puntaje >= 0.66 and not any(a["nivel"] == "roja" for a in alertas):
        reglas = peligro["regla_303030"]
        alertas.append({
            "origen": "riesgo", "nivel": "naranja",
            "mensaje": f"Condiciones de incendio: {reglas}/3 de la regla 30-30-30 cumplidas "
                       f"(temperatura, humedad y viento).",
            "lat": settings.APOLO["lat"], "lon": settings.APOLO["lon"], "creada_en": ahora,
        })

    return alertas
