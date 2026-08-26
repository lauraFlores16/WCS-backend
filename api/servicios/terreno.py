"""
============================================================================
SERVICIO DE TERRENO (Capa 1) — puerto de backend/servicios/terreno.js
============================================================================
"""
from __future__ import annotations

import json
import math
import os
import threading
from pathlib import Path
from urllib.parse import quote

from django.conf import settings

from ..lib.cache import con_cache_tolerante
from ..lib.cola import pedir
from .grid import celda_en, obtener_indice

URL_ELEVACION = "https://api.open-meteo.com/v1/elevation"

# Instancias públicas de Overpass, en orden de preferencia. La principal
# (overpass-api.de) va por delante de un filtro que devuelve 406 con bastante
# alegría; cuando eso pasa, en vez de quedarnos sin Capa 1 se prueba el espejo.
# Todas hablan el mismo protocolo, así que la consulta no cambia.
URLS_OVERPASS = [
    u.strip() for u in os.environ.get(
        "OVERPASS_URLS",
        "https://overpass-api.de/api/interpreter,"
        "https://overpass.kumi.systems/api/interpreter",
    ).split(",") if u.strip()
]
LOTE = 100

_dem: dict[str, float] | None = None
_dem_sucio = False
_dem_lock = threading.Lock()


def _ruta_dem() -> Path:
    return Path(settings.CACHE_DIR) / "dem.json"


def _cargar_dem_disco() -> dict:
    global _dem
    if _dem is not None:
        return _dem
    try:
        _dem = json.loads(_ruta_dem().read_text(encoding="utf-8"))
        print(f"[terreno] DEM en caché: {len(_dem)} celdas")
    except (OSError, ValueError):
        _dem = {}
    return _dem


def _guardar_dem_disco() -> None:
    global _dem_sucio
    if not _dem_sucio:
        return
    try:
        Path(settings.CACHE_DIR).mkdir(parents=True, exist_ok=True)
        _ruta_dem().write_text(json.dumps(_dem), encoding="utf-8")
        _dem_sucio = False
    except OSError as e:
        print(f"[terreno] no se pudo guardar el DEM: {e}")


def obtener_dem(foco_fila: int, foco_columna: int, radio: int = 20) -> dict:
    global _dem_sucio
    indice = obtener_indice()
    with _dem_lock:
        cache = _cargar_dem_disco()

        objetivo = []
        for f in range(foco_fila - radio, foco_fila + radio + 1):
            for c in range(foco_columna - radio, foco_columna + radio + 1):
                celda = indice["por_fila_col"].get(f"{f},{c}")
                if celda:
                    objetivo.append(celda)

        faltantes = [c for c in objetivo if f"{c['fila']}:{c['columna']}" not in cache]
        descargadas = 0

        for i in range(0, len(faltantes), LOTE):
            lote = faltantes[i:i + LOTE]
            lats = ",".join(f"{c['lat']:.5f}" for c in lote)
            lons = ",".join(f"{c['lon']:.5f}" for c in lote)
            try:
                r = pedir(f"{URL_ELEVACION}?latitude={lats}&longitude={lons}", etiqueta="Open-Meteo (elevación)")
                json_ = r.json()
                if json_.get("error"):
                    raise RuntimeError(json_.get("reason"))
                for k, h in enumerate(json_.get("elevation", [])):
                    if h is not None and math.isfinite(h):
                        cache[f"{lote[k]['fila']}:{lote[k]['columna']}"] = round(h)
                        descargadas += 1
                _dem_sucio = True
            except Exception as e:  # noqa: BLE001
                print(f"[terreno] elevación no disponible ({e}); se omiten los lotes restantes")
                break

        _guardar_dem_disco()

        alturas = {}
        valores = []
        for c in objetivo:
            h = cache.get(f"{c['fila']}:{c['columna']}")
            if h is not None:
                alturas[c["id"]] = h
                valores.append(h)

        estadisticas = None
        if valores:
            estadisticas = {
                "min": min(valores), "max": max(valores),
                "media": round(sum(valores) / len(valores)),
                "celdas": len(valores), "desnivel": round(max(valores) - min(valores)),
            }

        return {"alturas": alturas, "estadisticas": estadisticas, "descargadas": descargadas,
                "en_cache": len(valores) - descargadas}


def _consulta_overpass(bbox: dict) -> str:
    b = f"{bbox['sur']},{bbox['oeste']},{bbox['norte']},{bbox['este']}"
    return f"""
[out:json][timeout:180];
(
  way["waterway"~"^(river|canal)$"]({b});
  way["waterway"="stream"]({b});
  way["natural"="water"]({b});
  way["landuse"="reservoir"]({b});
  way["natural"~"^(bare_rock|sand|scree)$"]({b});
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary)$"]({b});
);
out geom;"""


