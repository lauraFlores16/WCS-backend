"""
============================================================================
CATÁLOGO DE ESCENARIOS METEOROLÓGICOS
============================================================================
Sucesos del tiempo que pueden alterar la propagación **en mitad** de la
corrida del autómata, no solo al principio.

Por qué esto hace falta
-----------------------
Los tres incendios reales de Apolo tienen medias estacionales casi idénticas:

    2021   ndvi 0.270 · humedad 0.375 · viento 0.40 m/s · T 18.3 °C  →   159 km²
    2023   ndvi 0.269 · humedad 0.374 · viento 0.39 m/s · T 18.3 °C  →   827 km²
    2024   ndvi 0.271 · humedad 0.374 · viento 0.40 m/s · T 18.2 °C  →   471 km²

Las variables promedio NO distinguen un evento de otro —son iguales hasta el
tercer decimal— y sin embargo el área quemada varía 5×. Lo que separa 159 de
827 km² no es la media de la temporada: es lo que pasó DENTRO de ella. Una
racha de viento de tres días, una lluvia que llegó o que no llegó. Por eso los
escenarios se aplican por paso y no como una constante de la corrida.

Dónde engancha cada escenario
-----------------------------
La probabilidad de que una celda encendida prenda a su vecina es, en
`automata.ejecutar_automata`:

    p = p_base · peso · fase · f_pendiente · f_viento · f_vegetación · f_humedad · f_lluvia
                                    │            │                        │           │
                                    │            │                        │           └── LLUVIA
                                    │            │                        └── HUMEDAD y SECADO
                                    │            └── (terreno: no lo toca el clima)
                                    └── VIENTO

Sensibilidad real medida sobre la mediana del grid de Apolo
(humedad 0.376 · ndvi 0.313 · viento 0.39 m/s):

    VIENTO      2 m/s  →  +23 %      5 m/s  →  +65 %     12 m/s  →  +165 %
    LLUVIA      2 mm/h →  −29 %     10 mm/h →  −71 %   (+ extinción de celdas)
    SECADO     +5 °C   →   +3 %    +10 °C   →   +6 %
    HUMEDAD    +0.10   →   −6 %     +0.25   →  −15 %

O sea: **el viento manda**. La humedad y la temperatura son palancas suaves;
subirlas a lo bruto para que «se note» sería falsear el modelo. La lluvia sí
pega fuerte, pero porque se le añadió un canal propio (ver `automata.py`):
antes de esto una celda ardiendo no se apagaba nunca por causas del tiempo.

Unidades
--------
    temperatura   °C
    humedad       fracción 0–1 (la del grid, no HR en %)
    viento        m/s
    lluvia        mm/h
    vpd           kPa (déficit de presión de vapor)
============================================================================
"""
from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Referencias del propio proyecto, para que las magnitudes no salgan de la nada
# ---------------------------------------------------------------------------
REFERENCIAS = {
    # Medias de los tres eventos reales (api/datos/eventos_historicos.json)
    "temperatura_temporada_c": 18.3,
    "humedad_temporada": 0.375,
    "viento_temporada_ms": 0.40,
    # Distribución real del grid (36.390 celdas de api/datos/grid.csv)
    "humedad_grid": {"min": 0.220, "p10": 0.356, "mediana": 0.376, "p90": 0.394, "max": 0.400},
    "viento_grid_ms": {"min": 0.07, "mediana": 0.39, "max": 0.52},
    # VPD de referencia: por encima de esto el combustible se seca
    "vpd_referencia_kpa": 1.0,
}

# Familias, para agrupar en la consola.
FAMILIAS = {
    "viento": "Viento",
    "lluvia": "Lluvia",
    "secado": "Calor y secado",
    "frente": "Frentes y cambios de masa de aire",
}


