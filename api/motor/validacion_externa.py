"""
============================================================================
VALIDACIÓN EXTERNA EN VIVO — Rurrenabaque E122 y paquetes de validacion_zonas
============================================================================
Repite desde el backend, y a petición del dashboard, la misma cadena que se
ejecutó en local con validacion_rurrenabaque/python:

    f11  autómata con los parámetros CONGELADOS de Apolo, N repeticiones
    f12  cruce con la cicatriz observada (MapBiomas Fuego) y métricas

Sin internet ni pandas: los insumos externos (clima ERA5 horario, barreras
OSM, resistencia del terreno) se leen de datos/insumos_validacion_rbq.json,
que exportar_insumos_rbq.py sacó de la grabación de la corrida oficial. Así
la corrida del dashboard ve EXACTAMENTE el mismo mundo que la oficial y, con
las mismas semillas, tiene que reproducir sus números. Esa es la comprobación.

Reglas que NO se tocan aquí (son las de la validación externa):
  · parámetros congelados: se leen, nunca se ajustan;
  · solo se evalúan celdas con valido = 1;
  · umbral de quema = el congelado (fracción de repeticiones).
============================================================================
"""
from __future__ import annotations

import csv
import json
import math
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import automata

RAIZ = Path(__file__).resolve().parents[2]          # raíz de WCS-backend
CARPETA = RAIZ / "validacion_rurrenabaque"
DATOS = CARPETA / "datos"
RESULTADOS = CARPETA / "resultados"
CONFIG = CARPETA / "config" / "parametros_ca_congelados.json"

AREA_CELDA_KM2 = 0.25
MINUTOS_POR_ITERACION = 15
MAX_REPETICIONES = 30
MARGEN_VENTANA = 10      # celdas alrededor del evento que se envían al mapa

