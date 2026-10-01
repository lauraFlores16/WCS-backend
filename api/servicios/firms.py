"""NASA FIRMS y focos históricos. DOS fuentes separadas, nunca intercambiables.

`obtener_focos_activos` devuelve exclusivamente lo que responde NASA FIRMS.
Si no hay focos, falta la clave o el servicio falla, devuelve lista vacía con
un estado que lo explica. Nunca sustituye por históricos: un foco de 2019
presentado como activo lleva a decidir sobre un incendio que no existe.

`obtener_focos_historicos` es la otra fuente, con su endpoint y su capa.
"""
from __future__ import annotations

from django.conf import settings

from ..lib.cache import con_cache_tolerante
from ..lib.cola import pedir
from ..lib.csv_utils import leer_csv
from . import grid as grid_srv

BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

SIN_CLAVE, SIN_FOCOS, ERROR, CORRECTO = "sin_clave", "sin_focos", "error", "correcto"

_poligonos: dict[str, object] = {}


def _poligono(zona: str):
    """Límite municipal preparado para punto-en-polígono. None si no carga."""
    if zona in _poligonos:
        return _poligonos[zona]
    try:
        import json
        from pathlib import Path
        from shapely.geometry import shape
        from shapely.prepared import prep
        raiz = Path(__file__).resolve().parents[3]
        # Repos separados: prototipo/WCS-backend + prototipo/WCS-frontend
        d = next((c for c in (raiz / "WCS-frontend" / "public" / "datos",
                              raiz / "frontend" / "public" / "datos") if c.exists()),
                 raiz / "WCS-frontend" / "public" / "datos")
        r = {"apolo": d / "apolo_limite.geojson",
             "rurrenabaque": d / "rurrenabaque_limite.geojson"}.get(zona)
        _poligonos[zona] = prep(shape(
            (lambda fc: fc["features"][0]["geometry"] if fc.get("features") else fc)(
                json.loads(r.read_text(encoding="utf-8"))))) if r and r.exists() else None
    except Exception as e:  # noqa: BLE001
        print(f"[firms] sin polígono de {zona}: {type(e).__name__}: {e}")
        _poligonos[zona] = None
    return _poligonos[zona]


def _dentro(focos: list[dict], zona: str) -> tuple[list[dict], int]:
    """Recorta al polígono. El bbox no basta: un rectángulo sobre un municipio
    irregular deja dentro superficie de municipios vecinos."""
    poli = _poligono(zona)
    if poli is None:
        return focos, 0
    from shapely.geometry import Point
    dentro, fuera = [], 0
    for f in focos:
        try:
            if poli.contains(Point(float(f["lon"]), float(f["lat"]))):
                dentro.append(f)
            else:
                fuera += 1
        except (TypeError, ValueError, KeyError):
            fuera += 1
    return dentro, fuera


def clave_configurada() -> bool:
    return bool(settings.FIRMS["clave"].strip())


def obtener_focos_historicos(zona: str = "apolo", limite: int = 2000,
                             desde: str | None = None,
                             hasta: str | None = None) -> dict:
    """Focos históricos del proyecto. Fuente distinta de NASA FIRMS."""
    todos = grid_srv.obtener_focos() or []
    filtrados = []
    for f in todos:
        fecha = str(f.get("fecha") or "")
        if (desde and fecha < desde) or (hasta and fecha > hasta):
            continue
        filtrados.append(f)

    dentro, fuera = _dentro(filtrados, zona)
    ordenados = sorted(dentro, key=lambda f: str(f.get("fecha", "")), reverse=True)
    salida = [{
        "id": f"hist-{f.get('id', i)}",
        "lat": f.get("lat"), "lon": f.get("lon"),
        "fecha": str(f["fecha"]) if f.get("fecha") is not None else "",
        "hora": "", "brillo": None, "frp": None,
        "confianza": str(f["confianza"]) if f.get("confianza") is not None else None,
        "satelite": "Base histórica SIPRO", "historico": True,
    } for i, f in enumerate(ordenados[:limite])]

    fechas = sorted({f["fecha"] for f in salida if f["fecha"]})
    return {
        "fuente": "Base histórica SIPRO", "tipo": "focos históricos", "zona": zona,
        "focos": salida, "total": len(salida), "disponibles": len(dentro),
        "descartados_fuera": fuera, "historico": True,
        "periodo": f"{fechas[0]} a {fechas[-1]}" if fechas else "sin registros",
        "mensaje": (f"{len(salida)} foco(s) histórico(s) de la base del proyecto."
                    if salida else "No hay focos históricos para esta zona."),
        "_aviso": "Registros históricos. NO son detecciones actuales.",
    }