def _clasificar(tags: dict) -> str | None:
    if tags.get("waterway") in ("river", "canal"):
        return "rio"
    if tags.get("waterway") == "stream":
        return "quebrada"
    if tags.get("natural") == "water" or tags.get("landuse") == "reservoir":
        return "agua"
    if tags.get("natural") in ("bare_rock", "sand", "scree"):
        return "desnudo"
    if tags.get("highway"):
        return "camino"
    return None


RESISTENCIA = {"rio": 0.0, "agua": 0.0, "quebrada": 0.45, "camino": 0.6, "desnudo": 0.1}


def obtener_terreno_osm() -> dict:
    indice = obtener_indice()
    b = indice["bbox"]
    clave = f"osm:{b['sur']:.3f},{b['oeste']:.3f},{b['norte']:.3f},{b['este']:.3f}"

    def producir():
        # El cuerpo va como application/x-www-form-urlencoded, así que la
        # consulta se escapa: lleva [ ] " ~ ^ $ ( ) ; = y saltos de línea. El
        # original en Node usaba encodeURIComponent y al portarlo se me pasó.
        cuerpo = "data=" + quote(_consulta_overpass(b), safe="")
        cabeceras = {
            "Content-Type": "application/x-www-form-urlencoded",
            # Overpass hace negociación de contenido y rechaza con 406 lo que
            # no le encaja; pedimos JSON explícitamente, que es lo que declara
            # la propia consulta con [out:json].
            "Accept": "application/json",
        }

        ultimo_error = None
        for i, url in enumerate(URLS_OVERPASS):
            etiqueta = "Overpass / OpenStreetMap"
            if i > 0:
                etiqueta += f" (espejo {i})"
            try:
                r = pedir(url, metodo="POST", headers=cabeceras, data=cuerpo,
                          etiqueta=etiqueta, timeout_s=190)
                if i > 0:
                    print(f"[terreno] Capa 1 resuelta por el espejo: {url}")
                break
            except Exception as e:  # noqa: BLE001
                ultimo_error = e
                print(f"[terreno] {etiqueta} no sirvió ({e}); se prueba la siguiente instancia")
        else:
            raise ultimo_error

        json_ = r.json()
        salida = []
        for e in json_.get("elements", []):
            geom = e.get("geometry")
            if not geom:
                continue
            tipo = _clasificar(e.get("tags") or {})
            if not tipo:
                continue
            salida.append({"t": tipo, "g": [[round(p["lat"], 5), round(p["lon"], 5)] for p in geom]})
        return salida

    r = con_cache_tolerante(clave, settings.TTL_SEGUNDOS["osm"], producir)

    rasterizado = _rasterizar(r.valor)
    rasterizado["procedencia"] = {
        "origen": r.origen,
        "edad_minutos": round(r.edad_ms / 60000),
        "reciente": r.origen != "cache-caducada",
        "aviso": str(r.error) if r.error else None,
    }
    return rasterizado


def _rasterizar(elementos: list[dict]) -> dict:
    indice = obtener_indice()
    resistencia: dict[str, float] = {}
    clases: dict[str, str] = {}
    conteo: dict[str, int] = {}

    def marcar(lat, lon, tipo):
        celda = celda_en(lat, lon)
        if not celda:
            return
        r = RESISTENCIA.get(tipo, 1)
        prev = resistencia.get(celda["id"])
        if prev is None or r < prev:
            resistencia[celda["id"]] = r
            clases[celda["id"]] = tipo

    for el in elementos:
        conteo[el["t"]] = conteo.get(el["t"], 0) + 1
        g = el["g"]
        for i, (lat, lon) in enumerate(g):
            marcar(lat, lon, el["t"])
            if i < len(g) - 1:
                la, lo = g[i]
                lb, lob = g[i + 1]
                pasos = math.ceil(max(abs(lb - la) / indice["paso_lat"], abs(lob - lo) / indice["paso_lon"]) * 2)
                for k in range(1, min(pasos, 400)):
                    marcar(la + (lb - la) * k / pasos, lo + (lob - lo) * k / pasos, el["t"])

    barreras = [id_ for id_, r in resistencia.items() if r <= 0.05]

    return {
        "resistencia": resistencia, "clases": clases, "barreras": barreras,
        "resumen": {
            "celdas_afectadas": len(resistencia),
            "barreras_hidricas": len(barreras),
            "rios": conteo.get("rio", 0),
            "quebradas": conteo.get("quebrada", 0),
            "cuerpos_agua": conteo.get("agua", 0),
            "caminos": conteo.get("camino", 0),
            "roca_desnuda": conteo.get("desnudo", 0),
        },
    }
