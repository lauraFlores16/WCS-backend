"""
============================================================================
SIMULACIÓN — orquestación del lado servidor — puerto de motor/simulacion.js
============================================================================
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from ..almacen import db as almacen_db
from ..servicios.grid import obtener_grid, obtener_indice
from ..servicios.terreno import obtener_dem, obtener_terreno_osm
from .alertas import evaluar_alertas
from .automata import ejecutar_automata

AREA_POR_CELDA_HA = 25.0


def preparar_entorno(parametros: dict, opciones: Optional[dict] = None) -> dict:
    """Reúne todo lo que el motor necesita del mundo exterior.

    Se extrajo de `ejecutar_simulacion` para que la simulación interactiva
    (`motor/sesion_simulacion.py`) arranque con EXACTAMENTE el mismo terreno,
    las mismas barreras y las mismas constantes calibradas. Si cada camino
    montara su propio entorno, una corrida pausada y una de un tirón dejarían
    de ser comparables y no se sabría por qué.
    """
    opciones = opciones or {}
    elevacion = None
    barreras_extra = None
    resumen_terreno = None

    if opciones.get("usar_terreno", True) is not False:
        try:
            dem = obtener_dem(parametros["foco_fila"], parametros["foco_columna"],
                              opciones.get("radio_dem") or 20)
            elevacion = dem["alturas"]
        except Exception as e:  # noqa: BLE001
            print(f"[simulacion] sin DEM: {e}")
        try:
            osm = obtener_terreno_osm()
            barreras_extra = set(osm["barreras"])
            resumen_terreno = osm["resumen"]
        except Exception as e:  # noqa: BLE001
            print(f"[simulacion] sin capa OSM: {e}")

    calibracion = almacen_db.leer_calibracion()
    constantes = parametros.get("constantes") or opciones.get("constantes") or (
        calibracion["constantes"] if calibracion else None)

    return {
        "elevacion": elevacion,
        "barreras_extra": barreras_extra,
        "constantes": constantes,
        "serie_viento": parametros.get("serie_viento") or opciones.get("serie_viento"),
        # La serie ambiental (temperatura, humedad relativa, VPD y lluvia por
        # hora) la descargaba `servicios/meteo.py` desde el principio y no
        # llegaba al motor: de todo el pronóstico solo se usaba el viento.
        "serie_ambiental": parametros.get("serie_ambiental") or opciones.get("serie_ambiental"),
        "resumen_terreno": resumen_terreno,
    }


def ejecutar_simulacion(parametros: dict, opciones: Optional[dict] = None) -> dict:
    opciones = opciones or {}
    grid = obtener_grid()
    indice = obtener_indice()

    entorno = preparar_entorno(parametros, opciones)
    resumen_terreno = entorno["resumen_terreno"]
    constantes = entorno["constantes"]

    resultado_motor = ejecutar_automata(grid, parametros, {
        "elevacion": entorno["elevacion"],
        "barreras_extra": entorno["barreras_extra"],
        "serie_viento": entorno["serie_viento"],
        "serie_ambiental": entorno["serie_ambiental"],
        "constantes": constantes,
        "guion": parametros.get("guion") or opciones.get("guion"),
    })
    return _persistir(parametros, resultado_motor["iteraciones"],
                      resultado_motor["metadatos"], resumen_terreno, constantes, opciones)


def guardar_sesion_como_escenario(parametros: dict, iteraciones: list,
                                  metadatos_motor_bruto: dict,
                                  opciones: Optional[dict] = None) -> dict:
    """Guarda una sesión interactiva ya terminada como escenario normal.

    Una corrida pausada e intervenida vale exactamente lo mismo que una de un
    tirón —se comprobó que dan resultados idénticos— así que se persiste por el
    mismo camino y acaba en el mismo Historial. Lo único que la distingue es su
    `guion`, que va dentro de los metadatos del motor.
    """
    return _persistir(parametros, iteraciones, metadatos_motor_bruto,
                      metadatos_motor_bruto.get("resumen_terreno"),
                      metadatos_motor_bruto.get("constantes"), opciones or {})


def _persistir(parametros: dict, iteraciones: list, metadatos_motor_bruto: dict,
               resumen_terreno, constantes, opciones: dict) -> dict:
    """Convierte el resultado del motor en un escenario y lo guarda.

    Estaba metido dentro de `ejecutar_simulacion`; se separa para que la
    consola interactiva guarde por el MISMO camino y no aparezca un segundo
    formato de escenario en la base de datos.
    """
    grid = obtener_grid()
    indice = obtener_indice()

    escenario_id = str(uuid.uuid4())
    probs = {c["id"]: c.get("prob_ignicion") for c in grid}
    alertas = []
    for i, it in enumerate(iteraciones):
        alertas.extend(evaluar_alertas(escenario_id, it, iteraciones[i - 1] if i > 0 else None, probs))

    ultima = iteraciones[-1]
    acc = {"ndvi": 0.0, "humedad": 0.0, "velocidad_viento": 0.0, "temperatura_aire": 0.0}
    nq = 0
    por_id = indice["por_id"]
    for celda in ultima["celdas"]:
        c = por_id.get(celda["celda_id"])
        if not c:
            continue
        acc["ndvi"] += c.get("ndvi") or 0
        acc["humedad"] += c.get("humedad") or 0
        acc["velocidad_viento"] += ((c.get("viento_u") or 0) ** 2 + (c.get("viento_v") or 0) ** 2) ** 0.5
        acc["temperatura_aire"] += c.get("temperatura_aire") if c.get("temperatura_aire") is not None else (c.get("lst_c") or 0)
        nq += 1

    variables_promedio = None
    if nq:
        variables_promedio = {
            "ndvi": round(acc["ndvi"] / nq, 4), "humedad": round(acc["humedad"] / nq, 4),
            "velocidad_viento": round(acc["velocidad_viento"] / nq, 4),
            "temperatura_aire": round(acc["temperatura_aire"] / nq, 4),
        }

    celda_foco = indice["por_fila_col"].get(f"{parametros['foco_fila']},{parametros['foco_columna']}")

    estado_prev: dict = {}
    iteraciones_delta = []
    for it in iteraciones:
        cambios = []
        for c in it["celdas"]:
            if estado_prev.get(c["celda_id"]) != c["estado"]:
                cambios.append({"celda_id": c["celda_id"], "lat": round(c["lat"], 5),
                                 "lon": round(c["lon"], 5), "estado": c["estado"]})
                estado_prev[c["celda_id"]] = c["estado"]
        iteraciones_delta.append({
            "iteracion": it["iteracion"],
            "num_celdas_ardiendo": it["num_celdas_ardiendo"],
            "num_celdas_quemadas": it["num_celdas_quemadas"],
            "viento": it.get("viento"),
            # El ambiente viaja con el delta para que el historial pueda
            # reproducir después las curvas de temperatura, humedad y lluvia
            # junto a la del incendio. Sin esto, un escenario guardado perdería
            # la mitad de la historia: se vería QUÉ pasó pero no POR QUÉ.
            "ambiente": it.get("ambiente"),
            "cambios": cambios,
        })

    parametros_guardados = {k: v for k, v in parametros.items() if k != "serie_viento"}

    metadatos_motor = {
        "constantes": metadatos_motor_bruto["constantes"],
        "usa_dem": metadatos_motor_bruto["usa_dem"],
        "celdas_con_dem": metadatos_motor_bruto["celdas_con_dem"],
        "usa_serie_viento": metadatos_motor_bruto["usa_serie_viento"],
        "barreras_osm": metadatos_motor_bruto["barreras_osm"],
        "saltos_spotting": len(metadatos_motor_bruto["eventos_spotting"]),
        "minutos_por_iteracion": metadatos_motor_bruto["minutos_por_iteracion"],
        "resumen_terreno": resumen_terreno,
        "calibrado": bool(constantes),
        # Escenarios meteorológicos que actuaron sobre esta corrida. Es lo que
        # convierte el escenario guardado en algo defendible: dice no solo qué
        # se simuló, sino qué tiempo se le supuso y en qué paso entró.
        "usa_serie_ambiental": metadatos_motor_bruto.get("usa_serie_ambiental", False),
        "guion": metadatos_motor_bruto.get("guion") or [],
        "celdas_apagadas_por_lluvia": metadatos_motor_bruto.get("celdas_apagadas_por_lluvia", 0),
    }

    escenario = {
        "escenario_id": escenario_id,
        "nombre": parametros.get("nombre_escenario") or f"Escenario {datetime.now(timezone.utc).isoformat()}",
        "descripcion": parametros.get("descripcion"),
        "parametros": parametros_guardados,
        "horas_serie_viento": len(parametros["serie_viento"]) if parametros.get("serie_viento") else 0,
        "creado_en": datetime.now(timezone.utc).isoformat(),
        "creado_por": (opciones.get("usuario") or {}).get("nombre", "demo"),
        "iteraciones_delta": iteraciones_delta,
        "alertas": alertas,
        "variables_promedio": variables_promedio,
        "foco_coordenadas": {"lat": celda_foco["lat"], "lon": celda_foco["lon"]} if celda_foco else None,
        "area_final_ha": ultima["num_celdas_quemadas"] * AREA_POR_CELDA_HA,
        "metadatos_motor": metadatos_motor,
        "diagnostico": parametros.get("diagnostico"),
    }

    almacen_db.guardar_escenario(escenario)

    return {
        "escenario_id": escenario_id,
        "parametros": escenario["parametros"],
        "iteraciones": iteraciones,  # completas, para pintar de inmediato
        "metadatos_motor": metadatos_motor,
        "alertas": alertas,
    }


def reconstruir_iteraciones(esc: dict) -> list[dict]:
    if esc.get("iteraciones") and not esc.get("iteraciones_delta"):
        return esc["iteraciones"]
    if not esc.get("iteraciones_delta"):
        return []

    estado: dict = {}
    salida = []
    for d in esc["iteraciones_delta"]:
        for c in d["cambios"]:
            estado[c["celda_id"]] = c
        celdas = [c for c in estado.values() if c["estado"] in ("ardiendo", "quemada", "quemado")]
        salida.append({
            "iteracion": d["iteracion"],
            "num_celdas_ardiendo": d["num_celdas_ardiendo"],
            "num_celdas_quemadas": d["num_celdas_quemadas"],
            "viento": d.get("viento"),
            "celdas": celdas,
        })
    return salida


def obtener_simulacion(escenario_id: str) -> Optional[dict]:
    esc = almacen_db.obtener_escenario(escenario_id)
    if not esc:
        return None
    return {
        "escenario_id": esc["escenario_id"],
        "parametros": esc["parametros"],
        "iteraciones": reconstruir_iteraciones(esc),
        "metadatos_motor": esc.get("metadatos_motor"),
    }
