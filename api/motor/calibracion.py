"""
============================================================================
CALIBRACIÓN AUTOMÁTICA DE LAS CONSTANTES K — puerto de motor/calibracion.js
============================================================================
Evolución diferencial (DE/rand/1/bin) contra los perímetros reales de NASA
FIRMS (2021, 2023, 2024). Ver la explicación larga en el .js original o en
MEJORAS.md — aquí solo cambia el lenguaje, no el método.
============================================================================
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from ..almacen.db import guardar_calibracion as _persistir_calibracion
from ..almacen.db import leer_calibracion as leer_calibracion  # noqa: F401  (re-export)
from ..servicios.grid import obtener_indice
from ..servicios.meteo import obtener_serie_viento_historica
from .automata import CONSTANTES_POR_DEFECTO, perimetro_simulado

MINUTOS_POR_ITERACION = 15

GENES = {
    "p_base": (0.02, 0.80, False),
    "K_PENDIENTE_ARRIBA": (0.50, 8.00, False),
    "K_PENDIENTE_ABAJO": (0.10, 4.00, False),
    "K_VIENTO": (0.02, 0.60, False),
    "K_HUMEDAD": (0.10, 1.50, False),
    "RESIDENCIA_MAX": (2, 10, True),
    "SPOTTING_PROB": (0.000, 0.060, False),
}
NOMBRES = list(GENES.keys())


def _celda_en_indice(lat, lon):
    from ..servicios.grid import celda_en
    return celda_en(lat, lon)


def construir_perimetros_reales(grid: list[dict], focos: list[dict], ventana_dias: int = 4,
                                 min_celdas: int = 15) -> list[dict]:
    por_evento: dict[str, list] = {}
    for f in focos:
        if not f.get("evento") or f.get("lat") is None or f.get("lon") is None:
            continue
        por_evento.setdefault(str(f["evento"]), []).append(f)

    perimetros = []
    ms_dia = 86400.0
    ix = obtener_indice()
    por_id = ix["por_id"]

    for evento, lista in por_evento.items():
        ordenados = []
        for f in lista:
            try:
                t = datetime.fromisoformat(str(f["fecha"])).timestamp()
            except ValueError:
                continue
            ordenados.append({**f, "t": t})
        ordenados.sort(key=lambda f: f["t"])
        if len(ordenados) < min_celdas:
            continue

        ancho = ventana_dias * ms_dia
        mejor = {"inicio": 0, "fin": 0, "n": 0}
        j = 0
        for i in range(len(ordenados)):
            while j < len(ordenados) and ordenados[j]["t"] - ordenados[i]["t"] <= ancho:
                j += 1
            if j - i > mejor["n"]:
                mejor = {"inicio": i, "fin": j, "n": j - i}

        en_ventana = ordenados[mejor["inicio"]:mejor["fin"]]
        if not en_ventana:
            continue
        celdas = set()
        celda_foco = None
        t_primer_dia = en_ventana[0]["t"] + ms_dia
        iniciales = {}
        ids_iniciales = set()
        for f in en_ventana:
            c = _celda_en_indice(f["lat"], f["lon"])
            if not c:
                continue
            celdas.add(c["id"])
            if celda_foco is None:
                celda_foco = c
            if f["t"] <= t_primer_dia:
                iniciales[f"{c['fila']},{c['columna']}"] = {"fila": c["fila"], "columna": c["columna"]}
                ids_iniciales.add(c["id"])

        if len(celdas) < min_celdas or celda_foco is None:
            continue

        perimetros.append({
            "evento": evento, "celdas": celdas,
            "dilatar": lambda conjunto, _por_id=por_id, _pfc=ix["por_fila_col"]: dilatar(conjunto, _por_id, _pfc, 1),
            "foco": {"fila": celda_foco["fila"], "columna": celda_foco["columna"]},
            "desde": en_ventana[0]["fecha"], "hasta": en_ventana[-1]["fecha"],
            "detecciones": len(en_ventana), "iniciales": list(iniciales.values()),
            "ids_iniciales": ids_iniciales,
            "area_km2": round(len(celdas) * 0.25, 1),
            "pasos": round((ventana_dias * 24 * 60) / MINUTOS_POR_ITERACION),
        })

    perimetros.sort(key=lambda p: len(p["celdas"]), reverse=True)
    return perimetros


def dilatar(celdas: set, indice_por_id: dict, indice_por_fila_col: dict, radio: int = 1) -> set:
    salida = set(celdas)
    for id_ in celdas:
        c = indice_por_id.get(id_)
        if not c:
            continue
        for df in range(-radio, radio + 1):
            for dc in range(-radio, radio + 1):
                v = indice_por_fila_col.get(f"{c['fila'] + df},{c['columna'] + dc}")
                if v:
                    salida.add(v["id"])
    return salida


def comparar_perimetros(simulado: set, real: set, excluir: Optional[set] = None,
                         dilatar_set: Optional[Callable[[set], set]] = None) -> dict:
    sim, obs = simulado, real
    if excluir:
        sim = {i for i in sim if i not in excluir}
        obs = {i for i in obs if i not in excluir}

    inter = len(sim & obs)
    precision_estricta = inter / len(sim) if sim else 0
    recall_estricto = inter / len(obs) if obs else 0
    f1_estricto = (2 * precision_estricta * recall_estricto / (precision_estricta + recall_estricto)
                  if (precision_estricta + recall_estricto) > 0 else 0)
    union_estricta = len(sim) + len(obs) - inter

    base = {
        "f1_estricto": f1_estricto,
        "iou_estricto": inter / union_estricta if union_estricta else 0,
        "area_sim": len(sim), "area_real": len(obs),
        "razon_area": len(sim) / len(obs) if obs else 0,
    }

    if not dilatar_set:
        return {**base, "f1": f1_estricto, "precision": precision_estricta, "recall": recall_estricto,
                "iou": base["iou_estricto"]}

    obs_dilatado = dilatar_set(obs)
    sim_dilatado = dilatar_set(sim)
    aciertos = sum(1 for i in sim if i in obs_dilatado)
    cubiertos = sum(1 for i in obs if i in sim_dilatado)

    precision = aciertos / len(sim) if sim else 0
    recall = cubiertos / len(obs) if obs else 0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0

    return {**base, "f1": f1, "precision": precision, "recall": recall,
            "iou": (f1 / (2 - f1)) if f1 != 2 else 0}


def _genes_a_parametros(vector: list[float]) -> dict:
    out = {}
    for i, nombre in enumerate(NOMBRES):
        minimo, maximo, entero = GENES[nombre]
        v = min(max(vector[i], minimo), maximo)
        out[nombre] = round(v) if entero else v
    return out


def _evaluar(vector: list[float], grid: list[dict], perimetros: list[dict], semillas: list[int],
             elevacion=None, barreras_extra=None) -> dict:
    g = _genes_a_parametros(vector)
    constantes = {
        **CONSTANTES_POR_DEFECTO,
        "K_PENDIENTE_ARRIBA": g["K_PENDIENTE_ARRIBA"], "K_PENDIENTE_ABAJO": g["K_PENDIENTE_ABAJO"],
        "K_VIENTO": g["K_VIENTO"], "K_HUMEDAD": g["K_HUMEDAD"],
        "RESIDENCIA_MAX": max(g["RESIDENCIA_MAX"], CONSTANTES_POR_DEFECTO["RESIDENCIA_MIN"]),
        "SPOTTING_PROB": g["SPOTTING_PROB"],
    }

    suma, n = 0.0, 0
    detalle = []

    for per in perimetros:
        f1_evento = 0.0
        metricas = None
        for semilla in semillas:
            sim = perimetro_simulado(
                grid,
                {
                    "foco_fila": per["foco"]["fila"], "foco_columna": per["foco"]["columna"],
                    "focos_iniciales": per["iniciales"], "p_base": g["p_base"],
                    "num_iteraciones": per["pasos"], "minutos_por_iteracion": MINUTOS_POR_ITERACION,
                    "multiplicador_viento": 1, "delta_humedad": 0, "delta_temperatura_c": 0,
                    "semilla": semilla, "serie_viento": per.get("serieViento"),
                },
                {"constantes": constantes, "elevacion": elevacion, "barreras_extra": barreras_extra,
                 "limite_celdas": len(per["celdas"]) * 8},
            )
            m = comparar_perimetros(sim, per["celdas"], excluir=per["ids_iniciales"], dilatar_set=per["dilatar"])
            castigo = 3 / m["razon_area"] if m["razon_area"] > 3 else 1
            f1_evento += m["f1"] * castigo
            if metricas is None:
                metricas = m
        f1_evento /= len(semillas)
        suma += f1_evento
        n += 1
        detalle.append({"evento": per["evento"], "f1": f1_evento, **(metricas or {})})

    return {"aptitud": suma / n if n else 0, "detalle": detalle, "genes": g, "constantes": constantes}


def calibrar(grid: list[dict], focos: list[dict], poblacion: int = 12, generaciones: int = 10,
             f_de: float = 0.7, cr: float = 0.9, ventana_dias: int = 4,
             on_progreso: Optional[Callable[[float, Optional[dict]], None]] = None,
             elevacion=None, barreras_extra=None, semillas: Optional[list[int]] = None,
             usar_viento_historico: bool = True) -> dict:
    semillas = semillas or [1, 2]
    perimetros = construir_perimetros_reales(grid, focos, ventana_dias=ventana_dias)

    if usar_viento_historico:
        for per in perimetros:
            try:
                celda_foco = next((c for c in grid if c["fila"] == per["foco"]["fila"]
                                   and c["columna"] == per["foco"]["columna"]), None)
                if celda_foco:
                    per["serieViento"] = obtener_serie_viento_historica(
                        celda_foco["lat"], celda_foco["lon"], per["desde"], per["hasta"])
            except Exception as e:  # noqa: BLE001
                print(f"Sin viento histórico para {per['evento']}: {e}")

    if not perimetros:
        raise RuntimeError(
            "No hay perímetros reales utilizables en focos.csv (se necesitan focos etiquetados por evento).")

    dim = len(NOMBRES)

    def azar(i):
        minimo, maximo, _ = GENES[NOMBRES[i]]
        return minimo + random.random() * (maximo - minimo)

    pob = [[azar(i) for i in range(dim)] for _ in range(poblacion)]
    pob[0] = [0.30 if nombre == "p_base" else CONSTANTES_POR_DEFECTO.get(nombre, azar(NOMBRES.index(nombre)))
              for nombre in NOMBRES]

    puntajes = [_evaluar(ind, grid, perimetros, semillas, elevacion, barreras_extra) for ind in pob]
    mejor_idx = max(range(poblacion), key=lambda i: puntajes[i]["aptitud"])

    historia = [{"generacion": 0, "mejor": puntajes[mejor_idx]["aptitud"]}]
    total_pasos = generaciones * poblacion
    hechos = 0

    for g in range(1, generaciones + 1):
        for i in range(poblacion):
            a = b = c = i
            while a == i:
                a = random.randrange(poblacion)
            while b == i or b == a:
                b = random.randrange(poblacion)
            while c == i or c == a or c == b:
                c = random.randrange(poblacion)

            j_azar = random.randrange(dim)
            prueba = []
            for j, v in enumerate(pob[i]):
                if random.random() < cr or j == j_azar:
                    minimo, maximo, _ = GENES[NOMBRES[j]]
                    prueba.append(min(max(pob[a][j] + f_de * (pob[b][j] - pob[c][j]), minimo), maximo))
                else:
                    prueba.append(v)

            res = _evaluar(prueba, grid, perimetros, semillas, elevacion, barreras_extra)
            if res["aptitud"] > puntajes[i]["aptitud"]:
                pob[i] = prueba
                puntajes[i] = res

            hechos += 1
            if on_progreso and hechos % 4 == 0:
                mi = max(range(poblacion), key=lambda k: puntajes[k]["aptitud"])
                on_progreso(hechos / total_pasos, puntajes[mi])
                time.sleep(0)

        mejor_idx = max(range(poblacion), key=lambda i: puntajes[i]["aptitud"])
        historia.append({"generacion": g, "mejor": puntajes[mejor_idx]["aptitud"]})

    mejor = puntajes[mejor_idx]
    resultado = {
        "fecha": datetime.now(timezone.utc).isoformat(),
        "metodo": f"Evolución diferencial DE/rand/1/bin · población {poblacion} · {generaciones} generaciones",
        "p_base": mejor["genes"]["p_base"],
        "constantes": mejor["constantes"],
        "f1": mejor["aptitud"],
        "detalle": mejor["detalle"],
        "historia": historia,
        "perimetros": [
            {"evento": p["evento"], "celdas": len(p["celdas"]), "area_km2": p["area_km2"],
             "desde": p["desde"], "hasta": p["hasta"], "detecciones": p["detecciones"],
             "foco": p["foco"], "pasos": p["pasos"]}
            for p in perimetros
        ],
        "minutos_por_iteracion": MINUTOS_POR_ITERACION,
    }

    _persistir_calibracion(resultado)
    return resultado


guardar_calibracion = _persistir_calibracion
