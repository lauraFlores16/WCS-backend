"""
============================================================================
AUTÓMATA CELULAR v2 — motor de propagación de incendios
============================================================================
Puerto fiel a Python de `backend/motor/automata.js` (motor v2 del prototipo
Node/Express). Es la pieza que pediste mover al backend Python: aquí vive el
cómputo del incendio, celda a celda, iteración a iteración.

  1. PENDIENTE REAL ENTRE CELDAS: el factor de pendiente usa la diferencia de
     altura entre la celda que arde y la vecina (Δh / distancia), no la
     inclinación de la celda destino. Uphill acelera, downhill frena, como en
     la formulación de Rothermel.
  2. VIENTO VARIABLE EN EL TIEMPO: acepta una serie meteorológica horaria
     (Open-Meteo) y la interpola al paso de simulación.
  3. TIEMPO DE RESIDENCIA: una celda arde varios pasos antes de pasar a
     quemada; su intensidad decae a lo largo de la combustión.
  4. SPOTTING: saltos de pavesas a distancia, con probabilidad y alcance
     dependientes de la intensidad del frente y de la velocidad del viento.
  5. CONSTANTES K CALIBRABLES (ver motor/calibracion.py).
  6. VECINDAD ADAPTATIVA CON PESOS POR DISTANCIA: radio configurable y peso
     1/d — la diagonal no propaga igual que la ortogonal.

Extras: PRNG con semilla (reproducible, imprescindible para calibrar) y
barreras externas (ríos, quebradas, caminos) inyectadas desde OSM.

Estados: 0=sin_quemar, 1=ardiendo, 2=quemado, 3=inerte
============================================================================
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import numpy as np

from . import escenarios as _escenarios

SIN_QUEMAR, ARDIENDO, QUEMADO, INERTE = 0, 1, 2, 3
_ESTADO_API = {ARDIENDO: "ardiendo", QUEMADO: "quemada"}

# Geometría del grid de Apolo: celdas de 0.0045° ≈ 500 m (25 ha).
TAM_CELDA_NS_M = 500.0  # norte-sur (Δfila)
TAM_CELDA_EO_M = 484.0  # este-oeste (Δcolumna, corregido por cos(lat))

CONSTANTES_POR_DEFECTO = {
    # Pendiente (Δh entre celdas)
    "K_PENDIENTE_ARRIBA": 3.0,
    "K_PENDIENTE_ABAJO": 1.2,
    "PENDIENTE_MAX": 4.0,
    # Viento
    "K_VIENTO": 0.15,
    "EXP_VIENTO": 1.0,
    # Temperatura / humedad
    "K_TEMPERATURA": 0.01,
    "K_HUMEDAD": 0.5,
    # Vegetación
    "NDVI_BARRERA": 0.10,
    "NDVI_BASE": 0.5,
    # Tiempo de residencia (en pasos de simulación; 1 paso = 15 min)
    "RESIDENCIA_MIN": 3,
    "RESIDENCIA_MAX": 6,
    "DECAIMIENTO_FASE": 0.25,
    # Spotting (saltos de pavesas)
    "SPOTTING_ACTIVO": True,
    "SPOTTING_PROB": 0.015,
    "SPOTTING_VIENTO_MIN": 1.0,
    "SPOTTING_DIST_MIN": 2,
    "SPOTTING_DIST_MAX": 6,
    "SPOTTING_DISPERSION": 0.35,
    # --- Barreras parciales del terreno --------------------------------------
    # Cuánto se cree el modelo la resistencia que trae la capa de OpenStreetMap
    # (servicios/terreno.py · RESISTENCIA). Antes esa resistencia se calculaba
    # y se tiraba: el autómata solo leía el conjunto binario `barreras_extra`,
    # y con el umbral de 0,05 ahí solo entraban ríos y cuerpos de agua. Un
    # camino con resistencia 0,6 y una quebrada con 0,45 no hacían NADA.
    #
    #   f_barrera = 1 − K_BARRERA · (1 − resistencia_destino)
    #
    # K_BARRERA = 1  → la resistencia se aplica entera (camino frena un 40 %)
    # K_BARRERA = 0  → se ignora, comportamiento anterior
    #
    # Es calibrable a propósito: cuánto frena de verdad una carretera de tierra
    # a escala de 500 m es justo el tipo de cosa que no se sabe a priori y que
    # los perímetros reales sí pueden decir.
    "K_BARRERA": 1.0,

    # Vecindad
    # "moore" = los 8 vecinos del 3x3 (Chebyshov). Es lo que el proyecto
    # declara en su metodología y lo que el código original pretendía: el
    # comentario del peso ya hablaba de "diagonal 0.707". Hasta esta
    # corrección el filtro era euclídeo y dejaba solo 4 vecinos.
    # Alternativas para análisis de sensibilidad: "von_neumann", "circular".
    "VECINDAD": "moore",
    "RADIO_VECINDAD": 1,
    "EXP_DISTANCIA": 1.0,

    # --- Lluvia (canal nuevo) -----------------------------------------------
    # Antes de esto la lluvia no existía en el motor: `delta_humedad` era lo
    # único que la representaba, y aun en su extremo (+0.25, muy por encima del
    # máximo observado en el grid) solo restaba un 15 % a la propagación. Peor:
    # una celda encendida NO se apagaba nunca por causa del tiempo, así que
    # «llega la tormenta» no podía terminar un incendio, que es justo lo que
    # pasa de verdad en Apolo al entrar la temporada de lluvias.
    #
    #   f_lluvia = 1 / (1 + K_LLUVIA_PROP · mm_h)     frena la propagación
    #
    # Saturante a propósito: doblar la lluvia no dobla el efecto. A 2 mm/h
    # quedan ~0.71 (−29 %) y a 10 mm/h ~0.33 (−67 %).
    "K_LLUVIA_PROP": 0.20,
    # Probabilidad por paso de que una celda ardiendo se apague:
    #   p = K_LLUVIA_EXT · mm_h · (1 − intensidad)
    # El (1 − intensidad) es la parte que importa: un frente vivo aguanta el
    # agua mucho mejor que unos rescoldos. Sin ese término la lluvia apagaría
    # el incendio de golpe y entero, que no es lo que se observa.
    "K_LLUVIA_EXT": 0.02,

    # --- Secado por déficit de presión de vapor -----------------------------
    # El VPD es el que de verdad seca el combustible fino; la temperatura sola
    # es un sustituto pobre (a +10 °C, K_TEMPERATURA=0.01 solo mueve la
    # propagación un +6 %). Open-Meteo ya descarga `vapour_pressure_deficit`
    # para Apolo en `servicios/meteo.py` — estaba ahí sin usarse.
    #
    #   secado = K_VPD · (vpd_kPa − VPD_REFERENCIA)     resta humedad
    "K_VPD": 0.04,
    "VPD_REFERENCIA": 1.0,
}


# ---------------------------------------------------------------------------
# PRNG determinista (mulberry32). Misma semilla → misma corrida. Puerto
# directo del generador que usaba el motor JS, para que la propagación sea
# reproducible aquí también.
# ---------------------------------------------------------------------------
class _Mulberry32:
    """Generador con estado LEGIBLE Y RESTAURABLE.

    Antes era un closure sobre `a`, que servía mientras la corrida fuera de
    un tirón. Para poder pausar el autómata en el paso 12, inyectar una
    tormenta y seguir, hay que poder guardar y reponer el punto exacto del
    generador: si no, al reanudar saldrían números distintos y la corrida
    dejaría de ser reproducible —justo lo que la calibración necesita—.

    Mulberry32 lo pone fácil: todo su estado es UN entero de 32 bits, así que
    el checkpoint cabe en el JSON de la sesión sin más.
    """

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


def _factor_pendiente(delta_h: float, distancia_m: float, k: dict) -> float:
    if distancia_m <= 0 or delta_h is None or math.isnan(delta_h):
        return 1.0
    tan = delta_h / distancia_m
    kk = k["K_PENDIENTE_ARRIBA"] if tan >= 0 else k["K_PENDIENTE_ABAJO"]
    f = math.exp(kk * tan)
    return min(max(f, 1 / k["PENDIENTE_MAX"]), k["PENDIENTE_MAX"])


def _factor_viento(df: int, dc: int, u: float, v: float, k: dict) -> float:
    vel = math.hypot(u, v)
    if vel < 1e-6:
        return 1.0
    norma = math.hypot(dc, df) or 1
    dot = ((dc / norma) * u + (-df / norma) * v) / vel
    return 1.0 + k["K_VIENTO"] * (vel ** k["EXP_VIENTO"]) * max(0.0, dot)


def _viento_en_paso(serie_viento, paso: int, minutos_por_paso: float, multiplicador: float):
    if not serie_viento:
        return None
    horas = (paso * minutos_por_paso) / 60
    i0 = min(int(math.floor(horas)), len(serie_viento) - 1)
    i1 = min(i0 + 1, len(serie_viento) - 1)
    t = horas - math.floor(horas)
    a, b = serie_viento[i0], serie_viento[i1]
    return {
        "u": (a["u"] + (b["u"] - a["u"]) * t) * multiplicador,
        "v": (a["v"] + (b["v"] - a["v"]) * t) * multiplicador,
        "hora": a.get("hora"),
    }


def _serie_en_paso(serie, paso: int, minutos_por_paso: float) -> Optional[dict]:
    """Interpola una serie HORARIA al paso de simulación (15 min por defecto).

    Mismo criterio que `_viento_en_paso`, pero para las variables ambientales
    escalares (temperatura, humedad relativa, VPD, lluvia) que Open-Meteo ya
    descarga en `servicios/meteo.py` y que hasta ahora no llegaban al motor:
    de todo el pronóstico solo se usaba el viento.
    """
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

    return {
        "hora": a.get("hora"),
        "temperatura_c": mezcla("temperatura_c"),
        "humedad_relativa": mezcla("humedad_relativa"),
        "vpd_kpa": mezcla("vpd_kpa"),
        # La lluvia NO se interpola: el dato horario es el acumulado de esa
        # hora, así que promediarlo con la siguiente inventaría precipitación
        # antes de que empiece a llover.
        "lluvia_mm_h": a.get("precipitacion_mm") if a.get("precipitacion_mm") is not None
                       else a.get("lluvia_mm_h"),
    }


def _componentes_viento(velocidad_ms: float, grados: float) -> tuple:
    """De (velocidad, dirección de procedencia) a componentes u/v.

    Misma convención que `servicios/meteo.direccion_a_componentes`: los grados
    dicen de dónde VIENE el viento, y u/v apuntan hacia donde va.
    """
    rad = math.radians(grados)
    return (-velocidad_ms * math.sin(rad), -velocidad_ms * math.cos(rad))


def _grados_viento(u: float, v: float) -> float:
    """Dirección de procedencia, en grados, a partir de u/v."""
    return (270 - math.degrees(math.atan2(v, u)) + 360) % 360


def _humedad_efectiva(humedad_base: float, desplazamiento: float) -> float:
    """Humedad del combustible en este paso, acotada a [0, 1].

    Separar la base (del grid, fija) del desplazamiento (del clima, variable)
    es lo que permite que un escenario cambie el ambiente a mitad de corrida.
    Sin escenarios el desplazamiento es constante y el resultado es idéntico
    al que daba hornearlo en el array al arrancar.
    """
    return min(max(humedad_base + desplazamiento, 0.0), 1.0)


def _construir_vecindad(radio: int, exp_distancia: float,
                        tipo: str = "moore") -> list[dict]:
    """Vecinos a los que una celda ardiendo puede propagar el fuego.

    CORRECCIÓN — antes esto devolvía 4 vecinos, no 8
    ------------------------------------------------
    El filtro era `math.hypot(df, dc) > radio`, o sea distancia EUCLÍDEA. Con
    radio 1 una diagonal está a hypot(1,1) = 1,4142 > 1, así que quedaba fuera:
    lo que salía era una vecindad de von Neumann de 4 vecinos.

    Que era un fallo y no una decisión lo dice el propio código original
    (backend/motor/automata.js), que se contradice consigo mismo:

        línea  76   RADIO_VECINDAD: 1,  // 1 = Moore 3x3
        línea 153   peso: 1/d^exp,      // ortogonal 1.0 · diagonal 0.707
        línea 149   if (dCeldas > radio) continue;  // vecindad circular

    Las dos primeras describen Moore y un peso diagonal de 0,707. La tercera
    elimina las diagonales, con lo cual ese 0,707 no podía darse nunca y el
    término de peso por distancia quedaba inerte: los cuatro vecinos
    ortogonales pesan todos 1,0.

    Ahora el tipo de vecindad es explícito:

        "moore"        distancia de Chebyshov, max(|df|, |dc|) <= radio
                       radio 1 →  8 vecinos   ← el que usa el proyecto
                       radio 2 → 24 vecinos
        "von_neumann"  distancia de Manhattan, |df| + |dc| <= radio
                       radio 1 →  4 vecinos
        "circular"     distancia euclídea (el comportamiento anterior)
                       radio 1 →  4 vecinos · radio 2 → 12

    Se conservan las tres para poder comparar y documentar el efecto del
    cambio, no para elegir la que dé mejores métricas.

    El peso 1/d^exp sí hace algo ahora: con Moore, la diagonal está a 1,4142
    celdas y pesa 0,707 frente al 1,0 de las ortogonales. Sin ese término el
    fuego avanzaría en cuadrados perfectos, porque la diagonal cubre más
    terreno por paso que la ortogonal.
    """
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
                raise ValueError(
                    f"VECINDAD no reconocida: {tipo!r}. "
                    "Usa 'moore', 'von_neumann' o 'circular'.")
            if not dentro:
                continue

            d_celdas = math.hypot(df, dc)
            d_metros = math.hypot(df * TAM_CELDA_NS_M, dc * TAM_CELDA_EO_M)
            vecinos.append({"df": df, "dc": dc, "d_celdas": d_celdas, "d_metros": d_metros,
                             "peso": 1 / (d_celdas ** exp_distancia)})
    return vecinos


def ejecutar_automata(grid_celdas: list[dict], parametros: dict, opciones: Optional[dict] = None) -> dict:
    """Ejecuta la simulación, entera o por tramos.

    Devuelve {"iteraciones": [...], "metadatos": {...}} — en JS los metadatos
    viajaban como una propiedad extra colgada del array; en Python se separan
    en dos claves explícitas.

    Ejecución por tramos (consola interactiva)
    ------------------------------------------
    `opciones` admite además:

        guion            lista de eventos de `motor/escenarios.py`
        serie_ambiental  pronóstico horario (T, HR, VPD, lluvia) de Open-Meteo
        desde_paso       en qué paso empieza este tramo (por defecto 0)
        hasta_paso       en qué paso para (por defecto num_iteraciones)
        estado_inicial   checkpoint devuelto por una llamada anterior
        devolver_estado  si True, incluye el checkpoint en los metadatos

    Un tramo 0→12 seguido de otro 12→40 partiendo de su checkpoint da
    EXACTAMENTE el mismo resultado que una corrida 0→40 con el mismo guion.
    Es lo que hace `pruebas/prueba_escenarios.py`, y es la propiedad que
    permite pausar, inyectar una tormenta y seguir sin perder la
    reproducibilidad por semilla que necesita la calibración.
    """
    opciones = opciones or {}

    foco_fila = parametros["foco_fila"]
    foco_columna = parametros["foco_columna"]
    p_base = parametros["p_base"]
    multiplicador_viento = parametros.get("multiplicador_viento", 1)
    delta_humedad = parametros.get("delta_humedad", 0)
    delta_temperatura_c = parametros.get("delta_temperatura_c", 0)
    num_iteraciones = parametros.get("num_iteraciones", 20)
    minutos_por_iteracion = parametros.get("minutos_por_iteracion", 15)
    semilla = parametros.get("semilla", 12345)
    focos_iniciales = parametros.get("focos_iniciales")

    k = {**CONSTANTES_POR_DEFECTO, **(opciones.get("constantes") or parametros.get("constantes") or {})}
    serie_viento = opciones.get("serie_viento") or parametros.get("serie_viento")
    serie_ambiental = opciones.get("serie_ambiental") or parametros.get("serie_ambiental")
    guion = opciones.get("guion") or parametros.get("guion") or []
    barreras_extra = opciones.get("barreras_extra")
    # Resistencia parcial por celda: {id_celda: 0..1}. 1 = terreno normal.
    # Convive con `barreras_extra`, que sigue siendo el corte duro (INERTE).
    resistencia_extra = opciones.get("resistencia_extra") or {}
    elevacion_fuente = opciones.get("elevacion")

    # --- Tramo a ejecutar y checkpoint de partida ---------------------------
    estado_inicial = opciones.get("estado_inicial")
    desde_paso = int(opciones.get("desde_paso") or 0)
    hasta_paso = opciones.get("hasta_paso")
    hasta_paso = num_iteraciones if hasta_paso is None else int(hasta_paso)
    devolver_estado = bool(opciones.get("devolver_estado"))

    rand = _mulberry32(int(semilla) & 0xFFFFFFFF)
    if estado_inicial and estado_inicial.get("prng") is not None:
        # Reponer el punto exacto del generador. Sin esto, reanudar daría una
        # secuencia distinta y dos corridas con la misma semilla dejarían de
        # coincidir.
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
    # 1.0 = sin resistencia. Se rellena desde la capa OSM cuando hay.
    resistencia = np.ones(n, dtype=np.float32)
    valida = np.zeros(n, dtype=np.uint8)
    celda_id: list = [None] * n
    lat_pos = np.zeros(n, dtype=np.float64)
    lon_pos = np.zeros(n, dtype=np.float64)

    ajuste_humedad_temp = k["K_TEMPERATURA"] * delta_temperatura_c
    suma_vel_local = 0.0
    n_vel = 0
    celdas_con_dem = 0

    for row in grid_celdas:
        i = idx(row["fila"], row["columna"])
        valida[i] = 1
        ndvi_val = row.get("ndvi") if row.get("ndvi") is not None else 0.3
        humedad_val = row.get("humedad") if row.get("humedad") is not None else 0.3
        pendiente_grados[i] = row.get("pendiente_grados") or 0
        ndvi[i] = ndvi_val
        # La humedad se guarda CRUDA, tal como viene del grid. Antes aquí se
        # horneaban `delta_humedad` y el secado por temperatura, y esa decisión
        # es la que impedía que el clima cambiara a mitad de corrida: una vez
        # sumados al array ya no había forma de volver atrás. Ahora los deltas
        # viajan como un desplazamiento por paso (`_humedad_efectiva`), que sin
        # escenarios vale exactamente lo mismo que antes.
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
            else:
                h = elevacion_fuente.get(row["id"]) if isinstance(elevacion_fuente, dict) else None
        if h is None and row.get("elevacion") is not None:
            h = row["elevacion"]
        if h is not None and math.isfinite(h):
            elevacion[i] = h
            hay_elevacion[i] = 1
            celdas_con_dem += 1

        es_barrera_osm = bool(barreras_extra and row["id"] in barreras_extra)
        barrera[i] = 1 if (ndvi_val < k["NDVI_BARRERA"] or es_barrera_osm) else 0

        # Resistencia parcial: caminos, quebradas y roca desnuda. No convierten
        # la celda en INERTE —el fuego puede cruzar una carretera— pero le
        # cuesta más. `K_BARRERA` gradúa cuánto se cree el modelo esa capa.
        r_osm = resistencia_extra.get(row["id"])
        if r_osm is not None and 0.0 <= r_osm < 1.0:
            resistencia[i] = 1.0 - k["K_BARRERA"] * (1.0 - r_osm)

    vel_media_local = (suma_vel_local / n_vel) if n_vel else 1.0
    # Viento medio del grid como vector: da la dirección de partida sobre la
    # que operan los escenarios cuando no hay serie de pronóstico.
    u_medio = float(viento_u[valida == 1].mean()) if n_vel else 0.0
    v_medio = float(viento_v[valida == 1].mean()) if n_vel else 0.0
    # Humedad representativa del terreno, para poder informar en cada iteración
    # de cómo va la humedad del combustible sin recorrer las 36.390 celdas.
    humedad_mediana = float(np.median(humedad[valida == 1])) if n_vel else 0.3

    residencia = np.zeros(n, dtype=np.uint8)
    for i in range(n):
        if not valida[i]:
            continue
        carga = min(max(ndvi[i] / 0.8, 0), 1)
        r = k["RESIDENCIA_MIN"] + carga * (k["RESIDENCIA_MAX"] - k["RESIDENCIA_MIN"])
        residencia[i] = max(1, round(r))

    vecindad = _construir_vecindad(k["RADIO_VECINDAD"], k["EXP_DISTANCIA"],
                                   k.get("VECINDAD", "moore"))

    estados = np.full(n, INERTE, dtype=np.uint8)
    for i in range(n):
        if valida[i]:
            estados[i] = INERTE if barrera[i] else SIN_QUEMAR
    pasos_ardiendo = np.zeros(n, dtype=np.uint16)

    i_foco = idx(foco_fila, foco_columna)
    if not valida[i_foco]:
        raise ValueError(
            f"La celda foco (fila={foco_fila}, columna={foco_columna}) no pertenece al grid válido de Apolo."
        )
    estados[i_foco] = ARDIENDO

    indices_iniciales = [i_foco]
    if isinstance(focos_iniciales, list):
        for fo in focos_iniciales:
            kk = idx(fo["fila"], fo["columna"])
            if kk != i_foco and valida[kk] and not barrera[kk] and estados[kk] == SIN_QUEMAR:
                estados[kk] = ARDIENDO
                indices_iniciales.append(kk)

    ahora = datetime.now(timezone.utc)
    eventos_spotting = []

    frente = list(indices_iniciales)
    tocadas = list(indices_iniciales)
    solo_conteo = bool(opciones.get("solo_conteo"))
    apagadas_por_lluvia = []

    # --- Reanudar desde un checkpoint --------------------------------------
    # El estado del autómata son cuatro cosas: qué hay en cada celda, cuántos
    # pasos lleva ardiendo cada una, quién está en el frente activo y qué
    # celdas se han tocado (para no recorrer las 36.390 en cada iteración).
    if estado_inicial:
        if estado_inicial.get("estados") is not None:
            estados = np.array(estado_inicial["estados"], dtype=np.uint8)
        if estado_inicial.get("pasos_ardiendo") is not None:
            pasos_ardiendo = np.array(estado_inicial["pasos_ardiendo"], dtype=np.uint16)
        if estado_inicial.get("frente") is not None:
            frente = [int(i) for i in estado_inicial["frente"]]
        if estado_inicial.get("tocadas") is not None:
            tocadas = [int(i) for i in estado_inicial["tocadas"]]
        if estado_inicial.get("hora_inicio"):
            ahora = datetime.fromisoformat(estado_inicial["hora_inicio"])

    # --- Ambiente por paso --------------------------------------------------
    # Todo lo que el clima le hace al fuego pasa por aquí. Antes esto no
    # existía: el viento venía de una serie horaria y la humedad estaba
    # horneada en el array desde el arranque, así que nada podía cambiar a
    # mitad de corrida.
    def ambiente_en(num_paso: int) -> dict:
        amb_serie = _serie_en_paso(serie_ambiental, num_paso, minutos_por_iteracion)
        viento_serie = _viento_en_paso(serie_viento, num_paso, minutos_por_iteracion,
                                       multiplicador_viento)

        if viento_serie:
            vel = math.hypot(viento_serie["u"], viento_serie["v"])
            grados = _grados_viento(viento_serie["u"], viento_serie["v"])
        else:
            # Sin serie de pronóstico, el viento sale de cada celda (abajo, en
            # el bucle). Aquí se toma la media del grid para que un escenario
            # tenga sobre qué operar: sin esto, «el viento rola al sur» no
            # tendría ninguna dirección de partida que girar.
            vel = vel_media_local * multiplicador_viento
            grados = _grados_viento(u_medio, v_medio) if (u_medio or v_medio) else 0.0

        base = {
            "temperatura_c": (amb_serie or {}).get("temperatura_c"),
            "vpd_kpa": (amb_serie or {}).get("vpd_kpa"),
            "lluvia_mm_h": (amb_serie or {}).get("lluvia_mm_h") or 0.0,
            "viento_ms": vel,
            "viento_grados": grados,
        }
        if base["temperatura_c"] is None:
            # Sin pronóstico, la referencia es la media de los tres eventos
            # reales de Apolo (18.3 °C). Hace falta por dos motivos: para que
            # un escenario de calor tenga sobre qué sumar, y para que la
            # consola dibuje una curva de temperatura en vez de un hueco.
            base["temperatura_c"] = _escenarios.REFERENCIAS["temperatura_temporada_c"]

        amb = _escenarios.ambiente_en_paso(base, guion, num_paso) if guion else {
            **base, "humedad_delta": 0.0, "eventos_activos": []}

        # Desplazamiento total de la humedad del combustible en este paso:
        #   · lo que pidió el usuario en los parámetros (constante)
        #   · el secado por temperatura (K_TEMPERATURA, como siempre)
        #   · el secado por VPD, si hay pronóstico
        #   · lo que aporten los escenarios vivos
        desplazamiento = delta_humedad - ajuste_humedad_temp + amb.get("humedad_delta", 0.0)
        vpd = amb.get("vpd_kpa")
        if vpd is not None:
            desplazamiento -= k["K_VPD"] * (vpd - k["VPD_REFERENCIA"])

        # Viento efectivo, ya con los escenarios aplicados.
        if viento_serie and amb.get("viento_grados") is not None:
            u, v = _componentes_viento(amb["viento_ms"] or 0.0, amb["viento_grados"])
            viento_efectivo = {"u": u, "v": v, "hora": viento_serie.get("hora")}
        elif viento_serie:
            viento_efectivo = viento_serie
        elif amb.get("eventos_activos"):
            # Sin serie pero con un escenario vivo, manda el escenario. Solo en
            # ese caso: si no hay ningún evento actuando, `viento_efectivo`
            # queda a None y el motor usa el viento de cada celda, que es
            # exactamente lo que hacía antes de existir los escenarios.
            u, v = _componentes_viento(amb["viento_ms"] or 0.0, amb["viento_grados"] or 0.0)
            viento_efectivo = {"u": u, "v": v, "hora": None}
        else:
            viento_efectivo = None

        return {
            "viento": viento_efectivo,
            # El viento que se INFORMA no es el mismo objeto que el que se
            # aplica: sin serie de pronóstico el motor usa el de cada celda
            # (`viento_efectivo` a None) pero la consola necesita igualmente
            # una cifra que dibujar, y la media del grid es esa cifra.
            "viento_ms_informado": amb.get("viento_ms"),
            "viento_grados_informado": amb.get("viento_grados"),
            "desplazamiento_humedad": desplazamiento,
            "lluvia_mm_h": max(amb.get("lluvia_mm_h") or 0.0, 0.0),
            "temperatura_c": amb.get("temperatura_c"),
            "vpd_kpa": vpd,
            "eventos_activos": amb.get("eventos_activos", []),
        }

    def construir_iteracion(num_iter, grid, amb):
        """Foto de la iteración: el fuego Y el ambiente que lo estaba moviendo.

        Antes solo llevaba el viento. La consola necesita las dos curvas juntas
        —temperatura, humedad y lluvia frente a celdas ardiendo— porque el
        objetivo es justamente ver cómo lo uno mueve lo otro.
        """
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
        for i in tocadas:
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
            direccion = (270 - (math.atan2(viento_usado["v"], viento_usado["u"]) * 180 / math.pi) + 360) % 360
            viento_obj = {"velocidad_ms": round(vel, 2), "direccion_grados": round(direccion),
                          "hora": viento_usado.get("hora")}

        return {
            "iteracion": num_iter,
            "timestamp_simulado": (ahora + timedelta(minutes=minutos_por_iteracion * num_iter)).isoformat(),
            "celdas": celdas,
            "num_celdas_ardiendo": ardiendo,
            "num_celdas_quemadas": quemadas,
            "viento": viento_obj,
            # El ambiente de cada iteración es lo que pinta la consola: la
            # curva de temperatura, humedad y lluvia junto a la del incendio.
            "ambiente": {
                "temperatura_c": (round(amb["temperatura_c"], 1)
                                  if amb.get("temperatura_c") is not None else None),
                "humedad": round(_humedad_efectiva(humedad_mediana,
                                                   amb["desplazamiento_humedad"]), 3),
                "vpd_kpa": (round(amb["vpd_kpa"], 2) if amb.get("vpd_kpa") is not None else None),
                "lluvia_mm_h": round(amb.get("lluvia_mm_h") or 0.0, 2),
                "viento_ms": (round(amb["viento_ms_informado"], 2)
                              if amb.get("viento_ms_informado") is not None else None),
                "viento_grados": (round(amb["viento_grados_informado"])
                                  if amb.get("viento_grados_informado") is not None else None),
                "eventos_activos": amb.get("eventos_activos", []),
            },
        }

    def paso(grid, num_paso):
        nonlocal frente
        nuevo = grid.copy()
        amb = ambiente_en(num_paso)
        viento_global = amb["viento"]
        desp_humedad = amb["desplazamiento_humedad"]
        lluvia = amb["lluvia_mm_h"]
        # Un solo factor de lluvia para todo el paso: la precipitación es un
        # fenómeno de escala mucho mayor que la celda de 500 m.
        f_lluvia = 1.0 / (1.0 + k["K_LLUVIA_PROP"] * lluvia) if lluvia > 0 else 1.0
        siguiente_frente = []

        for i in frente:
            if grid[i] != ARDIENDO:
                continue
            f = i // columnas
            c = i - f * columnas

            pasos_ardiendo[i] += 1
            if pasos_ardiendo[i] >= residencia[i]:
                nuevo[i] = QUEMADO

            fase = pasos_ardiendo[i] / residencia[i]
            factor_fase = 1 - k["DECAIMIENTO_FASE"] * min(fase, 1)

            hum_i = _humedad_efectiva(humedad[i], desp_humedad)

            # --- Extinción por lluvia ------------------------------------
            # El camino que faltaba: hasta ahora una celda encendida solo
            # podía pasar a «quemada» por agotar su tiempo de residencia.
            # Ninguna condición meteorológica la apagaba, así que la entrada
            # de la temporada de lluvias —que es lo que de verdad termina los
            # incendios en Apolo— no se podía representar.
            #
            # El (1 − intensidad) es lo que hace que la regla sea creíble: un
            # frente vivo aguanta el agua, unos rescoldos no. Sin ese término
            # la lluvia apagaría el incendio entero de una vez.
            if lluvia > 0 and nuevo[i] == ARDIENDO:
                intensidad_i = min(ndvi[i] / 0.8, 1) * (1 - hum_i) * factor_fase
                p_apagar = k["K_LLUVIA_EXT"] * lluvia * max(1 - intensidad_i, 0)
                if rand() < min(p_apagar, 1.0):
                    nuevo[i] = QUEMADO
                    apagadas_por_lluvia.append({"iteracion": num_paso, "celda": int(i)})
                    continue

            if viento_global:
                escala_local = (math.hypot(viento_u[i], viento_v[i]) / vel_media_local) if vel_media_local > 1e-6 else 1
                esc = 0.6 + 0.4 * min(escala_local, 2)
                u = viento_global["u"] * esc
                v = viento_global["v"] * esc
            else:
                u = viento_u[i] * multiplicador_viento
                v = viento_v[i] * multiplicador_viento
            vel_viento = math.hypot(u, v)

            for vec in vecindad:
                nf, nc = f + vec["df"], c + vec["dc"]
                if nf < 0 or nf >= filas or nc < 0 or nc >= columnas:
                    continue
                j = idx(nf, nc)
                if grid[j] != SIN_QUEMAR or nuevo[j] != SIN_QUEMAR:
                    continue
                if barrera[j]:
                    continue

                if hay_elevacion[i] and hay_elevacion[j]:
                    f_pend = _factor_pendiente(float(elevacion[j] - elevacion[i]), vec["d_metros"], k)
                else:
                    f_pend = 1 + 0.03 * pendiente_grados[j]

                f_viento = _factor_viento(vec["df"], vec["dc"], u, v, k)
                f_veg = k["NDVI_BASE"] + ndvi[j]
                f_hum = max(1 - k["K_HUMEDAD"] * _humedad_efectiva(humedad[j], desp_humedad), 0.1)

                # Resistencia del terreno de la celda DESTINO: cruzar hacia
                # una celda con una carretera o una quebrada cuesta más. Es la
                # resistencia del destino y no la del origen porque lo que
                # frena es la discontinuidad de combustible que hay que
                # atravesar para prender ahí.
                f_barrera = float(resistencia[j])

                p = (p_base * vec["peso"] * factor_fase * f_pend * f_viento
                     * f_veg * f_hum * f_lluvia * f_barrera)
                if rand() < min(max(p, 0), 1):
                    nuevo[j] = ARDIENDO
                    siguiente_frente.append(j)
                    tocadas.append(j)

            if k["SPOTTING_ACTIVO"] and vel_viento >= k["SPOTTING_VIENTO_MIN"]:
                intensidad = min(ndvi[i] / 0.8, 1) * (1 - hum_i) * factor_fase
                # La lluvia también moja las pavesas: una brasa empapada no
                # prende donde cae, por muy lejos que la lleve el viento.
                p_salto = k["SPOTTING_PROB"] * intensidad * min(vel_viento / 3, 3) * f_lluvia
                if rand() < p_salto:
                    # DIRECCIÓN DEL SALTO — corregido
                    #
                    # Antes era `math.atan2(-v, u)` con `sf = f - sin(...)`, y
                    # el signo norte-sur salía invertido. Comprobado
                    # numéricamente antes de tocarlo:
                    #
                    #     viento al NORTE -> Δfila +4 (SUR)     MAL
                    #     viento al SUR   -> Δfila -4 (NORTE)   MAL
                    #     viento al ESTE  -> Δcol  +4 (ESTE)    bien
                    #     viento al OESTE -> Δcol  -4 (OESTE)   bien
                    #
                    # El eje este-oeste estaba bien, y el factor de viento de la
                    # propagación normal (línea 157) también: el fallo afectaba
                    # solo al salto de pavesas y solo en el eje norte-sur.
                    #
                    # En Apolo estaba dormido: el viento del grid no llega a
                    # SPOTTING_VIENTO_MIN = 1,0 m/s y el spotting nunca se
                    # disparaba. Con el viento real de un escenario o de la
                    # serie ERA5 sí se dispara, y mandaba las pavesas al lado
                    # contrario.
                    #
                    # Correcto: `u` es la componente ESTE y `v` la NORTE, así
                    # que el ángulo del vector viento es atan2(v, u). Y como la
                    # fila crece hacia el SUR, el desplazamiento en filas es
                    # MENOS la componente norte.
                    dir_viento = math.atan2(v, u)
                    ang = dir_viento + (rand() - 0.5) * 2 * k["SPOTTING_DISPERSION"]
                    alcance = k["SPOTTING_DIST_MIN"] + rand() * (k["SPOTTING_DIST_MAX"] - k["SPOTTING_DIST_MIN"]) \
                        * min(vel_viento / 5, 1)
                    sf = f - round(math.sin(ang) * alcance)   # sin = norte → la fila BAJA
                    sc = c + round(math.cos(ang) * alcance)   # cos = este  → la columna SUBE
                    if 0 <= sf < filas and 0 <= sc < columnas:
                        s = idx(sf, sc)
                        # Las pavesas SÍ cruzan una carretera: vuelan por
                        # encima. Por eso el salto NO aplica `resistencia` —
                        # solo respeta las barreras duras (agua, roca, NDVI).
                        # Es justamente el mecanismo por el que un cortafuegos
                        # puede fallar con viento fuerte, y quitarlo aquí
                        # sobreestimaría la eficacia de los caminos.
                        if valida[s] and not barrera[s] and grid[s] == SIN_QUEMAR and nuevo[s] == SIN_QUEMAR:
                            nuevo[s] = ARDIENDO
                            siguiente_frente.append(s)
                            tocadas.append(s)
                            eventos_spotting.append({
                                "iteracion": num_paso, "origen": {"fila": f, "columna": c},
                                "destino": {"fila": sf, "columna": sc, "lat": float(lat_pos[s]), "lon": float(lon_pos[s])},
                                "distancia_m": round(alcance * TAM_CELDA_NS_M),
                            })

            if nuevo[i] == ARDIENDO:
                siguiente_frente.append(i)

        frente = siguiente_frente
        return nuevo, amb

    # El tramo empieza publicando su estado de partida. En una corrida entera
    # (desde_paso = 0) eso es la iteración 0 de siempre; al reanudar es la foto
    # del paso donde se pausó, que la consola ya tiene y descarta.
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
        if limite_celdas and len(tocadas) > limite_celdas:
            interrumpido_por = "limite_celdas"
            break

    ultimo_paso = iteraciones[-1]["iteracion"]
    metadatos = {
        "constantes": k,
        "minutos_por_iteracion": minutos_por_iteracion,
        "semilla": semilla,
        "celdas_con_dem": celdas_con_dem,
        "usa_dem": celdas_con_dem > 0,
        "usa_serie_viento": bool(serie_viento),
        "usa_serie_ambiental": bool(serie_ambiental),
        "barreras_osm": len(barreras_extra) if barreras_extra else 0,
        "eventos_spotting": eventos_spotting,
        # --- Tramo y escenarios ---------------------------------------------
        "desde_paso": desde_paso,
        "ultimo_paso": ultimo_paso,
        "completado": ultimo_paso >= num_iteraciones,
        "interrumpido_por": interrumpido_por,
        "guion": guion,
        "celdas_apagadas_por_lluvia": len(apagadas_por_lluvia),
    }

    if devolver_estado:
        # El checkpoint. `estados` y `pasos_ardiendo` son arrays de 36.390
        # posiciones, pero `tocadas` suele ser de unos pocos miles: para no
        # mover 70 KB en cada pausa se guardan solo las celdas tocadas, que son
        # las únicas que pueden estar en un estado distinto del inicial.
        metadatos["estado"] = {
            "paso": ultimo_paso,
            "prng": rand.a,
            "estados": estados.tolist(),
            "pasos_ardiendo": pasos_ardiendo.tolist(),
            "frente": [int(i) for i in frente],
            "tocadas": [int(i) for i in tocadas],
            "hora_inicio": ahora.isoformat(),
        }

    if solo_conteo:
        ids = [celda_id[i] for i in tocadas if estados[i] in (QUEMADO, ARDIENDO)]
        metadatos["celdas_quemadas_ids"] = ids

    return {"iteraciones": iteraciones, "metadatos": metadatos}


def perimetro_simulado(grid_celdas: list[dict], parametros: dict, opciones: Optional[dict] = None) -> set:
    """Para la calibración: corre el autómata y devuelve solo el perímetro
    final (conjunto de celdas quemadas). Mucho más barato que guardar todo."""
    opciones = dict(opciones or {})
    opciones["solo_conteo"] = True
    resultado = ejecutar_automata(grid_celdas, parametros, opciones)
    return set(resultado["metadatos"]["celdas_quemadas_ids"])