_cache: dict = {}
_cerrojo = threading.Lock()
_trabajos: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Lectura de archivos (csv/json, sin pandas)
# ---------------------------------------------------------------------------
def _num(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except ValueError:
        return v
    return int(f) if f.is_integer() and "." not in str(v) and "e" not in str(v).lower() else f


def _leer_csv(ruta: Path) -> list[dict]:
    with ruta.open(encoding="utf-8", newline="") as fh:
        return [{k: _num(v) for k, v in fila.items()} for fila in csv.DictReader(fh)]


def _leer_json(ruta: Path, defecto=None):
    if not ruta.exists():
        return defecto
    return json.loads(ruta.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Paquetes de validación
# ---------------------------------------------------------------------------
# Un paquete = un evento listo para el autómata. Rurrenabaque E122 vive en su
# carpeta histórica; los demás los produce validacion_zonas/python (p1-p3) en
# validacion_zonas/zonas/<municipio>/<año>/<evento>/.
ZONAS = RAIZ / "validacion_zonas" / "zonas"
PAQUETE_RBQ = "rurrenabaque-2023-E122"


def _rutas_rbq() -> dict:
    return {
        "grid": DATOS / "rurrenabaque_grid_500m.csv", "observado": DATOS / "observado_grid.csv",
        "insumos": DATOS / "insumos_validacion_rbq.json", "focos": RESULTADOS / "focos_iniciales.csv",
        "evento": RESULTADOS / "evento_validacion.json",
        "oficial": RESULTADOS / "metricas_validacion.json",
        "sin_spotting": RESULTADOS / "sens_sin_spotting" / "metricas_validacion.json",
        "resumen": RESULTADOS / "f11_resumen_ejecucion.json",
        "repeticiones": RESULTADOS / "ca_repeticiones.csv", "paquete": None,
        "limite": RAIZ.parent / "WCS-frontend" / "public" / "datos" / "rurrenabaque_limite.geojson",
    }


def _rutas_paquete(carpeta: Path) -> dict:
    r = carpeta / "resultados"
    return {
        "grid": carpeta / "grid.csv", "observado": carpeta / "observado.csv",
        "insumos": carpeta / "insumos.json", "focos": carpeta / "focos_iniciales.csv",
        "evento": carpeta / "evento.json", "oficial": r / "metricas_validacion.json",
        "sin_spotting": r / "sens_sin_spotting" / "metricas_validacion.json",
        "resumen": r / "resumen_ejecucion.json", "repeticiones": r / "ca_repeticiones.csv",
        "paquete": carpeta / "paquete.json", "limite": carpeta.parent.parent / "limite.geojson",
        "carpeta": carpeta,
    }


def listar_paquetes() -> list[dict]:
    """Todos los eventos que se pueden ejecutar, con su rol y sus métricas."""
    salida = [{
        "id": PAQUETE_RBQ, "municipio": "Rurrenabaque", "departamento": "Beni",
        "municipio_id": "puerto-menor-de-rurrenabaque-beni", "anio": 2023, "evento_id": "E122",
        "rol": "validacion", "verificacion": "confirmado", "historico": True,
        "iou": (_leer_json(RESULTADOS / "metricas_validacion.json", {}) or {}).get("iou"),
        "f1": (_leer_json(RESULTADOS / "metricas_validacion.json", {}) or {}).get("f1"),
    }]
    vistos = {p["id"] for p in salida}
    try:
        from ..almacen import db
        for f in db.vz_listar_paquetes():
            if f["id"] in vistos:
                continue
            p, met = f.get("paquete") or {}, f.get("oficial") or {}
            salida.append({
                "id": f["id"], "municipio": p.get("municipio", {}).get("nombre", f["municipio_id"]),
                "departamento": p.get("municipio", {}).get("departamento"), "municipio_id": f["municipio_id"],
                "anio": f["anio"], "evento_id": f["evento_id"], "rol": f["rol"],
                "verificacion": (p.get("verificacion") or {}).get("nivel_evento"),
                "inicio_utc": (f.get("evento") or {}).get("inicio_utc"),
                "focos": (f.get("evento") or {}).get("numero_focos"),
                "historico": False, "en_base": True, "iou": met.get("iou"), "f1": met.get("f1"),
            })
    except Exception as e:  # noqa: BLE001
        print(f"[validacion] sin paquetes de la base: {e}")
    return salida


def _en_base(pid: str) -> bool:
    if pid == PAQUETE_RBQ:
        return False
    if ZONAS.exists() and any(_leer_json(pj).get("id") == pid for pj in ZONAS.glob("*/*/*/paquete.json")):
        return False
    return True


def _cargar_base(pid: str) -> dict:
    from ..almacen import db
    from ..zonas.paquete import desempaquetar
    fila = db.vz_leer_paquete(pid)
    if not fila:
        raise KeyError(f"No existe el paquete de validación '{pid}'.")
    d = desempaquetar(fila)
    num = lambda filas: [{k: _num(v) for k, v in r.items()} for r in filas]  # noqa: E731
    return dict(id=pid, rutas=None, en_base=True, grid=num(d["grid"]),
                observado={o["id"]: o for o in num(d["observado"])}, insumos=d["insumos"],
                cfg=_leer_json(CONFIG), focos=fila.get("focos_iniciales") or [],
                evento=fila.get("evento") or {}, paquete=fila.get("paquete") or {},
                oficial=fila.get("oficial"), sin_spotting=None,
                resumen_f11=fila.get("resumen_oficial") or {},
                repeticiones_oficiales=fila.get("repeticiones") or [])


def rutas(pid: str) -> dict:
    if pid == PAQUETE_RBQ:
        return _rutas_rbq()
    for pj in ZONAS.glob("*/*/*/paquete.json") if ZONAS.exists() else []:
        if _leer_json(pj).get("id") == pid:
            return _rutas_paquete(pj.parent)
    raise KeyError(f"No existe el paquete de validación '{pid}'.")


def _cargar(pid: str = PAQUETE_RBQ) -> dict:
    """Carga (una vez por paquete) todo lo que necesita la validación."""
    with _cerrojo:
        if pid in _cache:
            return _cache[pid]
        if _en_base(pid):
            d = _cargar_base(pid)
            for c in d["grid"]:
                for k in ("lat", "lon", "pendiente_grados", "ndvi", "humedad",
                          "viento_u", "viento_v", "prob_ignicion", "elevacion_m"):
                    if c.get(k) is not None:
                        c[k] = float(c[k])
            _cache[pid] = d
            return d
        R = rutas(pid)
        faltan = [k for k in ("grid", "observado", "insumos") if not R[k].exists()]
        if not CONFIG.exists():
            faltan.append("parametros_ca_congelados.json")
        if faltan:
            raise FileNotFoundError(f"Faltan archivos del paquete {pid}: " + ", ".join(faltan))

        grid = _leer_csv(R["grid"])
        # El motor espera floats en las variables; fila y columna quedan enteras.
        for c in grid:
            for k in ("lat", "lon", "pendiente_grados", "ndvi", "humedad",
                      "viento_u", "viento_v", "prob_ignicion", "elevacion_m"):
                if c.get(k) is not None:
                    c[k] = float(c[k])
        _cache[pid] = dict(
            id=pid, rutas=R, grid=grid,
            observado={o["id"]: o for o in _leer_csv(R["observado"])},
            insumos=_leer_json(R["insumos"]), cfg=_leer_json(CONFIG),
            focos=_leer_csv(R["focos"]) if R["focos"].exists() else [],
            evento=_leer_json(R["evento"], {}),
            paquete=_leer_json(R["paquete"], {}) if R["paquete"] else {},
            oficial=_leer_json(R["oficial"]),
            sin_spotting=_leer_json(R["sin_spotting"]),
            resumen_f11=_leer_json(R["resumen"], {}),
            repeticiones_oficiales=_leer_csv(R["repeticiones"]) if R["repeticiones"].exists() else [],
        )
        return _cache[pid]


def olvidar(pid: str | None = None) -> None:
    """Descarta la caché (tras escribir resultados nuevos de un paquete)."""
    with _cerrojo:
        if pid:
            _cache.pop(pid, None)
        else:
            _cache.clear()


# ---------------------------------------------------------------------------
# Métricas — mismas fórmulas que f12_metricas_validacion.py
# ---------------------------------------------------------------------------
def calcular_metricas(simuladas: set[str], observado: dict, focos: list[dict]) -> dict:
    validas = [o for o in observado.values() if o.get("valido") == 1]
    tp = fp = fn = tn = 0
    obs_pts, sim_pts = [], []
    for o in validas:
        ob = bool(o.get("observado"))
        si = o["id"] in simuladas
        tp += ob and si
        fp += (not ob) and si
        fn += ob and not si
        tn += (not ob) and not si
        if ob:
            obs_pts.append((o["lat"], o["lon"]))
        if si:
            sim_pts.append((o["lat"], o["lon"]))

    def div(a, b):
        return a / b if b else None

    precision, recall = div(tp, tp + fp), div(tp, tp + fn)
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall else None)
    iou = div(tp, tp + fp + fn)
    area_obs, area_sim = (tp + fn) * AREA_CELDA_KM2, (tp + fp) * AREA_CELDA_KM2
    dif = area_sim - area_obs
    r4 = lambda v: None if v is None else round(v, 4)  # noqa: E731

    m = {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "celdas_evaluadas": tp + fp + fn + tn,
        "celdas_excluidas_sin_dato": len(observado) - len(validas),
        "iou": r4(iou), "precision": r4(precision), "recall": r4(recall),
        "f1": r4(f1), "accuracy": r4(div(tp + tn, tp + fp + fn + tn)),
        "area_observada_km2": round(area_obs, 3),
        "area_simulada_km2": round(area_sim, 3),
        "diferencia_area_km2": round(dif, 3),
        "error_area_pct": round(abs(dif) / area_obs * 100, 2) if area_obs else None,
        "comportamiento": ("SOBREESTIMACIÓN" if dif > 0 else
                           "SUBESTIMACIÓN" if dif < 0 else "COINCIDENCIA EXACTA"),
        "distancia_centroides_km": None, "error_angular_grados": None,
    }

    if obs_pts and sim_pts:
        lat_ref = sum(o["lat"] for o in validas) / len(validas)
        m_lat, m_lon = 110574.0, 111320.0 * math.cos(math.radians(lat_ref))
        c_obs = (sum(p[0] for p in obs_pts) / len(obs_pts), sum(p[1] for p in obs_pts) / len(obs_pts))
        c_sim = (sum(p[0] for p in sim_pts) / len(sim_pts), sum(p[1] for p in sim_pts) / len(sim_pts))
        m["distancia_centroides_km"] = round(
            math.hypot((c_sim[1] - c_obs[1]) * m_lon, (c_sim[0] - c_obs[0]) * m_lat) / 1000, 3)
        m["centroide_observado"] = {"lat": round(c_obs[0], 6), "lon": round(c_obs[1], 6)}
        m["centroide_simulado"] = {"lat": round(c_sim[0], 6), "lon": round(c_sim[1], 6)}
        if focos:
            org = (sum(f["lat"] for f in focos) / len(focos), sum(f["lon"] for f in focos) / len(focos))

            def acimut(h):
                return (math.degrees(math.atan2((h[1] - org[1]) * m_lon, (h[0] - org[0]) * m_lat)) + 360) % 360

            a_o, a_s = acimut(c_obs), acimut(c_sim)
            err = (a_s - a_o + 180) % 360 - 180
            m.update(angulo_observado_grados=round(a_o, 2), angulo_simulado_grados=round(a_s, 2),
                     error_angular_grados=round(abs(err), 2), error_angular_con_signo=round(err, 2),
                     origen_focos={"lat": round(org[0], 6), "lon": round(org[1], 6)})
    return m


# ---------------------------------------------------------------------------
# Contexto para la pantalla (lo que no cambia entre corridas)
# ---------------------------------------------------------------------------
def contexto(pid: str = PAQUETE_RBQ) -> dict:
    d = _cargar(pid)
    obs = d["observado"]
    quemadas = [o for o in obs.values() if o.get("observado") == 1 and o.get("valido") == 1]
    filas = [o["fila"] for o in quemadas] + [f["fila"] for f in d["focos"]]
    cols = [o["columna"] for o in quemadas] + [f["columna"] for f in d["focos"]]
    f0, f1 = min(filas) - MARGEN_VENTANA, max(filas) + MARGEN_VENTANA
    c0, c1 = min(cols) - MARGEN_VENTANA, max(cols) + MARGEN_VENTANA

    # Paso de la rejilla, medido (no supuesto) sobre las celdas vecinas.
    por_fc = {(c["fila"], c["columna"]): c for c in d["grid"]}
    dlat = dlon = None
    for (f, c), cel in por_fc.items():
        if dlat is None and (f + 1, c) in por_fc:
            dlat = abs(por_fc[(f + 1, c)]["lat"] - cel["lat"])
        if dlon is None and (f, c + 1) in por_fc:
            dlon = abs(por_fc[(f, c + 1)]["lon"] - cel["lon"])
        if dlat and dlon:
            break

    ventana = [
        {"id": o["id"], "fila": o["fila"], "columna": o["columna"], "lat": o["lat"], "lon": o["lon"],
         "observado": o.get("observado"), "valido": o.get("valido")}
        for o in obs.values() if f0 <= o["fila"] <= f1 and c0 <= o["columna"] <= c1
    ]
    cfg, ev, paq = d["cfg"], d["evento"], d["paquete"]
    mun = paq.get("municipio") or {"nombre": "Rurrenabaque", "departamento": "Beni",
                                    "id": "puerto-menor-de-rurrenabaque-beni"}
    return {
        "id": pid, "zona": mun["nombre"], "departamento": mun["departamento"],
        "municipio_id": mun.get("id"), "anio": paq.get("anio", 2023),
        "rol": paq.get("rol", "validacion"),
        "verificacion": paq.get("verificacion") or {"nivel_evento": "confirmado"},
        "observado_meta": paq.get("observado"),
        "historico": pid == PAQUETE_RBQ,
        "evento": {k: ev.get(k) for k in ("evento_id", "fecha_inicio", "fecha_fin", "inicio_utc",
                                           "fin_utc", "duracion_h", "numero_focos", "sensor_principal",
                                           "frp_total_mw", "frp_max_mw", "extension_km", "centro",
                                           "criterio_seleccion", "parametros_agrupamiento")},
        "parametros": {
            "modelo": cfg.get("modelo"), "version_modelo": cfg.get("version_modelo"),
            "p_base": cfg.get("p_base"), "fecha_congelacion": cfg.get("fecha_congelacion"),
            "origen": cfg.get("origen_parametros"), "regla": cfg.get("_regla"),
            "area_calibracion": cfg.get("area_calibracion"),
            "metricas_calibracion_apolo": cfg.get("metricas_calibracion_apolo"),
            "ejecucion": cfg.get("ejecucion"), "constantes": cfg.get("constantes"),
            "genes_calibrables": cfg.get("genes_calibrables"),
            "coinciden_con_motor": _constantes_coinciden(cfg),
        },
        "oficial": d["oficial"],
        # La sensibilidad guardada solo vale si se hizo con el p_base congelado
        # actual. La que hay en resultados/sens_sin_spotting es de la
        # calibración v2 (p_base 0,1659): se marca para no presentarla como
        # vigente. La pantalla puede recalcularla en vivo.
        "sin_spotting": d["sin_spotting"],
        "sin_spotting_vigente": bool(d["sin_spotting"]) and
            d["sin_spotting"].get("p_base") == cfg.get("p_base"),
        "repeticiones_oficiales": d["repeticiones_oficiales"],
        "fecha_corrida_oficial": d["resumen_f11"].get("fecha_ejecucion"),
        "focos_iniciales": [{k: f.get(k) for k in ("fecha", "hora", "lat", "lon", "frp", "fila",
                                                    "columna", "nivel", "sensor")}
                            for f in d["focos"]],
        "validas_total": sum(1 for o in obs.values() if o.get("valido") == 1),
        "rejilla": {"celdas": len(d["grid"]), "paso_lat": dlat, "paso_lon": dlon,
                    "ventana": {"fila": [f0, f1], "columna": [c0, c1]}},
        "celdas": ventana,
        "insumos": {
            "horas_clima": len(d["insumos"].get("serie_ambiental") or []),
            "barreras": len(d["insumos"].get("barreras_extra") or []),
            "celdas_resistencia": len(d["insumos"].get("resistencia_extra") or {}),
        },
        "max_repeticiones": MAX_REPETICIONES,
    }


def limite(pid: str) -> dict | None:
    if _en_base(pid):
        from ..almacen import db
        fila = db.vz_leer_paquete(pid)
        if not fila:
            raise KeyError(f"No existe el paquete de validación '{pid}'.")
        z = db.vz_leer_zona(fila["zona_id"], con_focos=False)
        return (z or {}).get("limite")
    r = rutas(pid).get("limite")
    return _leer_json(r) if r and r.exists() else None


def _constantes_coinciden(cfg: dict) -> dict:
    """Las constantes congeladas frente a las del motor actual (solo informa)."""
    motor = automata.CONSTANTES_POR_DEFECTO
    cong = cfg.get("constantes") or {}
    return {
        "faltan_en_congelados": sorted(k for k in motor if k not in cong),
        "distintas": sorted(k for k in cong if k in motor and cong[k] != motor[k]),
    }


# ---------------------------------------------------------------------------
# Corrida
# ---------------------------------------------------------------------------
def ejecutar(repeticiones: int, spotting: bool = True, progreso=None,
             pid: str = PAQUETE_RBQ) -> dict:
    d = _cargar(pid)
    cfg, ins = d["cfg"], d["insumos"]
    n_rep = max(1, min(int(repeticiones), MAX_REPETICIONES))
    umbral = cfg["ejecucion"]["umbral_prob_quemada"]
    if cfg.get("p_base") is None:
        raise ValueError("Los parámetros congelados no tienen p_base: hay que congelar Apolo antes.")

    constantes = dict(automata.CONSTANTES_POR_DEFECTO)
    constantes.update(cfg["constantes"])
    if not spotting:
        constantes["SPOTTING_ACTIVO"] = False

    base = dict(ins["parametros_base"])
    base["constantes"] = constantes
    base["p_base"] = cfg["p_base"]
    opciones = {
        "elevacion": {c["id"]: c["elevacion_m"] for c in d["grid"] if c.get("elevacion_m") is not None},
        "barreras_extra": set(ins.get("barreras_extra") or []),
        "resistencia_extra": ins.get("resistencia_extra") or {},
        "serie_ambiental": ins.get("serie_ambiental"),
        "serie_viento": ins.get("serie_viento"),
    }
    minutos = base.get("minutos_por_iteracion", MINUTOS_POR_ITERACION)

    # Por celda, la hora de ignición en cada repetición (None = no ardió).
    igniciones: dict[str, list] = {}
    reps, veces = [], {}
    t0 = time.time()
    for i, semilla in enumerate(range(1, n_rep + 1)):
        r = automata.ejecutar_automata(d["grid"], {**base, "semilla": semilla}, opciones)
        primera: dict[str, float] = {}
        for it in r["iteraciones"]:
            h = round(it["iteracion"] * minutos / 60, 2)
            for c in it["celdas"]:
                if c.get("estado") in ("quemada", "ardiendo") and c["celda_id"] not in primera:
                    primera[c["celda_id"]] = h
        ult = r["iteraciones"][-1]
        finales = {c["celda_id"] for c in ult["celdas"] if c.get("estado") in ("quemada", "ardiendo")}
        for cid in finales:
            veces[cid] = veces.get(cid, 0) + 1
        for cid, h in primera.items():
            igniciones.setdefault(cid, [None] * n_rep)[i] = h
        reps.append({
            "semilla": semilla, "celdas_quemadas": len(finales),
            "area_km2": round(len(finales) * AREA_CELDA_KM2, 3),
            "duracion_horas": round(ult["iteracion"] * minutos / 60, 2),
            "eventos_spotting": len(r["metadatos"].get("eventos_spotting", [])),
        })
        if progreso:
            progreso((i + 1) / n_rep, reps[-1])

    prob = {cid: v / n_rep for cid, v in veces.items()}
    simuladas = {cid for cid, p in prob.items() if p >= umbral}
    metricas = calcular_metricas(simuladas, d["observado"], d["focos"])
    metricas.update(n_repeticiones=n_rep, umbral_prob_quemada=umbral, p_base=cfg["p_base"])

    of = d["oficial"] or {}
    comparables = ("tp", "fp", "fn", "tn", "iou", "precision", "recall", "f1",
                   "area_simulada_km2", "distancia_centroides_km", "error_angular_grados")
    reproduce = None
    if spotting and of and n_rep == (of.get("n_repeticiones") or MAX_REPETICIONES):
        reproduce = {k: {"vivo": metricas.get(k), "oficial": of.get(k),
                         "coincide": metricas.get(k) == of.get(k)} for k in comparables}

    areas = [r["celdas_quemadas"] for r in reps]
    media = sum(areas) / len(areas)
    sd = math.sqrt(sum((a - media) ** 2 for a in areas) / (len(areas) - 1)) if len(areas) > 1 else 0.0
    return {
        "fecha": datetime.now(timezone.utc).isoformat(),
        "duracion_s": round(time.time() - t0, 1),
        "repeticiones": reps, "n_repeticiones": n_rep, "spotting": spotting,
        "horizonte_h": round(base["num_iteraciones"] * minutos / 60, 2),
        "umbral": umbral,
        "probabilidad": {k: round(v, 4) for k, v in prob.items()},
        "igniciones": igniciones,
        # Datos de las celdas que ardieron, por si alguna cae fuera de la
        # ventana del mapa: sin esto el frontend no podría clasificarlas.
        "celdas_ignicion": {
            cid: {k: d["observado"].get(cid, {}).get(k) for k in
                  ("fila", "columna", "lat", "lon", "observado", "valido")}
            for cid in igniciones},
        "metricas": metricas,
        "dispersion": {"min": min(areas), "max": max(areas), "media": round(media, 2),
                       "desviacion": round(sd, 2),
                       "cv_pct": round(sd / media * 100, 1) if media else None},
        "reproduce_oficial": reproduce,
        "paquete": pid,
        "_constantes_usadas": constantes,
    }


def guardar_oficial(pid: str, res: dict) -> Path:
    """Escribe la corrida como resultado OFICIAL del paquete (lo usa p3).

    Solo para paquetes de validacion_zonas: el de Rurrenabaque E122 tiene su
    resultado oficial de la cadena f11 + f12 y no se pisa desde aquí.
    """
    if pid == PAQUETE_RBQ:
        raise ValueError("El resultado oficial de Rurrenabaque E122 no se reescribe.")
    d = _cargar(pid)
    if d.get("en_base"):
        return _guardar_oficial_base(pid, d, res)
    R = d["rutas"]
    carpeta = R["oficial"].parent if res["spotting"] else R["sin_spotting"].parent
    carpeta.mkdir(parents=True, exist_ok=True)
    ev, paq = d["evento"], d["paquete"]
    m = {
        "municipio": paq["municipio"]["nombre"], "departamento": paq["municipio"]["departamento"],
        "evento": ev.get("evento_id"), "periodo": f"{ev.get('fecha_inicio')} a {ev.get('fecha_fin')}",
        "rol": paq.get("rol"), "fecha_calculo": res["fecha"],
        **res["metricas"], "spotting": res["spotting"],
        "advertencias": ([] if (paq.get("observado") or {}).get("celdas_observadas", 0) >= 10
                         else ["Menos de 10 celdas observadas: métricas inestables."]),
        "_nota_accuracy": "COMPLEMENTARIA: dominada por los TN. No usar como métrica principal.",
    }
    (carpeta / "metricas_validacion.json").write_text(
        json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    with (carpeta / "ca_repeticiones.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(res["repeticiones"][0].keys()))
        w.writeheader()
        w.writerows(res["repeticiones"])
    with (carpeta / "comparacion_espacial.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "fila", "columna", "lat", "lon", "observado", "simulado",
                    "prob_quemada", "valido", "clase"])
        umbral = res["umbral"]
        for o in d["observado"].values():
            p = res["probabilidad"].get(o["id"], 0.0)
            sim = int(p >= umbral)
            ob = int(o.get("observado") == 1)
            clase = ("—" if o.get("valido") != 1 else
                     "TP" if ob and sim else "FP" if sim else "FN" if ob else "TN")
            w.writerow([o["id"], o["fila"], o["columna"], o["lat"], o["lon"], ob, sim, p,
                        o.get("valido"), clase])
    cfg = d["cfg"]
    resumen = {
        "fecha_ejecucion": res["fecha"], "motor": "api/motor/automata.py (el mismo del backend)",
        "paquete": pid, "n_repeticiones": res["n_repeticiones"],
        "semillas": list(range(1, res["n_repeticiones"] + 1)), "spotting": res["spotting"],
        "umbral_prob_quemada": res["umbral"], "p_base": cfg.get("p_base"),
        "fecha_congelacion_parametros": cfg.get("fecha_congelacion"),
        "constantes_usadas": res["_constantes_usadas"], "horizonte_horas": res["horizonte_h"],
        "dispersion": res["dispersion"], "duracion_s": res["duracion_s"],
    }
    (carpeta / "resumen_ejecucion.json").write_text(
        json.dumps(resumen, indent=2, ensure_ascii=False), encoding="utf-8")
    olvidar(pid)
    return carpeta


# ---------------------------------------------------------------------------
# Trabajos en segundo plano (Render corta peticiones largas)
# ---------------------------------------------------------------------------
def lanzar(repeticiones: int, spotting: bool, usuario: str | None = None,
           pid: str = PAQUETE_RBQ) -> dict:
    _cargar(pid)   # falla pronto (KeyError) si el paquete no existe
    for t in _trabajos.values():
        if t["estado"] == "ejecutando":
            return _publico(t)
    tid = uuid.uuid4().hex[:12]
    t = {"id": tid, "estado": "ejecutando", "progreso": 0.0, "parciales": [],
         "iniciado": datetime.now(timezone.utc).isoformat(), "usuario": usuario,
         "repeticiones": repeticiones, "spotting": spotting, "paquete": pid,
         "resultado": None, "error": None}
    _trabajos.clear()
    _trabajos[tid] = t

    def avance(frac, rep):
        t["progreso"] = round(frac, 3)
        t["parciales"].append(rep)

    def correr():
        try:
            res = ejecutar(repeticiones, spotting, avance, pid=pid)
            res.pop("_constantes_usadas", None)
            t["resultado"] = res
            t["estado"] = "terminado"
        except Exception as e:  # noqa: BLE001
            t["estado"], t["error"] = "error", f"{type(e).__name__}: {e}"

    threading.Thread(target=correr, daemon=True).start()
    return _publico(t)


def estado_trabajo(tid: str) -> dict | None:
    t = _trabajos.get(tid)
    return _publico(t) if t else None


def _publico(t: dict) -> dict:
    return {k: v for k, v in t.items()}


def _guardar_oficial_base(pid: str, d: dict, res: dict):
    from ..almacen import db
    paq, ev, cfg = d["paquete"], d["evento"], d["cfg"]
    m = {
        "municipio": paq["municipio"]["nombre"], "departamento": paq["municipio"]["departamento"],
        "evento": ev.get("evento_id"), "periodo": f"{ev.get('fecha_inicio')} a {ev.get('fecha_fin')}",
        "rol": paq.get("rol"), "fecha_calculo": res["fecha"], **res["metricas"], "spotting": res["spotting"],
        "advertencias": ([] if (paq.get("observado") or {}).get("celdas_observadas", 0) >= 10
                         else ["Menos de 10 celdas observadas: métricas inestables."]),
    }
    resumen = {
        "fecha_ejecucion": res["fecha"], "paquete": pid, "n_repeticiones": res["n_repeticiones"],
        "spotting": res["spotting"], "umbral_prob_quemada": res["umbral"], "p_base": cfg.get("p_base"),
        "fecha_congelacion_parametros": cfg.get("fecha_congelacion"),
        "horizonte_horas": res["horizonte_h"], "dispersion": res["dispersion"], "duracion_s": res["duracion_s"],
    }
    if res["spotting"]:
        db.vz_actualizar_paquete(pid, {"oficial": m, "resumen_oficial": resumen,
                                       "repeticiones": res["repeticiones"]})
    olvidar(pid)
    return None