# ---------------------------------------------------------------------------
# EL CATÁLOGO
# ---------------------------------------------------------------------------
# Cada escenario declara:
#   id, nombre, familia, descripcion  → lo que se ve en la consola
#   efectos    → qué le hace al ambiente mientras dura
#   duracion_pasos → cuántos pasos dura (None = hasta el final de la corrida)
#   justificacion  → de dónde sale la magnitud (para la defensa)
#
# Los efectos son SIEMPRE relativos al ambiente que hubiera en ese paso, no
# absolutos: así un mismo escenario inyectado en el paso 5 o en el 30 hace lo
# mismo respecto de lo que estaba pasando.
#
#   viento_ms_delta        suma m/s a la velocidad
#   viento_ms_fijar        fija la velocidad (ignora la serie)
#   viento_grados_delta    rola la dirección (positivo = horario)
#   viento_grados_fijar    fija la dirección
#   lluvia_mm_h            precipitación durante el suceso
#   temperatura_delta_c    suma °C
#   humedad_delta          suma a la fracción de humedad del combustible
#   vpd_delta_kpa          suma al déficit de presión de vapor
#   rampa_pasos            pasos que tarda en alcanzar el efecto pleno (0 = de golpe)
# ---------------------------------------------------------------------------
CATALOGO = [
    # ======================= VIENTO =======================
    {
        "id": "racha_viento",
        "nombre": "Racha de viento sostenida",
        "familia": "viento",
        "descripcion": "El viento arrecia y se mantiene. Es el suceso que más "
                       "acelera el frente y el que dispara los saltos de pavesas.",
        "efectos": {"viento_ms_fijar": 8.0, "rampa_pasos": 2},
        "duracion_pasos": 12,
        "justificacion": "8 m/s (≈29 km/h) es una racha fuerte pero corriente en la "
                         "temporada seca. A esa velocidad la propagación a favor del "
                         "viento sube ~108 % y el spotting pasa de imposible a ~1 % por "
                         "paso: el umbral del motor son 1.0 m/s.",
        "intensidades": {"suave": 4.0, "fuerte": 8.0, "extrema": 14.0},
    },
    {
        "id": "rolada_viento",
        "nombre": "El viento rola",
        "familia": "viento",
        "descripcion": "La dirección gira y el frente cambia de eje. Lo que era el "
                       "flanco pasa a ser cabeza: el fuego avanza hacia donde nadie "
                       "lo esperaba.",
        "efectos": {"viento_grados_delta": 90, "rampa_pasos": 3},
        "duracion_pasos": None,
        "justificacion": "Un giro de 90° no cambia la velocidad del frente, cambia su "
                         "geometría. Es el escenario que justifica por qué un perímetro "
                         "no se puede extrapolar en línea recta.",
        "intensidades": {"leve": 45, "fuerte": 90, "inversion": 180},
    },
    {
        "id": "calma",
        "nombre": "Se calma el viento",
        "familia": "viento",
        "descripcion": "El viento cae casi a cero. El frente pierde dirección y se "
                       "expande de forma más redonda, empujado solo por la pendiente.",
        "efectos": {"viento_ms_fijar": 0.3, "rampa_pasos": 2},
        "duracion_pasos": 10,
        "justificacion": "0.3 m/s está dentro del rango real del grid (mín. 0.07, "
                         "mediana 0.39). Por debajo de 1.0 m/s el motor desactiva el "
                         "spotting, así que el fuego deja de dar saltos.",
    },

    # ======================= LLUVIA =======================
    {
        "id": "lluvia_ligera",
        "nombre": "Llovizna",
        "familia": "lluvia",
        "descripcion": "Precipitación débil. Humedece el combustible fino y frena algo "
                       "el avance, pero no apaga un frente ya establecido.",
        "efectos": {"lluvia_mm_h": 1.0, "humedad_delta": 0.03, "rampa_pasos": 1},
        "duracion_pasos": 8,
        "justificacion": "<2.5 mm/h es el umbral habitual de lluvia ligera. Frena la "
                         "propagación ~17 % y casi no extingue: sirve para enseñar que "
                         "no toda lluvia salva.",
    },
    {
        "id": "lluvia_moderada",
        "nombre": "Lluvia moderada",
        "familia": "lluvia",
        "descripcion": "Precipitación sostenida. Frena el frente de forma clara y "
                       "empieza a apagar las celdas de menor intensidad.",
        "efectos": {"lluvia_mm_h": 5.0, "humedad_delta": 0.08,
                    "temperatura_delta_c": -3, "rampa_pasos": 2},
        "duracion_pasos": 12,
        "justificacion": "2.5–7.6 mm/h es el rango de lluvia moderada. A 5 mm/h la "
                         "propagación cae ~50 % y las celdas débiles empiezan a "
                         "extinguirse. La bajada de 3 °C acompaña a la lluvia: es aire "
                         "descendente enfriado por evaporación.",
    },
    {
        "id": "tormenta",
        "nombre": "Tormenta",
        "familia": "lluvia",
        "descripcion": "Lluvia intensa CON ráfagas. El caso interesante: apaga por un "
                       "lado y empuja por el otro, así que el resultado no es obvio.",
        "efectos": {"lluvia_mm_h": 15.0, "humedad_delta": 0.15,
                    "viento_ms_fijar": 10.0, "temperatura_delta_c": -5,
                    "rampa_pasos": 1},
        "duracion_pasos": 8,
        "justificacion": ">7.6 mm/h es lluvia intensa. La ráfaga de salida de tormenta "
                         "es un fenómeno real y peligroso: en los primeros pasos el "
                         "viento puede expandir el frente antes de que el agua lo "
                         "apague. Sirve para defender que el modelo no es monótono.",
    },
    {
        "id": "fin_temporada",
        "nombre": "Entrada de la temporada de lluvias",
        "familia": "lluvia",
        "descripcion": "Lluvia persistente que no cesa. Es lo que de verdad termina "
                       "los incendios en Apolo, no la extinción manual.",
        "efectos": {"lluvia_mm_h": 8.0, "humedad_delta": 0.20,
                    "temperatura_delta_c": -4, "rampa_pasos": 4},
        "duracion_pasos": None,
        "justificacion": "Los tres eventos reales acaban entre finales de octubre y "
                         "finales de noviembre, que es cuando entran las lluvias. "
                         "Ninguno se apagó solo.",
    },

    # ======================= CALOR Y SECADO =======================
    {
        "id": "ola_calor",
        "nombre": "Ola de calor",
        "familia": "secado",
        "descripcion": "Sube la temperatura y se dispara el déficit de presión de "
                       "vapor: el combustible fino se seca y prende con menos energía.",
        "efectos": {"temperatura_delta_c": 8, "vpd_delta_kpa": 1.5,
                    "humedad_delta": -0.04, "rampa_pasos": 4},
        "duracion_pasos": None,
        "justificacion": "Sobre los 18.3 °C de media de la temporada, +8 °C deja el "
                         "aire a ~26 °C. El efecto sobre la propagación es modesto "
                         "(+6–9 %) y así hay que contarlo: la temperatura seca el "
                         "combustible, no lo enciende. Quien manda es el viento.",
        "intensidades": {"suave": 4, "fuerte": 8, "extrema": 14},
    },
    {
        "id": "tarde_seca",
        "nombre": "Máximo de la tarde",
        "familia": "secado",
        "descripcion": "El pico diario: entre las 13 y las 17 h la humedad toca fondo "
                       "y el viento sube. Es la ventana en la que los incendios corren.",
        "efectos": {"temperatura_delta_c": 5, "humedad_delta": -0.05,
                    "vpd_delta_kpa": 1.0, "viento_ms_delta": 1.5, "rampa_pasos": 3},
        "duracion_pasos": 16,
        "justificacion": "16 pasos × 15 min = 4 h, la duración real de la ventana de "
                         "tarde. Combina las tres palancas a la vez, que es como se "
                         "comporta el ciclo diurno de verdad.",
    },
    {
        "id": "noche",
        "nombre": "Caída de la noche",
        "familia": "secado",
        "descripcion": "Baja la temperatura, sube la humedad relativa y el viento "
                       "amaina. El frente se aquieta sin apagarse.",
        "efectos": {"temperatura_delta_c": -7, "humedad_delta": 0.06,
                    "vpd_delta_kpa": -0.8, "viento_ms_fijar": 1.0, "rampa_pasos": 4},
        "duracion_pasos": 40,
        "justificacion": "40 pasos × 15 min = 10 h de noche. Es el contrapunto de "
                         "«tarde seca» y explica el patrón de avance a tirones que se "
                         "ve en los perímetros reales día a día.",
    },

    # ======================= FRENTES =======================
    {
        "id": "surazo",
        "nombre": "Surazo (frente frío del sur)",
        "familia": "frente",
        "descripcion": "Entrada de aire frío desde el sur: la temperatura se desploma, "
                       "el viento rola al sur y arrecia. Fenómeno propio de Bolivia.",
        "efectos": {"temperatura_delta_c": -10, "humedad_delta": 0.10,
                    "viento_grados_fijar": 180, "viento_ms_fijar": 9.0,
                    "rampa_pasos": 3},
        "duracion_pasos": 20,
        "justificacion": "El surazo es un fenómeno documentado del invierno boliviano. "
                         "Interesa porque tira de las dos palancas en sentidos "
                         "contrarios: enfría y humedece (frena) pero rola y arrecia "
                         "(acelera y cambia el eje del frente).",
    },
    {
        "id": "viento_seco",
        "nombre": "Viento seco de ladera",
        "familia": "frente",
        "descripcion": "El peor caso: viento fuerte y aire muy seco a la vez. Aire que "
                       "baja de la cordillera, se comprime y se calienta.",
        "efectos": {"viento_ms_fijar": 12.0, "temperatura_delta_c": 6,
                    "humedad_delta": -0.08, "vpd_delta_kpa": 2.0, "rampa_pasos": 2},
        "duracion_pasos": 16,
        "justificacion": "Efecto föhn: Apolo está en la transición de la cordillera a "
                         "la Amazonía, la topografía para esto existe. Es el escenario "
                         "de peor caso del catálogo: viento máximo y secado máximo "
                         "empujando en la misma dirección.",
    },
]