def obtener_focos_activos(zona: str = "apolo") -> dict:
    firms = settings.FIRMS
    base = {"fuente": "NASA FIRMS", "zona": zona, "sensor": firms["fuente"],
            "dias": firms["dias"], "periodo": f"últimos {firms['dias']} días",
            "focos": [], "activos": 0, "historico": False}

    if not clave_configurada():
        return {**base, "estado": SIN_CLAVE, "configurada": False,
                "mensaje": "NASA FIRMS no configurado.",
                "detalle": "Falta NASA_FIRMS_MAP_KEY en backend_django/.env. "
                           "Se pide gratis en firms.modaps.eosdis.nasa.gov/api/map_key/"}

    clave = f"firms:{zona}:{firms['fuente']}:{firms['dias']}:{firms['bbox']}"

    def producir():
        url = f"{BASE}/{firms['clave']}/{firms['fuente']}/{firms['bbox']}/{firms['dias']}"
        r = pedir(url, etiqueta="NASA FIRMS")
        texto = r.text
        if texto.startswith("Invalid") or "Invalid MAP_KEY" in texto:
            raise RuntimeError(f"NASA FIRMS: {texto.strip()[:200]}")
        salida = []
        for i, f in enumerate(leer_csv(texto)):
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
                # Se conservan tal como los manda NASA, para poder cotejarlos
                # uno a uno contra el visor oficial.
                "instrumento": str(f["instrument"]) if f.get("instrument") else None,
                "version": str(f["version"]) if f.get("version") else None,
                "brillo_ti5": f.get("bright_ti5"),
                "dia_noche": str(f["daynight"]) if f.get("daynight") else None,
                "historico": False,
            })
        return salida

    try:
        r = con_cache_tolerante(clave, settings.TTL_SEGUNDOS["firms"], producir)
    except Exception as e:  # noqa: BLE001
        return {**base, "estado": ERROR, "configurada": True,
                "mensaje": "NASA FIRMS no está disponible.",
                "detalle": str(e)[:220],
                "procedencia": {"origen": "error", "reciente": False}}

    crudos = r.valor or []
    focos, fuera = _dentro(crudos, zona)
    proc = {"origen": r.origen, "edad_minutos": round(r.edad_ms / 60000),
            "reciente": r.origen != "cache-caducada",
            "aviso": str(r.error) if r.error else None}

    if not focos:
        return {**base, "estado": SIN_FOCOS, "configurada": True,
                "mensaje": "Sin focos activos para el período consultado.",
                "detalle": (f"NASA FIRMS devolvió {len(crudos)} detección(es) en el "
                            f"recuadro, ninguna dentro del municipio."
                            if crudos else "NASA FIRMS no devolvió detecciones."),
                "recibidos_bbox": len(crudos), "descartados_fuera": fuera,
                "procedencia": proc}

    return {**base, "estado": CORRECTO, "configurada": True,
            "focos": focos, "activos": len(focos),
            "recibidos_bbox": len(crudos), "descartados_fuera": fuera,
            "url_consultada": f"{BASE}/<clave>/{firms['fuente']}/{firms['bbox']}/{firms['dias']}",
            "mensaje": f"{len(focos)} foco(s) activo(s) en el período consultado.",
            "procedencia": proc}
