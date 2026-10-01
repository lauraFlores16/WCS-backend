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


def obtener_serie_ambiental_historica(lat: float, lon: float,
                                       fecha_inicio: str, fecha_fin: str) -> list[dict]:
    """Clima horario REAL de un evento pasado, en el formato que lee el motor.

    POR QUÉ HACE FALTA
        El autómata no tiene ningún mecanismo propio que APAGUE un incendio.
        Una celda deja de arder al agotar su tiempo de residencia, pero el
        frente sigue avanzando indefinidamente. Medido sobre Apolo con p_base
        0,14 y sin capa de terreno:

            6 h   →  0,3 % del municipio
           12 h   →  1,5 %
           24 h   →  7,9 %
           48 h   → 39,1 %
           96 h   → 91,0 %

        Bajar p_base solo retrasa la inundación, no la evita. Lo que detiene
        un incendio real y el modelo no estaba viendo es el CICLO DIURNO: de
        noche la temperatura cae, la humedad relativa sube y la propagación se
        frena sola. Y si llovió durante el evento, se apaga.

        El motor ya sabía usar todo eso —`serie_ambiental`, con temperatura,
        humedad, VPD y lluvia por hora— pero solo se alimentaba desde el
        PRONÓSTICO, que no sirve para un evento de 2021 o de 2023. Esta
        función trae esa misma serie del archivo ERA5 para las fechas del
        evento, y con ella la validación histórica deja de correr en un día
        eterno sin noches.

    Devuelve la lista en el mismo formato que `serieAmbiental` de
    `obtener_meteorologia`, para que el motor no note la diferencia.
    """
    clave = f"ambiental-hist:{lat:.2f},{lon:.2f}:{fecha_inicio}:{fecha_fin}"

    def producir():
        url = (
            f"{ARCHIVO}?latitude={lat:.4f}&longitude={lon:.4f}"
            f"&start_date={fecha_inicio}&end_date={fecha_fin}"
            f"&hourly=temperature_2m,relative_humidity_2m,precipitation,"
            f"vapour_pressure_deficit,wind_speed_10m,soil_moisture_0_to_7cm"
            f"&timezone=America%2FLa_Paz"
        )
        r = pedir(url, etiqueta="Open-Meteo (archivo ERA5)")
        h = (r.json().get("hourly") or {})
        tiempos = h.get("time") or []
        salida = []
        for i, t in enumerate(tiempos):
            salida.append({
                "hora": t,
                "temperatura_c": _at(h, "temperature_2m", i),
                "humedad_relativa": _at(h, "relative_humidity_2m", i),
                "vpd_kpa": _at(h, "vapour_pressure_deficit", i),
                "precipitacion_mm": _at(h, "precipitation", i),
                "humedad_suelo": _at(h, "soil_moisture_0_to_7cm", i),
                "viento_ms": (_at(h, "wind_speed_10m", i) or 0) / 3.6,
            })
        return salida

    return con_cache(clave, settings.TTL_SEGUNDOS["viento_historico"], producir).valor


def _factores_peligro(t, hr_pct, v_kmh, horas_sin_lluvia):
    """Los cuatro factores del índice, cada uno normalizado a 0..1.

    Se extrae aparte para que la proyección horaria use EXACTAMENTE la misma
    fórmula y los mismos pesos que el valor actual. Si estuviera duplicada en
    dos sitios —peor aún, uno en Python y otro en JavaScript— acabarían
    divergiendo y la gráfica del panel no diría lo mismo que la tarjeta de al
    lado.

    Los umbrales son los de la regla operacional 30-30-30 de bomberos:
    30 °C, 30 % de humedad relativa, 30 km/h de viento. La normalización es
    lineal entre el punto donde el factor empieza a contar y el umbral.
    """
    f_t = min(max((t - 15) / (30 - 15), 0), 1)
    f_h = min(max((45 - hr_pct) / (45 - 30), 0), 1)
    f_v = min(max(v_kmh / 30, 0), 1)
    f_s = min(max(horas_sin_lluvia / 168, 0), 1)   # 168 h = una semana seca
    return f_t, f_h, f_v, f_s


def _puntaje_peligro(f_t, f_h, f_v, f_s):
    return min(max(0.3 * f_t + 0.3 * f_h + 0.25 * f_v + 0.15 * f_s, 0), 1)