POR_ID = {e["id"]: e for e in CATALOGO}


def listar() -> list[dict]:
    """El catálogo para la consola, agrupado por familia."""
    return [
        {**e, "familia_nombre": FAMILIAS.get(e["familia"], e["familia"])}
        for e in CATALOGO
    ]


# ---------------------------------------------------------------------------
# Un suceso concreto colocado en la línea de tiempo de una corrida
# ---------------------------------------------------------------------------
def crear_evento(id_escenario: str, paso_inicio: int,
                 intensidad: Optional[str] = None,
                 ajustes: Optional[dict] = None,
                 duracion_pasos: Optional[int] = None) -> dict:
    """Instancia un escenario del catálogo en un paso concreto.

    `intensidad` elige una de las variantes declaradas (suave/fuerte/extrema).
    `ajustes` permite cambiar cualquier efecto a mano, para que el analista no
    quede encerrado en las tres variantes.
    """
    base = POR_ID.get(id_escenario)
    if not base:
        raise ValueError(f"No existe el escenario «{id_escenario}»")

    efectos = dict(base["efectos"])

    # Las variantes de intensidad solo tocan el efecto principal de la familia.
    if intensidad and base.get("intensidades"):
        valor = base["intensidades"].get(intensidad)
        if valor is None:
            raise ValueError(
                f"«{intensidad}» no es una intensidad de «{id_escenario}»; "
                f"hay {list(base['intensidades'])}")
        for clave in ("viento_ms_fijar", "viento_grados_delta", "temperatura_delta_c"):
            if clave in efectos:
                efectos[clave] = valor
                break

    if ajustes:
        efectos.update({k: v for k, v in ajustes.items() if v is not None})

    dur = duracion_pasos if duracion_pasos is not None else base["duracion_pasos"]
    return {
        "id": id_escenario,
        "nombre": base["nombre"],
        "familia": base["familia"],
        "paso_inicio": int(paso_inicio),
        "duracion_pasos": dur,
        "intensidad": intensidad,
        "efectos": efectos,
    }


