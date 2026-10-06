"""
Paquete de validación de un evento, construido en el servidor (lo que hacía
p2_preparar_evento.py). Mismas reglas que Rurrenabaque:

  ventana    detecciones del evento ± 8 km, en la rejilla global de 0,0045°
  grid       SRTM (pendiente), Sentinel-2 (NDVI), ERA5-Land (humedad, viento):
             las funciones de f8_construir_grid.py, sin cambios
  observado  fracción quemada de MapBiomas ≥ 0,5 dentro de la huella FIRMS ± 2
             celdas; quemas de otros incendios del año → valido = 0 (regla f9d)
  focos      detecciones de las primeras 6 h del evento
  insumos    clima ERA5 horario y terreno OSM, grabados para reproducir sin red
"""
from __future__ import annotations

import math
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .comun import (AREA_CELDA_KM2, M_LAT, PASO, RBQ_PYTHON, csv_texto, gz_json, gz_texto,
                    json_gz, leer_csv_texto, m_lon, slug, texto_gz)

MINUTOS_POR_ITERACION = 15
MARGEN_KM = 8.0
MARGEN_CELDAS = 2
UMBRAL_OBSERVADO = 0.5
HORAS_INICIALES = 6.0
MAX_CELDAS = 12_000          # tope de seguridad para el plan gratis de Render
COLS_GRID = ["id", "fila", "columna", "lat", "lon", "pendiente_grados", "ndvi", "humedad",
             "viento_u", "viento_v", "prob_ignicion", "elevacion_m"]
COLS_OBS = ["id", "fila", "columna", "lat", "lon", "observado", "fraccion_quemada",
            "valido", "motivo", "pixeles_30m"]


class Ventana:
    """Rectángulo alineado a la rejilla global; interfaz que esperan las
    funciones de f8 (coordenada, contiene, id_celda, tam_celda_m, bbox…)."""

    def __init__(self, oeste, sur, este, norte, prefijo):
        self.lat_origen = math.ceil(norte / PASO) * PASO
        self.lon_origen = math.floor(oeste / PASO) * PASO
        self.filas = int(math.ceil((self.lat_origen - sur) / PASO))
        self.columnas = int(math.ceil((este - self.lon_origen) / PASO))
        self.bbox = (self.lon_origen, self.lat_origen - self.filas * PASO,
                     self.lon_origen + self.columnas * PASO, self.lat_origen)
        self.prefijo = prefijo
        self.centroide = ((self.bbox[1] + self.bbox[3]) / 2, (self.bbox[0] + self.bbox[2]) / 2)

    def coordenada(self, f, c):
        return round(self.lat_origen - f * PASO, 6), round(self.lon_origen + c * PASO, 6)

    def celda(self, lat, lon):
        return round((self.lat_origen - lat) / PASO), round((lon - self.lon_origen) / PASO)

    def contiene(self, lat, lon):
        return True

    def id_celda(self, f, c):
        return f"{self.prefijo}-{f:03d}-{c:03d}"

    def tam_celda_m(self):
        return PASO * M_LAT, PASO * m_lon(self.centroide[0])

    def celdas(self) -> list[dict]:
        return [{"id": self.id_celda(f, c), "fila": f, "columna": c,
                 "lat": self.coordenada(f, c)[0], "lon": self.coordenada(f, c)[1]}
                for f in range(self.filas + 1) for c in range(self.columnas + 1)]


def _f8():
    if str(RBQ_PYTHON) not in sys.path:
        sys.path.insert(0, str(RBQ_PYTHON))
    import f8_construir_grid as F8
    return F8


