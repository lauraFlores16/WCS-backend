"""
============================================================================
NASA FIRMS — focos activos (Capa 3) — puerto de backend/servicios/firms.js
============================================================================
"""
from __future__ import annotations

from django.conf import settings

from ..lib.cache import con_cache_tolerante
from ..lib.cola import pedir
from ..lib.csv_utils import leer_csv
from . import grid as grid_srv

BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"


def clave_configurada() -> bool:
    return bool(settings.FIRMS["clave"].strip())


def _focos_historicos_de_respaldo(limite: int = 200) -> list[dict]:
    todos = grid_srv.obtener_focos() or []
    if not todos:
        return []
    ordenados = sorted(todos, key=lambda f: str(f.get("fecha", "")), reverse=True)
    salida = []
    for i, f in enumerate(ordenados[:limite]):
        salida.append({
            "id": f"hist-{f.get('id', i)}",
            "lat": f.get("lat"), "lon": f.get("lon"),
            "fecha": str(f["fecha"]) if f.get("fecha") is not None else "",
            "hora": "", "brillo": None, "frp": None,
            "confianza": str(f["confianza"]) if f.get("confianza") is not None else None,
            "satelite": "Histórico (focos.csv)", "historico": True,
        })
    return salida


def obtener_focos_activos() -> dict:
    firms = settings.FIRMS
    if not clave_configurada():
        return {
            "focos": _focos_historicos_de_respaldo(),
            "configurada": False,
            "respaldo": "historico",
            "mensaje": "Sin MAP_KEY: se muestran focos históricos reales (focos.csv). "
                       "Añade NASA_FIRMS_MAP_KEY en backend_django/.env para ver los activos.",
            "procedencia": {"origen": "historico", "reciente": False},
        }

    clave = f"firms:{firms['fuente']}:{firms['dias']}:{firms['bbox']}"

    def producir():
        url = f"{BASE}/{firms['clave']}/{firms['fuente']}/{firms['bbox']}/{firms['dias']}"
        r = pedir(url, etiqueta="NASA FIRMS")
        texto = r.text
        if texto.startswith("Invalid") or "Invalid MAP_KEY" in texto:
            raise RuntimeError(f"NASA FIRMS: {texto.strip()[:200]}")
        filas = leer_csv(texto)
        salida = []
        for i, f in enumerate(filas):
            if f.get("latitude") is None or f.get("longitude") is None:
                continue
            salida.append({
                "id": f"firms-{i}",
                "lat": f["latitude"], "lon": f["longitude"],
                "fecha": str(f["acq_date"]) if f.get("acq_date") is not None else "",
                "hora": str(f["acq_time"]).zfill(4) if f.get("acq_time") is not None else "",
                "brillo": f.get("bright_ti4", f.get("brightness")),
                "frp": f.get("frp"),
                "confianza": str(f["confidence"]) if f.get("confidence") is not None else None,
                "satelite": str(f["satellite"]) if f.get("satellite") else firms["fuente"],
            })
        return salida

    r = con_cache_tolerante(clave, settings.TTL_SEGUNDOS["firms"], producir)

    if not r.valor:
        return {
            "focos": _focos_historicos_de_respaldo(),
            "configurada": True, "activos": 0, "respaldo": "historico",
            "mensaje": "Sin focos activos ahora mismo. Se muestran focos históricos reales del municipio.",
            "procedencia": {"origen": "historico", "reciente": False},
        }

    return {
        "focos": r.valor, "configurada": True, "activos": len(r.valor),
        "fuente": firms["fuente"], "dias": firms["dias"],
        "procedencia": {
            "origen": r.origen,
            "edad_minutos": round(r.edad_ms / 60000),
            "reciente": r.origen != "cache-caducada",
            "aviso": str(r.error) if r.error else None,
        },
    }