def nivel_peligro(puntaje: float) -> str:
    """Cortes de nivel. Son los mismos que usa el resto del sistema."""
    if puntaje >= 0.70:
        return "critico"
    if puntaje >= 0.50:
        return "alto"
    if puntaje >= 0.30:
        return "moderado"
    return "bajo"


def proyeccion_peligro(meteo: dict | None, horas: int = 12) -> list[dict]:
    """Índice de peligro hora a hora sobre el pronóstico.

    QUÉ ES Y QUÉ NO ES
        Esto proyecta el ÍNDICE DE PELIGRO METEOROLÓGICO, no la probabilidad
        de ocurrencia de XGBoost. Son cosas distintas y conviene no mezclarlas:

          · XGBoost da una probabilidad POR CELDA, estática, entrenada con
            variables del territorio. No se puede recalcular hora a hora: el
            modelo no está en este backend, solo su salida precalculada.

          · El índice de peligro es METEOROLÓGICO y sí evoluciona: depende de
            temperatura, humedad, viento y días sin lluvia, y de eso hay
            pronóstico horario.

        Lo que responde la proyección es «¿las condiciones van a empeorar en
        las próximas horas?», que es una pregunta operativa útil y honesta.
        Presentarla como «probabilidad de incendio» sería atribuirle una
        precisión que no tiene.

    La sequedad acumulada se arrastra hacia adelante: si el pronóstico da
    lluvia en una hora, el contador se reinicia desde ahí.
    """
    if not meteo:
        return []
    serie = meteo.get("serieAmbiental") or []
    if not serie:
        return []

    horas_sin_lluvia = (meteo.get("sequedad") or {}).get("horas_sin_lluvia", 0)

    salida = []
    for h in serie[:horas]:
        lluvia = h.get("precipitacion_mm") or 0
        # El contador de sequedad avanza con el tiempo y se reinicia si llueve.
        horas_sin_lluvia = 0 if lluvia > 0.1 else horas_sin_lluvia + 1

        t = h.get("temperatura_c")
        # OJO: en `serieAmbiental` la humedad viene en PORCENTAJE (0-100) y en
        # `actual` viene en FRACCIÓN (0-1). Es una inconsistencia del propio
        # servicio; se normaliza aquí para no propagarla.
        hr = h.get("humedad_relativa")
        hr_pct = (hr * 100) if (hr is not None and hr <= 1.0) else hr
        v_kmh = (h.get("viento_ms") or 0) * 3.6

        if t is None or hr_pct is None:
            continue

        f_t, f_h, f_v, f_s = _factores_peligro(t, hr_pct, v_kmh, horas_sin_lluvia)
        puntaje = _puntaje_peligro(f_t, f_h, f_v, f_s)

        salida.append({
            "hora": h.get("hora"),
            "puntaje": round(puntaje, 4),
            "nivel": nivel_peligro(puntaje),
            "temperatura_c": t,
            "humedad_relativa_pct": round(hr_pct, 1),
            "viento_kmh": round(v_kmh, 1),
            "precipitacion_mm": round(lluvia, 2),
            "horas_sin_lluvia": horas_sin_lluvia,
            "regla_303030": sum([t >= 30, hr_pct <= 30, v_kmh >= 30]),
            "factores": {
                "temperatura": round(f_t, 3), "humedad": round(f_h, 3),
                "viento": round(f_v, 3), "sequedad": round(f_s, 3),
            },
        })
    return salida


def indice_peligro(meteo: dict | None) -> dict | None:
    if not meteo:
        return None
    a = meteo["actual"]
    t = a.get("temperatura_c") or 0
    hr = (a.get("humedad_relativa") or 0.5) * 100
    v_kmh = a.get("viento_kmh") or 0

    f_t, f_h, f_v, f_s = _factores_peligro(
        t, hr, v_kmh, meteo["sequedad"]["horas_sin_lluvia"])
    puntaje = _puntaje_peligro(f_t, f_h, f_v, f_s)

    return {
        "puntaje": puntaje,
        "nivel": nivel_peligro(puntaje),
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
        # Proyección horaria, misma fórmula y mismos pesos.
        "proyeccion": proyeccion_peligro(meteo, horas=12),
        "_nota": (
            "Índice de peligro METEOROLÓGICO (temperatura, humedad, viento y "
            "sequedad acumulada), no probabilidad de ocurrencia de XGBoost. "
            "Umbrales de la regla operacional 30-30-30."
        ),
    }