def construir_grid(ventana: Ventana, desde: str, hasta: str, avance) -> tuple[list[dict], dict]:
    F8 = _f8()
    celdas = ventana.celdas()
    fuentes, faltan = {}, []
    tmp = Path(tempfile.gettempdir())
    avance(0.10, f"Grid de {len(celdas):,} celdas · elevación SRTM 30 m…")
    try:
        try:
            F8.traer_elevacion(celdas, "opentopodata", tmp / f"dem_{ventana.prefijo}.json")
            fuentes["elevacion"] = "SRTM 30 m vía OpenTopoData"
        except Exception as e:  # noqa: BLE001
            avance(0.20, f"OpenTopoData falló ({e}); pruebo Copernicus GLO-90 (Open-Meteo)…")
            F8.traer_elevacion(celdas, "openmeteo", tmp / f"dem90_{ventana.prefijo}.json")
            fuentes["elevacion"] = "Copernicus GLO-90 vía Open-Meteo"
        F8.calcular_pendiente(celdas, ventana)
    except Exception as e:  # noqa: BLE001
        faltan.append(f"elevación/pendiente ({e})")
    avance(0.35, "Humedad del suelo y viento (ERA5-Land)…")
    try:
        n = F8.traer_meteo(celdas, ventana, desde, hasta, 0.08)
        if n:
            fuentes["humedad_viento"] = f"ERA5-Land vía Open-Meteo, media {desde} a {hasta}"
        else:
            faltan.append("humedad/viento")
    except Exception as e:  # noqa: BLE001
        faltan.append(f"humedad/viento ({e})")
    avance(0.50, "NDVI (Sentinel-2 anterior al evento)…")
    try:
        info = F8.traer_ndvi(celdas, ventana, desde, 45, 40.0, Z_PASO=PASO)
        if info:
            fuentes["ndvi"] = f"Sentinel-2 L2A {info['fecha']} · {info['nubes_pct']:.1f} % nubes"
        else:
            faltan.append("ndvi")
    except Exception as e:  # noqa: BLE001
        faltan.append(f"ndvi ({e})")
    for c in celdas:
        c["prob_ignicion"] = 0
    return celdas, {"fuentes": fuentes, "faltan": faltan}


def _dilatar(celdas: set, r: int) -> set:
    return {(f + i, c + j) for f, c in celdas for i in range(-r, r + 1) for j in range(-r, r + 1)}


def observado(grid: list[dict], ventana: Ventana, ev_focos: list[dict], otros: list[dict],
              cicatriz) -> tuple[list[dict], dict]:
    lats = [float(c["lat"]) for c in grid]
    lons = [float(c["lon"]) for c in grid]
    fr, npx = cicatriz.fraccion(lats, lons)
    huella = {ventana.celda(f["latitude"], f["longitude"]) for f in ev_focos}
    zona_ev = _dilatar(huella, MARGEN_CELDAS)
    ajenas = _dilatar({ventana.celda(f["latitude"], f["longitude"]) for f in otros}, 1)
    cont = {"observado": 0, "no_quemado": 0, "sin_dato": 0, "otro_incendio": 0, "atribucion_dudosa": 0}
    filas = []
    for c, f_, n_ in zip(grid, fr, npx):
        k = (int(c["fila"]), int(c["columna"]))
        obs, val, motivo = 0, 1, "no_quemado"
        if np.isnan(f_):
            val, motivo = 0, "sin_dato"
        elif k in zona_ev:
            if k in ajenas and f_ >= 0.25:
                val, motivo = 0, "atribucion_dudosa"
            elif f_ >= UMBRAL_OBSERVADO:
                obs, motivo = 1, "observado"
        elif f_ >= 0.25:
            val, motivo = 0, "otro_incendio"
        cont[motivo] += 1
        filas.append({"id": c["id"], "fila": k[0], "columna": k[1], "lat": c["lat"], "lon": c["lon"],
                      "observado": obs, "fraccion_quemada": "" if np.isnan(f_) else round(float(f_), 4),
                      "valido": val, "motivo": motivo, "pixeles_30m": int(n_)})
    meta = {"fuente": f"MapBiomas Fuego ({cicatriz.fuente or 'anual'}) agregado a 500 m",
            "regla": f"observado = fracción quemada ≥ {UMBRAL_OBSERVADO} dentro de la huella FIRMS ± "
                     f"{MARGEN_CELDAS} celdas",
            "celdas_huella_firms": len(huella), "celdas_observadas": cont["observado"],
            "area_observada_km2": cont["observado"] * AREA_CELDA_KM2, "celdas_por_motivo": cont,
            "celdas_validas": sum(1 for x in filas if x["valido"] == 1),
            "_uso": "REFERENCIA. No entra al autómata."}
    return filas, meta