def _peso_rampa(evento: dict, paso: int) -> float:
    """Cuánto del efecto está aplicado en este paso (0 → 1).

    Un frente no llega de golpe: la rampa evita el escalón artificial que
    haría que el perímetro diera un salto imposible entre dos iteraciones.
    """
    inicio = evento["paso_inicio"]
    if paso < inicio:
        return 0.0
    dur = evento.get("duracion_pasos")
    if dur is not None and paso >= inicio + dur:
        return 0.0
    rampa = evento["efectos"].get("rampa_pasos") or 0
    if rampa <= 0:
        return 1.0
    return min((paso - inicio + 1) / rampa, 1.0)


def ambiente_en_paso(base: dict, eventos: list[dict], paso: int) -> dict:
    """Aplica al ambiente base todos los eventos vivos en este paso.

    `base` es lo que diga el pronóstico (o los valores del grid) para ese
    instante. Devuelve el ambiente ya alterado más la lista de los eventos que
    están actuando, que es lo que la consola enseña en su línea de tiempo.
    """
    amb = {
        "temperatura_c": base.get("temperatura_c"),
        "humedad_delta": 0.0,
        "vpd_kpa": base.get("vpd_kpa"),
        "lluvia_mm_h": base.get("lluvia_mm_h") or 0.0,
        "viento_ms": base.get("viento_ms"),
        "viento_grados": base.get("viento_grados"),
    }
    activos = []

    for ev in eventos:
        w = _peso_rampa(ev, paso)
        if w <= 0:
            continue
        activos.append({"id": ev["id"], "nombre": ev["nombre"],
                        "familia": ev["familia"], "avance": round(w, 3)})
        e = ev["efectos"]

        if "temperatura_delta_c" in e and amb["temperatura_c"] is not None:
            amb["temperatura_c"] += e["temperatura_delta_c"] * w
        if "humedad_delta" in e:
            amb["humedad_delta"] += e["humedad_delta"] * w
        if "vpd_delta_kpa" in e and amb["vpd_kpa"] is not None:
            amb["vpd_kpa"] = max(amb["vpd_kpa"] + e["vpd_delta_kpa"] * w, 0.0)
        if "lluvia_mm_h" in e:
            # Dos lluvias a la vez no se suman: manda la más intensa.
            amb["lluvia_mm_h"] = max(amb["lluvia_mm_h"], e["lluvia_mm_h"] * w)

        # Viento: `fijar` interpola desde lo que hubiera hacia el valor nuevo,
        # para que la rampa también valga aquí.
        if "viento_ms_fijar" in e and amb["viento_ms"] is not None:
            amb["viento_ms"] = amb["viento_ms"] * (1 - w) + e["viento_ms_fijar"] * w
        if "viento_ms_delta" in e and amb["viento_ms"] is not None:
            amb["viento_ms"] = max(amb["viento_ms"] + e["viento_ms_delta"] * w, 0.0)
        if "viento_grados_fijar" in e and amb["viento_grados"] is not None:
            amb["viento_grados"] = _interpolar_angulo(
                amb["viento_grados"], e["viento_grados_fijar"], w)
        if "viento_grados_delta" in e and amb["viento_grados"] is not None:
            amb["viento_grados"] = (amb["viento_grados"] + e["viento_grados_delta"] * w) % 360

    amb["eventos_activos"] = activos
    return amb


def _interpolar_angulo(desde: float, hasta: float, w: float) -> float:
    """Interpola por el camino corto: de 350° a 10° son 20°, no 340°."""
    d = ((hasta - desde + 180) % 360) - 180
    return (desde + d * w) % 360
