"""Puerto de backend/motor/alertas.js — reglas de alerta por simulación.

  AMARILLA: > 50 celdas ardiendo en una iteración
  NARANJA:  > 20% de crecimiento entre iteraciones consecutivas
  ROJA:     celda con probabilidad XGBoost > 0.85 comprometida (ardiendo)
"""
from __future__ import annotations

from datetime import datetime, timezone

UMBRAL_AMARILLA_CELDAS = 50
UMBRAL_NARANJA_CRECIMIENTO = 0.20
UMBRAL_ROJA_PROBABILIDAD = 0.85


def evaluar_alertas(escenario_id: str, iter_actual: dict, iter_anterior: dict | None,
                     prob_por_celda: dict | None) -> list[dict]:
    alertas = []
    ahora = datetime.now(timezone.utc).isoformat()

    if iter_actual["num_celdas_ardiendo"] > UMBRAL_AMARILLA_CELDAS:
        alertas.append({
            "escenario_id": escenario_id, "nivel": "amarilla",
            "mensaje": f"{iter_actual['num_celdas_ardiendo']} celdas ardiendo en la iteración "
                       f"{iter_actual['iteracion']} (umbral: {UMBRAL_AMARILLA_CELDAS})",
            "iteracion": iter_actual["iteracion"], "creado_en": ahora,
        })

    if iter_anterior and iter_anterior["num_celdas_ardiendo"] > 0:
        crecimiento = ((iter_actual["num_celdas_ardiendo"] - iter_anterior["num_celdas_ardiendo"])
                       / iter_anterior["num_celdas_ardiendo"])
        if crecimiento > UMBRAL_NARANJA_CRECIMIENTO:
            alertas.append({
                "escenario_id": escenario_id, "nivel": "naranja",
                "mensaje": f"Crecimiento de {crecimiento * 100:.0f}% respecto a la iteración anterior "
                           f"(umbral: {UMBRAL_NARANJA_CRECIMIENTO * 100:.0f}%)",
                "iteracion": iter_actual["iteracion"], "creado_en": ahora,
            })

    if prob_por_celda:
        for c in iter_actual["celdas"]:
            if c["estado"] != "ardiendo":
                continue
            p = prob_por_celda.get(c["celda_id"])
            if p is not None and p > UMBRAL_ROJA_PROBABILIDAD:
                alertas.append({
                    "escenario_id": escenario_id, "nivel": "roja",
                    "mensaje": f"Celda {str(c['celda_id'])[:8]} comprometida con probabilidad {p:.2f} "
                               f"(umbral: {UMBRAL_ROJA_PROBABILIDAD})",
                    "iteracion": iter_actual["iteracion"], "celda_id": c["celda_id"],
                    # Coordenadas de la celda comprometida: son lo que permite
                    # saltar al punto desde el panel de alertas del frontend.
                    # Sin ellas la alerta es un texto que no lleva a ninguna parte.
                    "lat": c.get("lat"), "lon": c.get("lon"),
                    "creado_en": ahora,
                })

    return alertas