def insumos_externos(grid: list[dict], iniciales: list[dict], evento: dict, n_pasos: int, avance) -> dict:
    from ..servicios import meteo, terreno
    por = {(int(c["fila"]), int(c["columna"])): c for c in grid}
    c0 = por[(iniciales[0]["fila"], iniciales[0]["columna"])]
    lat0, lon0 = float(c0["lat"]), float(c0["lon"])
    serie_amb = serie_vto = None
    barreras, resistencia, avisos = [], {}, []
    avance(0.70, "Clima horario ERA5 del evento…")
    try:
        serie_amb = meteo.obtener_serie_ambiental_historica(lat0, lon0, evento["fecha_inicio"], evento["fecha_fin"])
    except Exception as e:  # noqa: BLE001
        avisos.append(f"Sin clima horario ({e}): sin ciclo día/noche el modelo sobreestima.")
    try:
        serie_vto = meteo.obtener_serie_viento_historica(lat0, lon0, evento["inicio_utc"], evento["fin_utc"])
    except Exception as e:  # noqa: BLE001
        avisos.append(f"Sin viento horario ({e}): se usa el del grid.")
    avance(0.78, "Ríos y caminos de OpenStreetMap…")
    try:
        celdas = [{**c, "lat": float(c["lat"]), "lon": float(c["lon"]),
                   "fila": int(c["fila"]), "columna": int(c["columna"])} for c in grid]
        bbox = {"sur": min(c["lat"] for c in celdas), "norte": max(c["lat"] for c in celdas),
                "oeste": min(c["lon"] for c in celdas), "este": max(c["lon"] for c in celdas)}
        osm = terreno.obtener_terreno_osm(bbox, celdas)
        barreras, resistencia = sorted(osm["barreras"]), osm["resistencia"]
    except Exception as e:  # noqa: BLE001
        avisos.append(f"Sin OSM ({e}): sin ríos ni caminos que frenen el frente.")
    return {
        "parametros_base": {
            "focos_iniciales": iniciales, "foco_fila": iniciales[0]["fila"],
            "foco_columna": iniciales[0]["columna"], "num_iteraciones": n_pasos,
            "minutos_por_iteracion": MINUTOS_POR_ITERACION, "multiplicador_viento": 1.0,
            "delta_humedad": 0.0, "delta_temperatura_c": 0.0, "inicio_utc": evento["inicio_utc"],
        },
        "serie_ambiental": serie_amb, "serie_viento": serie_vto,
        "barreras_extra": barreras, "resistencia_extra": resistencia, "_avisos": avisos,
    }


