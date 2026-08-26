"""
============================================================================
SERVICIO DE METEOROLOGÍA (Open-Meteo) — puerto de backend/servicios/meteo.js
============================================================================
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from django.conf import settings

from ..lib.cache import con_cache, con_cache_tolerante
from ..lib.cola import pedir

BASE = "https://api.open-meteo.com/v1/forecast"
ARCHIVO = "https://archive-api.open-meteo.com/v1/archive"

VARS_ACTUALES = ",".join([
    "temperature_2m", "relative_humidity_2m", "wind_speed_10m",
    "wind_direction_10m", "wind_gusts_10m", "precipitation", "surface_pressure",
])

VARS_HORARIAS = ",".join([
    "temperature_2m", "relative_humidity_2m", "wind_speed_10m", "wind_direction_10m",
    "wind_gusts_10m", "precipitation", "vapour_pressure_deficit",
    "et0_fao_evapotranspiration", "soil_moisture_0_to_1cm", "soil_temperature_0cm",
])


def direccion_a_componentes(velocidad_kmh, direccion_grados) -> dict:
    vel = (velocidad_kmh or 0) / 3.6
    rad = ((direccion_grados or 0) * math.pi) / 180
    return {"u": -vel * math.sin(rad), "v": -vel * math.cos(rad), "velocidad_ms": vel}


def obtener_meteorologia(lat: float | None = None, lon: float | None = None, horas_adelante: int = 48) -> dict:
    lat = settings.APOLO["lat"] if lat is None else lat
    lon = settings.APOLO["lon"] if lon is None else lon
    clave = f"meteo:{lat:.2f},{lon:.2f}"

    def producir():
        url = (
            f"{BASE}?latitude={lat:.4f}&longitude={lon:.4f}"
            f"&current={VARS_ACTUALES}&hourly={VARS_HORARIAS}"
            f"&past_days=2&forecast_days=3&timezone=America%2FLa_Paz"
        )
        r = pedir(url, etiqueta="Open-Meteo")
        json_ = r.json()
        if json_.get("error"):
            raise RuntimeError(f"Open-Meteo: {json_.get('reason')}")
        return json_

    r = con_cache_tolerante(clave, settings.TTL_SEGUNDOS["meteo"], producir)

    salida = _normalizar(r.valor, horas_adelante)
    salida["procedencia"] = {
        "origen": r.origen,
        "edad_minutos": round(r.edad_ms / 60000),
        "reciente": r.origen != "cache-caducada",
        "aviso": str(r.error) if r.error else None,
    }
    return salida


def _normalizar(json_: dict, horas_adelante: int) -> dict:
    c = json_.get("current") or {}
    h = json_.get("hourly") or {}
    tiempos = h.get("time") or []

    ahora_iso = (c.get("time") or datetime.now(timezone.utc).isoformat())[:13]
    i_ahora = next((i for i, t in enumerate(tiempos) if t[:13] == ahora_iso), -1)
    if i_ahora < 0:
        i_ahora = max(0, len(tiempos) - 72)

    actual_viento = direccion_a_componentes(c.get("wind_speed_10m"), c.get("wind_direction_10m"))

    serie_viento = []
    serie_ambiental = []
    for i in range(i_ahora, min(i_ahora + horas_adelante, len(tiempos))):
        comp = direccion_a_componentes(_at(h, "wind_speed_10m", i), _at(h, "wind_direction_10m", i))
        serie_viento.append({
            "hora": tiempos[i], "u": comp["u"], "v": comp["v"],
            "velocidad_ms": comp["velocidad_ms"],
            "rafaga_ms": (_at(h, "wind_gusts_10m", i) or 0) / 3.6,
            "direccion_grados": _at(h, "wind_direction_10m", i),
        })
        serie_ambiental.append({
            "hora": tiempos[i],
            "temperatura_c": _at(h, "temperature_2m", i),
            "humedad_relativa": _at(h, "relative_humidity_2m", i),
            "vpd_kpa": _at(h, "vapour_pressure_deficit", i),
            "precipitacion_mm": _at(h, "precipitation", i),
            "humedad_suelo": _at(h, "soil_moisture_0_to_1cm", i),
            "viento_ms": (_at(h, "wind_speed_10m", i) or 0) / 3.6,
        })

    horas_sin_lluvia = 0
    for i in range(i_ahora, -1, -1):
        if (_at(h, "precipitation", i) or 0) > 0.1:
            break
        horas_sin_lluvia += 1
    lluvia_48h = sum((_at(h, "precipitation", i) or 0) for i in range(max(0, i_ahora - 48), i_ahora + 1))

    hr = c.get("relative_humidity_2m")
    return {
        "fuente": "Open-Meteo (best_match: ECMWF/GFS/ICON)",
        "consultado": datetime.now(timezone.utc).isoformat(),
        "coordenadas": {"lat": json_.get("latitude"), "lon": json_.get("longitude")},
        "elevacion_m": json_.get("elevation"),
        "actual": {
            "hora": c.get("time"),
            "temperatura_c": c.get("temperature_2m"),
            "humedad_relativa": (hr / 100) if hr is not None else None,
            "viento_ms": actual_viento["velocidad_ms"],
            "viento_kmh": c.get("wind_speed_10m"),
            "viento_direccion": c.get("wind_direction_10m"),
            "viento_u": actual_viento["u"],
            "viento_v": actual_viento["v"],
            "rafaga_ms": (c.get("wind_gusts_10m") or 0) / 3.6,
            "precipitacion_mm": c.get("precipitation") or 0,
            "vpd_kpa": _at(h, "vapour_pressure_deficit", i_ahora),
            "humedad_suelo": _at(h, "soil_moisture_0_to_1cm", i_ahora),
        },
        "sequedad": {"horas_sin_lluvia": horas_sin_lluvia, "lluvia_48h_mm": round(lluvia_48h, 2)},
        "serieViento": serie_viento,
        "serieAmbiental": serie_ambiental,
    }


def _at(h: dict, campo: str, i: int):
    arr = h.get(campo)
    if not arr or i < 0 or i >= len(arr):
        return None
    return arr[i]


def obtener_climatologia(lat: float | None = None, lon: float | None = None, anios: int = 5) -> dict:
    lat = settings.APOLO["lat"] if lat is None else lat
    lon = settings.APOLO["lon"] if lon is None else lon
    mes = datetime.now().month
    clave = f"clima:{lat:.2f},{lon:.2f}:{mes}:{anios}"

    def producir():
        hoy = datetime.now()
        acumulado = {"t": 0.0, "hr": 0.0, "v": 0.0, "n": 0}

        for k in range(1, anios + 1):
            anio = hoy.year - k
            ini = f"{anio}-{mes:02d}-01"
            fin = f"{anio}-{mes:02d}-28"
            url = (
                f"{ARCHIVO}?latitude={lat:.4f}&longitude={lon:.4f}"
                f"&start_date={ini}&end_date={fin}"
                f"&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m&timezone=America%2FLa_Paz"
            )
            try:
                r = pedir(url, etiqueta="Open-Meteo (archivo ERA5)")
                j = r.json()
                h = j.get("hourly") or {}
                tiempos = h.get("time") or []
                for i in range(len(tiempos)):
                    t = _at(h, "temperature_2m", i)
                    if t is None:
                        continue
                    acumulado["t"] += t
                    acumulado["hr"] += _at(h, "relative_humidity_2m", i) or 0
                    acumulado["v"] += (_at(h, "wind_speed_10m", i) or 0) / 3.6
                    acumulado["n"] += 1
            except Exception as e:  # noqa: BLE001
                print(f"[meteo] climatología {anio} no disponible: {e}")

        if acumulado["n"] == 0:
            raise RuntimeError("No se pudo construir la climatología ERA5.")

        return {
            "mes": mes, "anios_usados": anios, "horas": acumulado["n"],
            "temperatura_c": round(acumulado["t"] / acumulado["n"], 2),
            "humedad_relativa": round(acumulado["hr"] / acumulado["n"] / 100, 4),
            "viento_ms": round(acumulado["v"] / acumulado["n"], 3),
            "fuente": "ERA5 (Open-Meteo Historical Weather API)",
        }

    r = con_cache_tolerante(clave, settings.TTL_SEGUNDOS["climatologia"], producir)
    return r.valor


def obtener_serie_viento_historica(lat: float, lon: float, fecha_inicio: str, fecha_fin: str) -> list[dict]:
    clave = f"viento-hist:{lat:.2f},{lon:.2f}:{fecha_inicio}:{fecha_fin}"

    def producir():
        url = (
            f"{ARCHIVO}?latitude={lat:.4f}&longitude={lon:.4f}"
            f"&start_date={fecha_inicio}&end_date={fecha_fin}"
            f"&hourly=wind_speed_10m,wind_direction_10m,temperature_2m,relative_humidity_2m"
            f"&timezone=America%2FLa_Paz"
        )
        r = pedir(url, etiqueta="Open-Meteo (archivo ERA5)")
        j = r.json()
        h = j.get("hourly") or {}
        tiempos = h.get("time") or []
        salida = []
        for i, t in enumerate(tiempos):
            comp = direccion_a_componentes(_at(h, "wind_speed_10m", i), _at(h, "wind_direction_10m", i))
            hr = _at(h, "relative_humidity_2m", i)
            salida.append({
                "hora": t, "u": comp["u"], "v": comp["v"], "velocidad_ms": comp["velocidad_ms"],
                "temperatura_c": _at(h, "temperature_2m", i),
                "humedad_relativa": (hr / 100) if hr is not None else None,
            })
        return salida

    r = con_cache(clave, settings.TTL_SEGUNDOS["viento_historico"], producir)
    return r.valor


def indice_peligro(meteo: dict | None) -> dict | None:
    if not meteo:
        return None
    a = meteo["actual"]
    t = a.get("temperatura_c") or 0
    hr = (a.get("humedad_relativa") or 0.5) * 100
    v_kmh = a.get("viento_kmh") or 0

    f_t = min(max((t - 15) / (30 - 15), 0), 1)
    f_h = min(max((45 - hr) / (45 - 30), 0), 1)
    f_v = min(max(v_kmh / 30, 0), 1)
    f_s = min(meteo["sequedad"]["horas_sin_lluvia"] / 168, 1)

    puntaje = 0.3 * f_t + 0.3 * f_h + 0.25 * f_v + 0.15 * f_s

    return {
        "puntaje": min(max(puntaje, 0), 1),
        "condiciones": {
            "temperatura": {"valor": t, "umbral": 30, "cumple": t >= 30, "factor": f_t},
            "humedad": {"valor": hr, "umbral": 30, "cumple": hr <= 30, "factor": f_h},
            "viento": {"valor": v_kmh, "umbral": 30, "cumple": v_kmh >= 30, "factor": f_v},
            "sequedad": {
                "valor": meteo["sequedad"]["horas_sin_lluvia"], "umbral": 168,
                "cumple": meteo["sequedad"]["horas_sin_lluvia"] >= 168, "factor": f_s,
            },
        },
        "regla_303030": sum([t >= 30, hr <= 30, v_kmh >= 30]),
    }
