"""
============================================================================
AUTÓMATA CELULAR v3 — propagación por VELOCIDAD DE AVANCE
============================================================================
Sustituye al motor v2 (probabilidad de contagio por paso). La versión anterior
queda en `automata_v2_probabilistico.py` solo como referencia histórica.

POR QUÉ SE CAMBIÓ EL MECANISMO
------------------------------
En v2 cada celda ardiendo contagiaba a cada vecina con una probabilidad por
paso y ardía solo 3–5 pasos (45–75 min). Eso es un proceso de percolación: si
cada celda enciende en promedio más de ~2 vecinas el fuego no para nunca, si
enciende menos se apaga enseguida. No hay régimen intermedio. La validación en
Rurrenabaque (E122) lo mostró: con los parámetros calibrados cada celda
encendía ~3 vecinas, el modelo quemó 247 veces el área real, y bajar p_base a
la mitad hacía que se apagara. Las variables ambientales actuaban, pero solo
podían empujar el sistema a un lado u otro del umbral.

CÓMO FUNCIONA AHORA
-------------------
Cada celda ardiendo empuja un frente hacia cada vecina a una VELOCIDAD DE
AVANCE (ROS, m/h). El frente acumula distancia paso a paso; cuando cubre la
distancia entre centros (500 m, 484 m o 695 m en diagonal) la vecina prende.

    ROS = ROS_base · f_viento · f_pendiente · f_vegetacion · f_humedad
                   · f_lluvia · resistencia_destino · heterogeneidad · ruido

    ROS_base = p_base · ROS_REFERENCIA_M_H      (p_base 0,30 → 30 m/h)

Consecuencias:
  · El área crece de forma gradual con los factores. Duplicar ROS duplica la
    distancia recorrida; ya no hay un salto de «nada» a «todo».
  · La humedad del combustible puede llevar la velocidad a CERO (humedad de
    extinción de Rothermel). De noche, con humedad relativa alta, el frente
    se detiene de verdad.
  · Un frente detenido demasiadas horas se apaga (estancamiento). Es el
    mecanismo de extinción que v2 no tenía.
  · El viento acelera a favor y FRENA en contra.

FACTORES
--------
f_viento      exp(K_VIENTO · v · cos θ), acotado en [1/VIENTO_MAX, VIENTO_MAX].
              θ = ángulo entre la dirección de avance y hacia donde va el viento.
f_pendiente   exp(K · Δh/d), K distinto subiendo y bajando, acotado (igual que v2).
f_vegetacion  (NDVI − NDVI_BARRERA) / (NDVI_SATURACION − NDVI_BARRERA), en
              [0,05 · 1]. Con NDVI < NDVI_BARRERA la celda es inerte. Tiene techo:
              en v2 era 0,5 + NDVI y crecía sin límite.
f_humedad     amortiguación de Rothermel (1972):
                  η = 1 − 2,59 r + 5,11 r² − 3,52 r³ ,  r = m / HUMEDAD_EXTINCION
              m = humedad del combustible fino muerto:
                  EMC(HR, T) de Simard (1968), hora a hora, de la serie ERA5
                  + K_HUMEDAD · (humedad_suelo − HUMEDAD_SUELO_REF)
                  + desplazamientos de escenarios / delta_humedad
              Con m ≥ HUMEDAD_EXTINCION, η = 0 y no hay propagación.
f_lluvia      1 / (1 + K_LLUVIA_PROP · mm/h), como en v2.

ESTOCASTICIDAD
--------------
Heterogeneidad fija por celda (lognormal, σ = RUIDO_CELDA): variaciones de
combustible que el grid de 500 m no ve. Más un ruido por paso (σ = RUIDO_PASO).
Las 30 repeticiones dan perímetros distintos pero del mismo orden de tamaño.

INTERFAZ
--------
Idéntica a v2: `ejecutar_automata(grid, parametros, opciones)` y
`perimetro_simulado(...)`, mismas claves de salida, checkpoints por tramos y
escenarios. `serie_viento` y `serie_ambiental` se aceptan juntas o por
separado; si solo llega una, la otra se deriva cuando es posible. Con
`inicio_utc` (opciones o parámetros) ambas series se alinean a la hora de
inicio del incendio.
============================================================================
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from . import escenarios as _escenarios

SIN_QUEMAR, ARDIENDO, QUEMADO, INERTE = 0, 1, 2, 3
_ESTADO_API = {ARDIENDO: "ardiendo", QUEMADO: "quemada"}
MODELO = "velocidad_v3"

TAM_CELDA_NS_M = 500.0
TAM_CELDA_EO_M = 484.0

CONSTANTES_POR_DEFECTO = {
    # --- Velocidad base ------------------------------------------------------
    # ROS_base = p_base · ROS_REFERENCIA_M_H. Se mantiene el nombre p_base para
    # no romper la interfaz (frontend, parametros.py, calibración): ahora es
    # una escala de velocidad, no una probabilidad.
    "ROS_REFERENCIA_M_H": 100.0,
    # --- Viento --------------------------------------------------------------
    "K_VIENTO": 0.15,           # por m/s
    "VIENTO_MAX": 6.0,
    # --- Pendiente (Δh entre celdas) ----------------------------------------
    "K_PENDIENTE_ARRIBA": 3.0,
    "K_PENDIENTE_ABAJO": 1.2,
    "PENDIENTE_MAX": 4.0,
    # --- Vegetación ----------------------------------------------------------
    "NDVI_BARRERA": 0.10,
    "NDVI_SATURACION": 0.45,
    # --- Humedad del combustible --------------------------------------------
    "HUMEDAD_EXTINCION": 0.25,  # fracción; hojarasca tropical 0,20–0,35
    "K_HUMEDAD": 0.5,           # peso de la humedad del suelo del grid
    "HUMEDAD_SUELO_REF": 0.30,
    "HR_REFERENCIA": 0.60,      # sin serie ambiental
    "T_REFERENCIA_C": 28.0,
    "K_TEMPERATURA": 0.01,      # solo para delta_temperatura_c de la interfaz
    "K_VPD": 0.04,              # solo para escenarios que cambian el VPD
    # --- Extinción -----------------------------------------------------------
    "ROS_MIN_M_H": 1.0,          # por debajo, el frente se considera detenido
    "T_ESTANCAMIENTO_H": 6.0,    # horas detenido antes de apagarse
    "T_MAX_ARDIENDO_H": 72.0,    # tope de combustión de una celda de 500 m
    # --- Estocasticidad ------------------------------------------------------
    "RUIDO_CELDA": 0.35,
    "RUIDO_PASO": 0.25,
    # --- Lluvia --------------------------------------------------------------
    "K_LLUVIA_PROP": 0.20,
    "K_LLUVIA_EXT": 0.02,
    # --- Barreras parciales OSM (igual que v2) ------------------------------
    "K_BARRERA": 1.0,
    # --- Spotting (igual que v2, con el signo N-S ya correcto) --------------
    "SPOTTING_ACTIVO": True,
    "SPOTTING_PROB": 0.015,
    "SPOTTING_VIENTO_MIN": 1.0,
    "SPOTTING_DIST_MIN": 2,
    "SPOTTING_DIST_MAX": 6,
    "SPOTTING_DISPERSION": 0.35,
    # --- Vecindad ------------------------------------------------------------
    "VECINDAD": "moore",
    "RADIO_VECINDAD": 1,
    "EXP_DISTANCIA": 1.0,
    # --- Heredadas de v2, SIN USO en v3 (se conservan para no romper
    #     configuraciones guardadas) --------------------------------------------
    "EXP_VIENTO": 1.0, "NDVI_BASE": 0.5, "RESIDENCIA_MIN": 3, "RESIDENCIA_MAX": 6,
    "DECAIMIENTO_FASE": 0.25, "VPD_REFERENCIA": 1.0,
}


# ---------------------------------------------------------------------------
class _Mulberry32:
    __slots__ = ("a",)

    def __init__(self, semilla: int):
        self.a = semilla & 0xFFFFFFFF

    def __call__(self) -> float:
        self.a = (self.a + 0x6D2B79F5) & 0xFFFFFFFF
        t = self.a
        t = ((t ^ (t >> 15)) * (t | 1)) & 0xFFFFFFFF
        t = (t ^ ((t + (((t ^ (t >> 7)) * (t | 61)) & 0xFFFFFFFF)) & 0xFFFFFFFF)) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0


def _mulberry32(semilla: int) -> _Mulberry32:
    return _Mulberry32(semilla)


def _normal(rand) -> float:
    """Normal estándar por Box-Muller a partir del PRNG reproducible."""
    u1 = max(rand(), 1e-12)
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * rand())


# ---------------------------------------------------------------------------
# Física
# ---------------------------------------------------------------------------
def _factor_pendiente(delta_h: float, distancia_m: float, k: dict) -> float:
    if distancia_m <= 0 or delta_h is None or math.isnan(delta_h):
        return 1.0
    tan = delta_h / distancia_m
    kk = k["K_PENDIENTE_ARRIBA"] if tan >= 0 else k["K_PENDIENTE_ABAJO"]
    f = math.exp(kk * tan)
    return min(max(f, 1 / k["PENDIENTE_MAX"]), k["PENDIENTE_MAX"])


def _factor_viento(df: int, dc: int, u: float, v: float, k: dict) -> float:
    """exp(K·v·cos θ): >1 a favor del viento, <1 en contra, 1 de costado."""
    vel = math.hypot(u, v)
    if vel < 1e-6:
        return 1.0
    norma = math.hypot(dc, df) or 1
    cos_t = ((dc / norma) * u + (-df / norma) * v) / vel   # fila crece al SUR
    f = math.exp(k["K_VIENTO"] * vel * cos_t)
    return min(max(f, 1 / k["VIENTO_MAX"]), k["VIENTO_MAX"])


def humedad_equilibrio(hr: float, temp_c: float) -> float:
    """Humedad de equilibrio del combustible fino muerto (fracción).

    Simard (1968), la usada por el NFDRS de EE. UU.; HR en fracción 0–1 o en %.
    """
    h = hr * 100.0 if hr <= 1.5 else hr
    h = min(max(h, 0.0), 100.0)
    t = temp_c * 9 / 5 + 32
    if h < 10:
        emc = 0.03229 + 0.281073 * h - 0.000578 * h * t
    elif h < 50:
        emc = 2.22749 + 0.160107 * h - 0.01478 * t
    else:
        emc = 21.0606 + 0.005565 * h * h - 0.00035 * h * t - 0.483199 * h
    return max(emc, 0.5) / 100.0


def amortiguacion_humedad(m, m_ext):
    """η de Rothermel (1972). Vale 1 con combustible seco y 0 en la extinción."""
    r = np.clip(np.asarray(m, dtype=np.float64) / m_ext, 0.0, 1.0)
    eta = 1 - 2.59 * r + 5.11 * r ** 2 - 3.52 * r ** 3
    return np.clip(eta, 0.0, 1.0)


def _eta(m: float, m_ext: float) -> float:
    """Versión escalar de `amortiguacion_humedad`, para el bucle interno."""
    r = m / m_ext
    if r <= 0:
        return 1.0
    if r >= 1:
        return 0.0
    e = 1 - 2.59 * r + 5.11 * r * r - 3.52 * r * r * r
    return 0.0 if e < 0 else (1.0 if e > 1 else e)


def _viento_en_paso(serie_viento, paso: int, minutos_por_paso: float, multiplicador: float):
    if not serie_viento:
        return None
    horas = (paso * minutos_por_paso) / 60
    i0 = min(int(math.floor(horas)), len(serie_viento) - 1)
    i1 = min(i0 + 1, len(serie_viento) - 1)
    t = horas - math.floor(horas)
    a, b = serie_viento[i0], serie_viento[i1]
    return {"u": (a["u"] + (b["u"] - a["u"]) * t) * multiplicador,
            "v": (a["v"] + (b["v"] - a["v"]) * t) * multiplicador,
            "hora": a.get("hora")}


def _serie_en_paso(serie, paso: int, minutos_por_paso: float) -> Optional[dict]:
    if not serie:
        return None
    horas = (paso * minutos_por_paso) / 60
    i0 = min(int(math.floor(horas)), len(serie) - 1)
    i1 = min(i0 + 1, len(serie) - 1)
    t = horas - math.floor(horas)
    a, b = serie[i0], serie[i1]

    def mezcla(campo):
        va, vb = a.get(campo), b.get(campo)
        if va is None:
            return vb
        if vb is None:
            return va
        return va + (vb - va) * t

    return {"hora": a.get("hora"), "temperatura_c": mezcla("temperatura_c"),
            "humedad_relativa": mezcla("humedad_relativa"), "vpd_kpa": mezcla("vpd_kpa"),
            "lluvia_mm_h": a.get("precipitacion_mm") if a.get("precipitacion_mm") is not None
            else a.get("lluvia_mm_h")}


def _serie_viento_desde_ambiental(serie_ambiental) -> Optional[list]:
    """Si la serie ambiental trae viento, conviértelo al formato de serie_viento."""
    if not serie_ambiental:
        return None
    out = []
    for s in serie_ambiental:
        if s.get("u") is not None and s.get("v") is not None:
            out.append({"u": s["u"], "v": s["v"], "hora": s.get("hora")})
        elif s.get("viento_u") is not None and s.get("viento_v") is not None:
            out.append({"u": s["viento_u"], "v": s["viento_v"], "hora": s.get("hora")})
        else:
            vel = s.get("velocidad_ms", s.get("viento_ms"))
            gra = s.get("direccion_grados", s.get("viento_direccion"))
            if vel is None or gra is None:
                return None
            u, v = _componentes_viento(vel, gra)
            out.append({"u": u, "v": v, "hora": s.get("hora")})
    return out or None


def _a_datetime(x):
    if x is None:
        return None
    if isinstance(x, datetime):
        return x.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(x).replace("Z", "")[:19]).replace(tzinfo=None)
    except ValueError:
        return None


def alinear_serie(serie, inicio):
    """Recorta una serie horaria para que su primer elemento sea la hora de
    inicio de la simulación.

    El motor interpreta serie[0] como el paso 0. Las series históricas se
    descargan por DÍAS completos (desde las 00:00), así que un incendio que
    empieza a las 17:59 recibía el clima de medianoche: el ciclo día/noche
    quedaba desfasado 18 horas. Si las horas no se pueden leer, la serie se
    devuelve intacta.
    """
    t0 = _a_datetime(inicio)
    if not serie or t0 is None:
        return serie
    t0 = t0.replace(minute=0, second=0, microsecond=0)
    for i, s in enumerate(serie):
        th = _a_datetime(s.get("hora") or s.get("time") or s.get("fecha"))
        if th is None:
            return serie
        if th >= t0:
            return serie[i:] if i > 0 else serie
    return serie


def _completar_serie_viento(serie_viento):
    """Acepta series con u/v o con velocidad y dirección."""
    if not serie_viento:
        return None
    if all("u" in s and "v" in s for s in serie_viento):
        return serie_viento
    return _serie_viento_desde_ambiental(serie_viento)


def _componentes_viento(velocidad_ms: float, grados: float) -> tuple:
    rad = math.radians(grados)
    return (-velocidad_ms * math.sin(rad), -velocidad_ms * math.cos(rad))


def _grados_viento(u: float, v: float) -> float:
    return (270 - math.degrees(math.atan2(v, u)) + 360) % 360


def _humedad_efectiva(humedad_base: float, desplazamiento: float) -> float:
    """Heredada de v2; se mantiene por compatibilidad."""
    return min(max(humedad_base + desplazamiento, 0.0), 1.0)


def _construir_vecindad(radio: int, exp_distancia: float = 1.0,
                        tipo: str = "moore") -> list[dict]:
    vecinos = []
    for df in range(-radio, radio + 1):
        for dc in range(-radio, radio + 1):
            if df == 0 and dc == 0:
                continue
            if tipo == "moore":
                dentro = max(abs(df), abs(dc)) <= radio
            elif tipo == "von_neumann":
                dentro = abs(df) + abs(dc) <= radio
            elif tipo == "circular":
                dentro = math.hypot(df, dc) <= radio + 1e-9
            else:
                raise ValueError(f"VECINDAD no reconocida: {tipo!r}")
            if not dentro:
                continue
            d_celdas = math.hypot(df, dc)
            vecinos.append({"df": df, "dc": dc, "d_celdas": d_celdas,
                            "d_metros": math.hypot(df * TAM_CELDA_NS_M, dc * TAM_CELDA_EO_M),
                            "peso": 1 / (d_celdas ** exp_distancia)})
    return vecinos


# ---------------------------------------------------------------------------
def ejecutar_automata(grid_celdas: list[dict], parametros: dict, opciones: Optional[dict] = None) -> dict:
    opciones = opciones or {}

    foco_fila = parametros["foco_fila"]
    foco_columna = parametros["foco_columna"]
    p_base = parametros["p_base"]
    multiplicador_viento = parametros.get("multiplicador_viento", 1)
    delta_humedad = parametros.get("delta_humedad", 0)
    delta_temperatura_c = parametros.get("delta_temperatura_c", 0)
    num_iteraciones = parametros.get("num_iteraciones", 20)
    minutos_por_iteracion = parametros.get("minutos_por_iteracion", 15)
    dt_h = minutos_por_iteracion / 60.0
    semilla = parametros.get("semilla", 12345)
    focos_iniciales = parametros.get("focos_iniciales")

    k = {**CONSTANTES_POR_DEFECTO, **(opciones.get("constantes") or parametros.get("constantes") or {})}
    serie_ambiental = opciones.get("serie_ambiental") or parametros.get("serie_ambiental")
    serie_viento = _completar_serie_viento(opciones.get("serie_viento") or parametros.get("serie_viento"))
    if not serie_viento:
        serie_viento = _serie_viento_desde_ambiental(serie_ambiental)
    inicio_sim = opciones.get("inicio_utc") or parametros.get("inicio_utc")
    if inicio_sim:
        serie_ambiental = alinear_serie(serie_ambiental, inicio_sim)
        serie_viento = alinear_serie(serie_viento, inicio_sim)
    guion = opciones.get("guion") or parametros.get("guion") or []
    barreras_extra = opciones.get("barreras_extra")
    resistencia_extra = opciones.get("resistencia_extra") or {}
    elevacion_fuente = opciones.get("elevacion")

    estado_inicial = opciones.get("estado_inicial")
    desde_paso = int(opciones.get("desde_paso") or 0)
    hasta_paso = opciones.get("hasta_paso")
    hasta_paso = num_iteraciones if hasta_paso is None else int(hasta_paso)
    devolver_estado = bool(opciones.get("devolver_estado"))

    rand = _mulberry32(int(semilla) & 0xFFFFFFFF)
    if estado_inicial and estado_inicial.get("prng") is not None:
        rand.a = int(estado_inicial["prng"]) & 0xFFFFFFFF

    filas = max(c["fila"] for c in grid_celdas) + 1
    columnas = max(c["columna"] for c in grid_celdas) + 1
    n = filas * columnas

    def idx(f, c):
        return f * columnas + c

    pendiente_grados = np.zeros(n, dtype=np.float32)
    elevacion = np.zeros(n, dtype=np.float32)
    hay_elevacion = np.zeros(n, dtype=np.uint8)
    ndvi = np.zeros(n, dtype=np.float32)
    humedad = np.zeros(n, dtype=np.float32)
    viento_u = np.zeros(n, dtype=np.float32)
    viento_v = np.zeros(n, dtype=np.float32)
    barrera = np.zeros(n, dtype=np.uint8)
    resistencia = np.ones(n, dtype=np.float32)
    valida = np.zeros(n, dtype=np.uint8)
    celda_id: list = [None] * n
    lat_pos = np.zeros(n, dtype=np.float64)
    lon_pos = np.zeros(n, dtype=np.float64)
    suma_vel_local, n_vel, celdas_con_dem = 0.0, 0, 0

    for row in grid_celdas:
        i = idx(row["fila"], row["columna"])
        valida[i] = 1
        ndvi_val = row.get("ndvi") if row.get("ndvi") is not None else 0.3
        humedad_val = row.get("humedad") if row.get("humedad") is not None else k["HUMEDAD_SUELO_REF"]
        pendiente_grados[i] = row.get("pendiente_grados") or 0
        ndvi[i] = ndvi_val
        humedad[i] = humedad_val
        viento_u[i] = row.get("viento_u") or 0
        viento_v[i] = row.get("viento_v") or 0
        suma_vel_local += math.hypot(viento_u[i], viento_v[i])
        n_vel += 1
        celda_id[i] = row["id"]
        lat_pos[i] = row["lat"]
        lon_pos[i] = row["lon"]

        h = None
        if elevacion_fuente is not None:
            if callable(elevacion_fuente):
                h = elevacion_fuente(row)
            elif hasattr(elevacion_fuente, "get"):
                h = elevacion_fuente.get(row["id"])
        if h is None:
            h = row.get("elevacion", row.get("elevacion_m"))
        if h is not None and isinstance(h, (int, float)) and math.isfinite(h):
            elevacion[i] = h
            hay_elevacion[i] = 1
            celdas_con_dem += 1

        es_barrera_osm = bool(barreras_extra and row["id"] in barreras_extra)
        barrera[i] = 1 if (ndvi_val < k["NDVI_BARRERA"] or es_barrera_osm) else 0
        r_osm = resistencia_extra.get(row["id"])
        if r_osm is not None and 0.0 <= r_osm < 1.0:
            resistencia[i] = 1.0 - k["K_BARRERA"] * (1.0 - r_osm)

    vel_media_local = (suma_vel_local / n_vel) if n_vel else 1.0
    u_medio = float(viento_u[valida == 1].mean()) if n_vel else 0.0
    v_medio = float(viento_v[valida == 1].mean()) if n_vel else 0.0

    # Vegetación con techo: 0,05 … 1
    f_veg = np.clip((ndvi - k["NDVI_BARRERA"]) / max(k["NDVI_SATURACION"] - k["NDVI_BARRERA"], 1e-6),
                    0.05, 1.0).astype(np.float32)
    # Ajuste espacial de humedad del combustible por la humedad del suelo
    ajuste_suelo = (k["K_HUMEDAD"] * (humedad - k["HUMEDAD_SUELO_REF"])).astype(np.float64)

    # Heterogeneidad fija por celda, de un PRNG propio: no depende del tramo
    # ni del checkpoint, así que una corrida por tramos da lo mismo que entera.
    rng_celda = _mulberry32((int(semilla) * 2654435761 + 97) & 0xFFFFFFFF)
    heter = np.ones(n, dtype=np.float32)
    s_c = k["RUIDO_CELDA"]
    if s_c > 0:
        for i in range(n):
            if valida[i]:
                heter[i] = math.exp(s_c * _normal(rng_celda) - s_c * s_c / 2)

    vecindad = _construir_vecindad(k["RADIO_VECINDAD"], k["EXP_DISTANCIA"], k.get("VECINDAD", "moore"))
    ros_base = p_base * k["ROS_REFERENCIA_M_H"]

    estados = np.full(n, INERTE, dtype=np.uint8)
    for i in range(n):
        if valida[i]:
            estados[i] = INERTE if barrera[i] else SIN_QUEMAR
    pasos_ardiendo = np.zeros(n, dtype=np.uint16)
    horas_detenida = np.zeros(n, dtype=np.float32)
    progreso: dict[int, float] = {}

    i_foco = idx(foco_fila, foco_columna)
    if not valida[i_foco]:
        raise ValueError(f"La celda foco (fila={foco_fila}, columna={foco_columna}) no pertenece al grid.")
    estados[i_foco] = ARDIENDO
    indices_iniciales = [i_foco]
    if isinstance(focos_iniciales, list):
        for fo in focos_iniciales:
            kk = idx(fo["fila"], fo["columna"])
            if kk != i_foco and 0 <= kk < n and valida[kk] and not barrera[kk] and estados[kk] == SIN_QUEMAR:
                estados[kk] = ARDIENDO
                indices_iniciales.append(kk)

    ahora = datetime.now(timezone.utc)
    eventos_spotting, apagadas_por_lluvia, apagadas_estancadas = [], [], []
    frente = list(indices_iniciales)
    tocadas = list(indices_iniciales)
    solo_conteo = bool(opciones.get("solo_conteo"))

    if estado_inicial:
        if estado_inicial.get("estados") is not None:
            estados = np.array(estado_inicial["estados"], dtype=np.uint8)
        if estado_inicial.get("pasos_ardiendo") is not None:
            pasos_ardiendo = np.array(estado_inicial["pasos_ardiendo"], dtype=np.uint16)
        if estado_inicial.get("horas_detenida") is not None:
            horas_detenida = np.array(estado_inicial["horas_detenida"], dtype=np.float32)
        if estado_inicial.get("progreso") is not None:
            progreso = {int(a): float(b) for a, b in estado_inicial["progreso"]}
        if estado_inicial.get("frente") is not None:
            frente = [int(i) for i in estado_inicial["frente"]]
        if estado_inicial.get("tocadas") is not None:
            tocadas = [int(i) for i in estado_inicial["tocadas"]]
        if estado_inicial.get("hora_inicio"):
            ahora = datetime.fromisoformat(estado_inicial["hora_inicio"])

    # --- Ambiente por paso --------------------------------------------------
    def ambiente_en(num_paso: int) -> dict:
        amb_serie = _serie_en_paso(serie_ambiental, num_paso, minutos_por_iteracion)
        viento_serie = _viento_en_paso(serie_viento, num_paso, minutos_por_iteracion, multiplicador_viento)
        if viento_serie:
            vel = math.hypot(viento_serie["u"], viento_serie["v"])
            grados = _grados_viento(viento_serie["u"], viento_serie["v"])
        else:
            vel = vel_media_local * multiplicador_viento
            grados = _grados_viento(u_medio, v_medio) if (u_medio or v_medio) else 0.0

        base = {"temperatura_c": (amb_serie or {}).get("temperatura_c"),
                "vpd_kpa": (amb_serie or {}).get("vpd_kpa"),
                "lluvia_mm_h": (amb_serie or {}).get("lluvia_mm_h") or 0.0,
                "viento_ms": vel, "viento_grados": grados}
        if base["temperatura_c"] is None:
            base["temperatura_c"] = k["T_REFERENCIA_C"]
        amb = _escenarios.ambiente_en_paso(base, guion, num_paso) if guion else {
            **base, "humedad_delta": 0.0, "eventos_activos": []}

        hr = (amb_serie or {}).get("humedad_relativa")
        if hr is None:
            hr = k["HR_REFERENCIA"]
        temp = (amb.get("temperatura_c") or k["T_REFERENCIA_C"]) + delta_temperatura_c
        emc = humedad_equilibrio(hr, temp)
        desplazamiento = delta_humedad + amb.get("humedad_delta", 0.0)
        if base.get("vpd_kpa") is not None and amb.get("vpd_kpa") is not None:
            desplazamiento -= k["K_VPD"] * (amb["vpd_kpa"] - base["vpd_kpa"])

        if viento_serie and amb.get("viento_grados") is not None:
            u, v = _componentes_viento(amb["viento_ms"] or 0.0, amb["viento_grados"])
            viento_efectivo = {"u": u, "v": v, "hora": viento_serie.get("hora")}
        elif amb.get("eventos_activos"):
            u, v = _componentes_viento(amb["viento_ms"] or 0.0, amb["viento_grados"] or 0.0)
            viento_efectivo = {"u": u, "v": v, "hora": None}
        else:
            viento_efectivo = None

        return {"viento": viento_efectivo, "viento_ms_informado": amb.get("viento_ms"),
                "viento_grados_informado": amb.get("viento_grados"),
                "emc": emc, "desplazamiento_humedad": desplazamiento,
                "humedad_relativa": hr,
                "lluvia_mm_h": max(amb.get("lluvia_mm_h") or 0.0, 0.0),
                "temperatura_c": temp, "vpd_kpa": amb.get("vpd_kpa"),
                "eventos_activos": amb.get("eventos_activos", [])}

    def construir_iteracion(num_iter, grid, amb):
        viento_usado = amb.get("viento") if amb else None
        ardiendo = quemadas = 0
        if solo_conteo:
            for i in tocadas:
                if grid[i] == ARDIENDO:
                    ardiendo += 1
                elif grid[i] == QUEMADO:
                    quemadas += 1
            return {"iteracion": num_iter, "celdas": [], "num_celdas_ardiendo": ardiendo,
                    "num_celdas_quemadas": quemadas}
        celdas = []
        for i in dict.fromkeys(tocadas):
            e = int(grid[i])
            if e == ARDIENDO:
                ardiendo += 1
            elif e == QUEMADO:
                quemadas += 1
            else:
                continue
            celdas.append({"celda_id": celda_id[i], "lat": float(lat_pos[i]), "lon": float(lon_pos[i]),
                           "estado": _ESTADO_API[e]})
        viento_obj = None
        if viento_usado:
            vel = math.hypot(viento_usado["u"], viento_usado["v"])
            viento_obj = {"velocidad_ms": round(vel, 2),
                          "direccion_grados": round(_grados_viento(viento_usado["u"], viento_usado["v"])),
                          "hora": viento_usado.get("hora")}
        m_med = amb["emc"] + amb["desplazamiento_humedad"]
        return {
            "iteracion": num_iter,
            "timestamp_simulado": (ahora + timedelta(minutes=minutos_por_iteracion * num_iter)).isoformat(),
            "celdas": celdas, "num_celdas_ardiendo": ardiendo, "num_celdas_quemadas": quemadas,
            "viento": viento_obj,
            "ambiente": {
                "temperatura_c": round(amb["temperatura_c"], 1) if amb.get("temperatura_c") is not None else None,
                # En v3 «humedad» es la del combustible fino (fracción)
                "humedad": round(min(max(m_med, 0.0), 1.0), 3),
                "humedad_relativa": round(amb["humedad_relativa"], 3) if amb.get("humedad_relativa") is not None else None,
                "vpd_kpa": round(amb["vpd_kpa"], 2) if amb.get("vpd_kpa") is not None else None,
                "lluvia_mm_h": round(amb.get("lluvia_mm_h") or 0.0, 2),
                "viento_ms": round(amb["viento_ms_informado"], 2) if amb.get("viento_ms_informado") is not None else None,
                "viento_grados": round(amb["viento_grados_informado"]) if amb.get("viento_grados_informado") is not None else None,
                "eventos_activos": amb.get("eventos_activos", []),
            },
        }

    mx = k["HUMEDAD_EXTINCION"]
    ajuste_suelo = ajuste_suelo.tolist()
    n_max_pasos = max(1, int(round(k["T_MAX_ARDIENDO_H"] / dt_h)))
    s_p = k["RUIDO_PASO"]

    def paso(grid, num_paso):
        nonlocal frente
        nuevo = grid.copy()
        amb = ambiente_en(num_paso)
        viento_global = amb["viento"]
        lluvia = amb["lluvia_mm_h"]
        f_lluvia = 1.0 / (1.0 + k["K_LLUVIA_PROP"] * lluvia) if lluvia > 0 else 1.0
        m_base = amb["emc"] + amb["desplazamiento_humedad"]
        siguiente_frente = []

        for i in frente:
            if grid[i] != ARDIENDO or nuevo[i] != ARDIENDO:
                continue
            f = i // columnas
            c = i - f * columnas
            pasos_ardiendo[i] += 1
            eta_i = _eta(m_base + ajuste_suelo[i], mx)

            if lluvia > 0:
                intensidad_i = float(f_veg[i]) * eta_i
                p_apagar = k["K_LLUVIA_EXT"] * lluvia * max(1 - intensidad_i, 0)
                if rand() < min(p_apagar, 1.0):
                    nuevo[i] = QUEMADO
                    apagadas_por_lluvia.append({"iteracion": num_paso, "celda": int(i)})
                    continue

            if viento_global:
                escala_local = (math.hypot(viento_u[i], viento_v[i]) / vel_media_local) if vel_media_local > 1e-6 else 1
                esc = 0.6 + 0.4 * min(escala_local, 2)
                u, v = viento_global["u"] * esc, viento_global["v"] * esc
            else:
                u, v = viento_u[i] * multiplicador_viento, viento_v[i] * multiplicador_viento
            vel_viento = math.hypot(u, v)

            pendientes = 0
            ros_max = 0.0
            ruido = math.exp(s_p * _normal(rand) - s_p * s_p / 2) if s_p > 0 else 1.0
            for vec in vecindad:
                nf, nc = f + vec["df"], c + vec["dc"]
                if nf < 0 or nf >= filas or nc < 0 or nc >= columnas:
                    continue
                j = nf * columnas + nc
                if grid[j] != SIN_QUEMAR or nuevo[j] != SIN_QUEMAR or barrera[j]:
                    continue
                pendientes += 1
                if hay_elevacion[i] and hay_elevacion[j]:
                    f_pend = _factor_pendiente(float(elevacion[j] - elevacion[i]), vec["d_metros"], k)
                else:
                    f_pend = 1 + 0.03 * pendiente_grados[j]
                eta_j = _eta(m_base + ajuste_suelo[j], mx)
                ros = (ros_base * _factor_viento(vec["df"], vec["dc"], u, v, k) * f_pend
                       * float(f_veg[j]) * eta_j * f_lluvia * float(resistencia[j]) * float(heter[j]) * ruido)
                if ros > ros_max:
                    ros_max = ros
                clave = i * n + j
                avance = progreso.get(clave, 0.0) + ros * dt_h
                if avance >= vec["d_metros"]:
                    nuevo[j] = ARDIENDO
                    siguiente_frente.append(j)
                    tocadas.append(j)
                    progreso.pop(clave, None)
                    pendientes -= 1
                else:
                    progreso[clave] = avance

            # --- Fin de la combustión de la celda -------------------------
            if pendientes <= 0:
                nuevo[i] = QUEMADO                       # no le queda nada que quemar
            elif pasos_ardiendo[i] >= n_max_pasos:
                nuevo[i] = QUEMADO                       # combustible agotado
            else:
                if ros_max < k["ROS_MIN_M_H"]:
                    horas_detenida[i] += dt_h
                    if horas_detenida[i] >= k["T_ESTANCAMIENTO_H"]:
                        nuevo[i] = QUEMADO               # frente detenido: se apaga
                        apagadas_estancadas.append({"iteracion": num_paso, "celda": int(i)})
                else:
                    horas_detenida[i] = 0.0

            # --- Saltos de pavesas ------------------------------------------
            if k["SPOTTING_ACTIVO"] and vel_viento >= k["SPOTTING_VIENTO_MIN"] and nuevo[i] == ARDIENDO:
                intensidad = float(f_veg[i]) * eta_i
                p_salto = k["SPOTTING_PROB"] * intensidad * min(vel_viento / 3, 3) * f_lluvia
                if rand() < p_salto:
                    ang = math.atan2(v, u) + (rand() - 0.5) * 2 * k["SPOTTING_DISPERSION"]
                    alcance = k["SPOTTING_DIST_MIN"] + rand() * (k["SPOTTING_DIST_MAX"] - k["SPOTTING_DIST_MIN"]) \
                        * min(vel_viento / 5, 1)
                    sf = f - round(math.sin(ang) * alcance)
                    sc = c + round(math.cos(ang) * alcance)
                    if 0 <= sf < filas and 0 <= sc < columnas:
                        s = sf * columnas + sc
                        if valida[s] and not barrera[s] and grid[s] == SIN_QUEMAR and nuevo[s] == SIN_QUEMAR:
                            eta_s = _eta(m_base + ajuste_suelo[s], mx)
                            if eta_s > 0:                 # una brasa no prende combustible saturado
                                nuevo[s] = ARDIENDO
                                siguiente_frente.append(s)
                                tocadas.append(s)
                                eventos_spotting.append({
                                    "iteracion": num_paso, "origen": {"fila": f, "columna": c},
                                    "destino": {"fila": sf, "columna": sc, "lat": float(lat_pos[s]),
                                                "lon": float(lon_pos[s])},
                                    "distancia_m": round(alcance * TAM_CELDA_NS_M)})

            if nuevo[i] == ARDIENDO:
                siguiente_frente.append(i)

        frente = siguiente_frente
        return nuevo, amb

    amb_inicial = ambiente_en(desde_paso)
    iteraciones = [construir_iteracion(desde_paso, estados, amb_inicial)]
    limite_celdas = opciones.get("limite_celdas")
    interrumpido_por = None
    for t in range(desde_paso + 1, hasta_paso + 1):
        estados, amb_usado = paso(estados, t)
        iteraciones.append(construir_iteracion(t, estados, amb_usado))
        if iteraciones[-1]["num_celdas_ardiendo"] == 0:
            interrumpido_por = "extinguido"
            break
        if limite_celdas and len(set(tocadas)) > limite_celdas:
            interrumpido_por = "limite_celdas"
            break

    ultimo_paso = iteraciones[-1]["iteracion"]
    metadatos = {
        "modelo": MODELO, "constantes": k, "minutos_por_iteracion": minutos_por_iteracion,
        "semilla": semilla, "celdas_con_dem": celdas_con_dem, "usa_dem": celdas_con_dem > 0,
        "usa_serie_viento": bool(serie_viento), "usa_serie_ambiental": bool(serie_ambiental),
        "serie_ambiental_desde": (serie_ambiental[0].get("hora") if serie_ambiental else None),
        "serie_viento_desde": (serie_viento[0].get("hora") if serie_viento else None),
        "barreras_osm": len(barreras_extra) if barreras_extra else 0,
        "eventos_spotting": eventos_spotting,
        "desde_paso": desde_paso, "ultimo_paso": ultimo_paso,
        "completado": ultimo_paso >= num_iteraciones, "interrumpido_por": interrumpido_por,
        "guion": guion, "celdas_apagadas_por_lluvia": len(apagadas_por_lluvia),
        "celdas_apagadas_por_estancamiento": len(apagadas_estancadas),
    }
    if devolver_estado:
        metadatos["estado"] = {
            "paso": ultimo_paso, "prng": rand.a,
            "estados": estados.tolist(), "pasos_ardiendo": pasos_ardiendo.tolist(),
            "horas_detenida": horas_detenida.tolist(),
            "progreso": [[int(a), float(b)] for a, b in progreso.items()],
            "frente": [int(i) for i in frente], "tocadas": [int(i) for i in dict.fromkeys(tocadas)],
            "hora_inicio": ahora.isoformat(),
        }
    if solo_conteo:
        metadatos["celdas_quemadas_ids"] = [celda_id[i] for i in dict.fromkeys(tocadas)
                                            if estados[i] in (QUEMADO, ARDIENDO)]
    return {"iteraciones": iteraciones, "metadatos": metadatos}


def perimetro_simulado(grid_celdas: list[dict], parametros: dict, opciones: Optional[dict] = None) -> set:
    opciones = dict(opciones or {})
    opciones["solo_conteo"] = True
    resultado = ejecutar_automata(grid_celdas, parametros, opciones)
    return set(resultado["metadatos"]["celdas_quemadas_ids"])