def preparar(zona: dict, evento_id: str, rol: str, cicatriz, avance, grid_previo=None,
             insumos_previos=None) -> dict:
    """Construye la fila de validacion_paquetes. `zona` es la fila de validacion_zonas."""
    eventos = zona["eventos"]["eventos"]
    ev = next((e for e in eventos if e["evento_id"] == evento_id), None)
    if not ev:
        raise KeyError(f"No existe {evento_id} en {zona['id']}.")
    if cicatriz is None:
        raise ValueError(f"No hay cicatriz de MapBiomas para {zona['anio']}: súbela antes de empaquetar.")
    focos = leer_csv_texto(texto_gz(zona["focos_gz"]))
    for f in focos:
        f["latitude"], f["longitude"] = float(f["latitude"]), float(f["longitude"])
    fe = sorted((f for f in focos if f.get("evento") == evento_id), key=lambda f: f["fecha_hora_utc"])
    ini = datetime.fromisoformat(fe[0]["fecha_hora_utc"])
    fin = datetime.fromisoformat(fe[-1]["fecha_hora_utc"])
    if not cicatriz.con_dato([f["latitude"] for f in fe], [f["longitude"] for f in fe]).any():
        raise ValueError("La cicatriz de MapBiomas de ese año no cubre este evento.")
    margen = timedelta(days=3)
    otros = [f for f in focos if f.get("evento") and f["evento"] != evento_id and
             not (ini - margen <= datetime.fromisoformat(f["fecha_hora_utc"]) <= fin + margen)]

    P = zona["municipio"]
    prefijo = slug(P["nombre"]).replace("-", "")[:3].upper()
    desde, hasta = ini.strftime("%Y-%m-%d"), fin.strftime("%Y-%m-%d")
    lat_c = sum(f["latitude"] for f in fe) / len(fe)
    dlat, dlon = MARGEN_KM * 1000 / M_LAT, MARGEN_KM * 1000 / m_lon(lat_c)
    if grid_previo:
        grid = grid_previo
        la = [float(c["lat"]) for c in grid]
        lo = [float(c["lon"]) for c in grid]
        ventana = Ventana(min(lo), min(la), max(lo), max(la), prefijo)
        info_grid = {"fuentes": {"grid": "reutilizado"}, "faltan": []}
    else:
        ventana = Ventana(min(f["longitude"] for f in fe) - dlon, min(f["latitude"] for f in fe) - dlat,
                          max(f["longitude"] for f in fe) + dlon, max(f["latitude"] for f in fe) + dlat, prefijo)
        n = (ventana.filas + 1) * (ventana.columnas + 1)
        if n > MAX_CELDAS:
            raise ValueError(f"La ventana del evento tiene {n:,} celdas (máximo {MAX_CELDAS:,}). "
                             "El evento es demasiado extenso para el servidor gratuito.")
        grid, info_grid = construir_grid(ventana, desde, hasta, avance)
        if any(x.startswith(("ndvi", "humedad")) for x in info_grid["faltan"]):
            raise RuntimeError("No se pudo construir el grid: falta " + "; ".join(info_grid["faltan"]))

    avance(0.60, "Cicatriz observada (MapBiomas)…")
    obs, meta_obs = observado(grid, ventana, fe, otros, cicatriz)

    iniciales, filas_ini = [], []
    for f in fe:
        if datetime.fromisoformat(f["fecha_hora_utc"]) > ini + timedelta(hours=HORAS_INICIALES):
            break
        k = ventana.celda(f["latitude"], f["longitude"])
        filas_ini.append({"fecha": f["fecha_hora_utc"][:10], "hora": f["fecha_hora_utc"][11:16],
                          "lat": f["latitude"], "lon": f["longitude"], "frp": f.get("frp"),
                          "sensor": f.get("fuente_firms"), "nivel": f.get("nivel"),
                          "grid_id": ventana.id_celda(*k), "fila": k[0], "columna": k[1]})
        if {"fila": k[0], "columna": k[1]} not in iniciales:
            iniciales.append({"fila": k[0], "columna": k[1]})

    dur_h = (fin - ini).total_seconds() / 3600
    evento = {
        "municipio": P["nombre"], "departamento": P["departamento"], "anio": zona["anio"],
        "evento_id": evento_id, "fecha_inicio": desde, "fecha_fin": hasta,
        "inicio_utc": ini.isoformat(), "fin_utc": fin.isoformat(), "duracion_h": round(dur_h, 1),
        "numero_focos": len(fe), "sensor_principal": fe[0].get("fuente_firms"),
        "frp_total_mw": ev.get("frp_total_mw"), "frp_max_mw": ev.get("frp_max_mw"),
        "extension_km": ev.get("extension_km"), "centro": ev["centro"],
        "criterio_seleccion": "Elegido en el dashboard entre los eventos detectados.",
        "parametros_agrupamiento": zona["eventos"]["agrupamiento"],
    }
    n_pasos = max(int(round(max(dur_h, 6.0) * 60 / MINUTOS_POR_ITERACION)), 1)
    if insumos_previos:
        ins = dict(insumos_previos)
        ins["parametros_base"] = {**ins["parametros_base"], "focos_iniciales": iniciales,
                                  "foco_fila": iniciales[0]["fila"], "foco_columna": iniciales[0]["columna"],
                                  "num_iteraciones": n_pasos, "inicio_utc": evento["inicio_utc"]}
    else:
        ins = insumos_externos(grid, iniciales, evento, n_pasos, avance)
    avisos = ins.pop("_avisos", []) if "_avisos" in ins else []

    pid = f"{zona['municipio_id']}-{zona['anio']}-{evento_id}"
    paquete = {
        "id": pid, "municipio": {k: P[k] for k in ("id", "nombre", "departamento", "provincia", "area_km2")},
        "anio": zona["anio"], "rol": rol, "evento": evento,
        "verificacion": {"nivel_evento": ev["verificacion"], "fraccion_confirmada": ev.get("fraccion_confirmada"),
                         "focos_por_nivel": _contar(fe, "nivel")},
        "ventana": {"bbox": [round(v, 5) for v in ventana.bbox], "celdas": len(grid), "margen_km": MARGEN_KM},
        "grid": info_grid, "observado": meta_obs, "avisos": avisos,
        "horizonte_h": round(n_pasos * MINUTOS_POR_ITERACION / 60, 2),
        "insumos": {"horas_clima": len(ins.get("serie_ambiental") or []),
                    "horas_viento": len(ins.get("serie_viento") or []),
                    "barreras": len(ins.get("barreras_extra") or []),
                    "celdas_resistencia": len(ins.get("resistencia_extra") or {})},
        "fecha_preparacion": datetime.now(timezone.utc).isoformat(),
    }
    return {
        "id": pid, "zona_id": zona["id"], "municipio_id": zona["municipio_id"], "anio": zona["anio"],
        "evento_id": evento_id, "rol": rol, "paquete": paquete, "evento": evento,
        "focos_iniciales": filas_ini,
        "grid_gz": gz_texto(csv_texto(grid, COLS_GRID)),
        "observado_gz": gz_texto(csv_texto(obs, COLS_OBS)),
        "insumos_gz": gz_json(ins),
    }


def _contar(filas, clave):
    out = {}
    for f in filas:
        out[f.get(clave)] = out.get(f.get(clave), 0) + 1
    return out


def desempaquetar(fila: dict) -> dict:
    """Fila de validacion_paquetes → grid, observado e insumos listos para el motor."""
    return {"grid": leer_csv_texto(texto_gz(fila["grid_gz"])),
            "observado": leer_csv_texto(texto_gz(fila["observado_gz"])),
            "insumos": json_gz(fila["insumos_gz"])}
