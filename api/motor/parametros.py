"""
============================================================================
PARÁMETROS AUTOMÁTICOS DE SIMULACIÓN — puerto de motor/parametros.js
============================================================================
Cada parámetro se deduce de datos vivos (o del grid si no hay conexión) y
queda justificado en `diagnostico`, que es lo que pinta el panel "Decisiones
de la simulación" en el frontend.
============================================================================
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from ..almacen.db import leer_calibracion
from ..servicios.firms import clave_configurada, obtener_focos_activos
from ..servicios.grid import celda_mas_cercana, promedios_grid
from ..servicios.meteo import indice_peligro, obtener_climatologia, obtener_meteorologia
from .temporada import resolver_temporada

HORIZONTE_HORAS_POR_DEFECTO = 6
MINUTOS_POR_ITERACION = 15

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def rosa_de_los_vientos(grados) -> str:
    if grados is None:
        return "—"
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]
    return dirs[round(grados / 22.5) % 16]


def _elegir_foco(grid: list[dict], foco_manual: dict | None, historicos: list) -> dict:
    if foco_manual:
        return {"celda": foco_manual, "origen": "manual", "detalle": "Foco marcado por el usuario en el mapa"}

    if clave_configurada():
        try:
            r = obtener_focos_activos()
            # Solo focos REALES de NASA. Antes, cuando el servicio caía al
            # respaldo histórico, esta rama etiquetaba una detección de 2019
            # como «foco activo VIIRS» y además se saltaba XGBoost, que es la
            # siguiente prioridad de la cadena.
            focos = r["focos"] if r.get("estado") == "correcto" else []
            if focos:
                mejor = max(focos, key=lambda f: f.get("frp") or 0)
                celda = celda_mas_cercana(mejor["lat"], mejor["lon"])
                if celda:
                    return {
                        "celda": celda, "origen": "firms",
                        "detalle": f"Foco activo VIIRS del {mejor['fecha']} {mejor.get('hora', '')} · "
                                   f"FRP {mejor.get('frp', 'n/d')} MW (el más intenso de {len(focos)} detectados)",
                    }
        except Exception as e:  # noqa: BLE001
            print(f"FIRMS no disponible para elegir foco: {e}")

    mejor_celda = None
    for c in grid:
        if mejor_celda is None or (c.get("prob_ignicion") or 0) > (mejor_celda.get("prob_ignicion") or 0):
            mejor_celda = c
    if mejor_celda:
        return {
            "celda": mejor_celda, "origen": "xgboost",
            "detalle": f"Sin focos activos: se usa la celda de mayor probabilidad de ignición del modelo "
                       f"({(mejor_celda.get('prob_ignicion') or 0) * 100:.1f}%)",
        }

    if historicos:
        ev = max(historicos, key=lambda e: e.get("area_km2") or 0)
        return {
            "celda": {"fila": ev["foco"]["fila"], "columna": ev["foco"]["columna"],
                      "lat": ev["foco"]["lat"], "lon": ev["foco"]["lon"]},
            "origen": "historico", "detalle": f"Foco del evento histórico \"{ev['nombre']}\"",
        }

    raise RuntimeError("No se pudo determinar un foco automáticamente.")


def derivar_parametros(grid: list[dict], opciones: dict | None = None) -> dict:
    opciones = opciones or {}
    foco_manual = opciones.get("foco")
    historicos = opciones.get("historicos") or []
    horizonte_horas = opciones.get("horizonte_horas", HORIZONTE_HORAS_POR_DEFECTO)
    reproducible = opciones.get("reproducible", False)
    temporada_opts = opciones.get("temporada")

    diagnostico = []

    def anotar(etiqueta, valor, fuente, nota):
        diagnostico.append({"etiqueta": etiqueta, "valor": valor, "fuente": fuente, "nota": nota})

    seleccion = _elegir_foco(grid, foco_manual, historicos)
    celda_foco = seleccion["celda"]
    anotar(
        "Foco de ignición", f"fila {celda_foco['fila']}, columna {celda_foco['columna']}",
        {"manual": "Usuario", "firms": "NASA FIRMS (VIIRS)", "xgboost": "Modelo XGBoost V3",
         "historico": "Histórico"}[seleccion["origen"]],
        seleccion["detalle"],
    )

    meteo = climatologia = peligro = None
    error_meteo = None
    try:
        meteo = obtener_meteorologia(celda_foco["lat"], celda_foco["lon"])
        climatologia = obtener_climatologia(celda_foco["lat"], celda_foco["lon"])
        peligro = indice_peligro(meteo)
    except Exception as e:  # noqa: BLE001
        error_meteo = str(e)
        print(f"Open-Meteo no disponible, se usan los promedios del grid: {e}")

    prom = promedios_grid()

    delta_temp = 0.0
    if meteo and climatologia:
        delta_temp = round(meteo["actual"]["temperatura_c"] - climatologia["temperatura_c"], 1)
        anotar(
            "Temperatura",
            f"{meteo['actual']['temperatura_c']:.1f} °C  (Δ {'+' if delta_temp > 0 else ''}{delta_temp} °C)",
            "Open-Meteo · ERA5",
            f"Climatología de {MESES[climatologia['mes'] - 1]} ({climatologia['anios_usados']} años): "
            f"{climatologia['temperatura_c']} °C",
        )
    else:
        anotar("Temperatura", "Δ 0 °C", "Sin conexión", "No se pudo consultar Open-Meteo; se usa el promedio del grid")

    delta_humedad = 0.0
    if meteo:
        hr_actual = meteo["actual"]["humedad_relativa"] if meteo["actual"]["humedad_relativa"] is not None else prom["humedad"]
        referencia = climatologia["humedad_relativa"] if climatologia else prom["humedad"]
        delta_humedad = round(hr_actual - referencia, 3)
        delta_humedad = min(max(delta_humedad, -1), 1)
        anotar(
            "Humedad relativa",
            f"{hr_actual * 100:.0f} %  (Δ {'+' if delta_humedad > 0 else ''}{delta_humedad * 100:.0f} %)",
            "Open-Meteo",
            f"Referencia {referencia * 100:.0f} % · {meteo['sequedad']['horas_sin_lluvia']} h sin lluvia · "
            f"{meteo['sequedad']['lluvia_48h_mm']} mm en 48 h",
        )
    else:
        anotar("Humedad relativa", "Δ 0 %", "Sin conexión", "Se conserva la humedad ERA5 del grid")

    multiplicador_viento = 1.0
    serie_viento = None
    if meteo and meteo["serieViento"]:
        serie_viento = meteo["serieViento"]
        v0 = meteo["actual"]
        v_max = max(s["velocidad_ms"] for s in serie_viento)
        anotar(
            "Viento", f"{v0['viento_ms']:.1f} m/s del {rosa_de_los_vientos(v0['viento_direccion'])}",
            "Open-Meteo (serie horaria)",
            f"Serie de {len(serie_viento)} h cargada en el motor · máximo previsto {v_max:.1f} m/s · "
            f"ráfagas {v0['rafaga_ms']:.1f} m/s. El multiplicador queda en ×1.0 porque el viento ya es real.",
        )
    else:
        anotar("Viento", "×1.0 (ERA5 del grid)", "grid.csv", "Sin serie meteorológica en vivo")

    calibracion = leer_calibracion()
    p_base_calibrado = calibracion["p_base"] if calibracion else 0.30
    factor_peligro = (0.7 + 0.6 * peligro["puntaje"]) if peligro else 1.0
    p_base = round(min(max(p_base_calibrado * factor_peligro, 0.01), 1), 3)

    est = resolver_temporada(temporada_opts or {})
    if est["incluida"]:
        delta_humedad = round(min(max(delta_humedad + est["delta_humedad"], -1), 1), 3)
        p_base = round(min(max(p_base * est["factor_p_base"], 0.01), 1), 3)
        anotar(
            "Temporada",
            f"{est['etiqueta']}{' (auto por fecha)' if est.get('porFecha') else ''}",
            "Evento climático estacional", est["nota"],
        )

    anotar(
        "Velocidad base del frente", f"{p_base * 100:.0f} m/h (p_base {p_base:.3f})",
        f"Calibración {calibracion['fecha'][:10]}" if calibracion else "Valor por defecto",
        (f"p_base calibrada = {p_base_calibrado} (F1 = {(calibracion.get('f1') or 0):.3f} sobre perímetros reales), "
         f"ajustada ×{factor_peligro:.2f} por el peligro meteorológico actual") if calibracion else
        "Aún no se ha corrido la calibración automática (Capa 4 → Calibrar constantes K)",
    )

    num_iteraciones = round((horizonte_horas * 60) / MINUTOS_POR_ITERACION)
    anotar("Iteraciones", f"{num_iteraciones} pasos ({horizonte_horas} h)", "Derivado",
           f"{MINUTOS_POR_ITERACION} min de tiempo simulado por iteración")

    nivel = "BAJO"
    if peligro:
        if peligro["puntaje"] >= 0.66:
            nivel = "ALTO"
        elif peligro["puntaje"] >= 0.4:
            nivel = "MEDIO"
        anotar("Índice de peligro", f"{nivel} ({peligro['puntaje'] * 100:.0f} %)",
               "Regla 30-30-30 + sequedad",
               f"{peligro['regla_303030']}/3 condiciones 30-30-30 cumplidas con datos reales")

    ahora = datetime.now()
    marca = f"{ahora.day}-{MESES[ahora.month - 1]}-{ahora.year} {ahora.hour:02d}:{ahora.minute:02d}"
    if meteo:
        resumen_meteo = (f"{meteo['actual']['temperatura_c']:.0f}°C · "
                         f"HR {meteo['actual']['humedad_relativa'] * 100:.0f}% · "
                         f"viento {meteo['actual']['viento_ms']:.1f} m/s "
                         f"{rosa_de_los_vientos(meteo['actual']['viento_direccion'])}")
    else:
        resumen_meteo = "condiciones ERA5 promedio"
    nombre = f"Apolo · {marca} · {resumen_meteo} · peligro {nivel}"

    parametros = {
        "nombre_escenario": nombre,
        "descripcion": (f"Escenario generado automáticamente. Foco: {seleccion['detalle']}. "
                        + (f"Meteorología en vivo no disponible ({error_meteo})."
                           if error_meteo else f"Meteorología: {meteo['fuente']}.")),
        "foco_fila": celda_foco["fila"],
        "foco_columna": celda_foco["columna"],
        "p_base": p_base,
        "multiplicador_viento": multiplicador_viento,
        "delta_humedad": delta_humedad,
        "delta_temperatura_c": delta_temp,
        "num_iteraciones": num_iteraciones,
        "minutos_por_iteracion": MINUTOS_POR_ITERACION,
        "semilla": 12345 if reproducible else (int(time.time() * 1000) % 2147483647),
        "serie_viento": serie_viento,
        "constantes": calibracion["constantes"] if calibracion else None,
        "temporada": ({"temporada": est["temporada"], "etiqueta": est["etiqueta"],
                       "por_fecha": bool(est.get("porFecha"))} if est["incluida"] else None),
    }

    # --- Frescura de los datos ---------------------------------------------
    # La pantalla tiene que poder decir CUÁNDO se consultó cada cosa. Sin esto
    # no hay forma de distinguir «no hay focos activos» de «el dato tiene seis
    # horas», y son dos situaciones muy distintas para quien está decidiendo.
    #
    # Cada servicio cachea con su propio TTL: meteorología 10 min, FIRMS
    # 10 min, OSM 7 días, DEM nunca. Así que la antigüedad se reporta por
    # servicio, no como un único número.
    actualizado = {
        "consultado": datetime.now(timezone.utc).isoformat(),
        "meteo": {
            "hora_dato": (meteo or {}).get("actual", {}).get("hora"),
            "consultado": (meteo or {}).get("consultado"),
            "fuente": (meteo or {}).get("fuente"),
            "disponible": meteo is not None,
        },
        "firms": None,
    }
    try:
        f = obtener_focos_activos()
        actualizado["firms"] = {
            "activos": f.get("activos"),
            "fuente": f.get("fuente"),
            "dias_ventana": f.get("dias"),
            "edad_minutos": (f.get("procedencia") or {}).get("edad_minutos"),
            "origen": (f.get("procedencia") or {}).get("origen"),
            "reciente": (f.get("procedencia") or {}).get("reciente"),
            "configurada": f.get("configurada"),
        }
    except Exception:  # noqa: BLE001
        actualizado["firms"] = {"disponible": False}

    return {"parametros": parametros, "diagnostico": diagnostico, "meteo": meteo, "climatologia": climatologia,
            "peligro": peligro, "nivel": nivel, "error_meteo": error_meteo, "seleccion_foco": seleccion,
            "actualizado": actualizado}
