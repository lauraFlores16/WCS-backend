"""
============================================================================
SESIONES DE SIMULACIÓN INTERACTIVA
============================================================================
Una corrida que se puede pausar, mirar, alterar y continuar.

El autómata de siempre (`ejecutar_automata`) resuelve las 40 iteraciones de un
tirón y devuelve la película entera. Eso vale para guardar un escenario, pero
no para lo que pide la consola: parar en el paso 12 porque acaba de entrar una
tormenta y ver qué pasa a partir de ahí. Nadie sabe en el paso 0 que va a
querer intervenir en el 12 — si lo supiera, bastaría con programarlo.

Cómo funciona
-------------
El motor sabe arrancar desde un checkpoint (`estado_inicial`) y parar donde se
le diga (`hasta_paso`). Una sesión no es más que ese checkpoint guardado entre
peticiones, con su guion de eventos al lado:

    crear()      corre 0 → N pasos y guarda dónde se quedó
    avanzar()    sigue desde donde estaba, otros N pasos
    inyectar()   mete un escenario a partir del paso actual
    cerrar()     tira la sesión

La propiedad que lo sostiene, comprobada en `pruebas/prueba_escenarios.py`:
correr 0→40 de un tirón y correr 0→12 + 12→40 desde el checkpoint dan el mismo
perímetro celda a celda. El generador aleatorio de mulberry32 tiene todo su
estado en un entero de 32 bits, así que la pausa no pierde información.

Dónde vive esto
---------------
En memoria del proceso, a propósito. Una simulación a medias no es un dato del
sistema: es el borrador de alguien que está pensando. Lo que se guarda en
Supabase es el escenario TERMINADO, con su guion, y eso sigue pasando por
`motor/simulacion.py` como siempre. Consecuencias que hay que asumir:

  · reiniciar el backend tira las sesiones abiertas (y está bien);
  · con varios trabajadores, la sesión vive en el que la creó. En desarrollo
    `runserver` es un solo proceso; en producción hay que fijar un trabajador
    o mover esto a Redis. Queda anotado aquí y no escondido.

Se limpian solas: `MAX_SESIONES` a la vez y `VIDA_MAX_S` de inactividad, para
que un navegador que se cierra sin avisar no deje 36.390 celdas ocupadas para
siempre.
============================================================================
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Optional

from ..errores import ErrorAPI
from . import escenarios as esc
from .automata import ejecutar_automata

# Una sesión pesa lo que pesan sus arrays: ~36.390 uint8 + 36.390 uint16 ≈
# 110 KB, más las celdas tocadas. Veinte sesiones son unos pocos MB.
MAX_SESIONES = 20
VIDA_MAX_S = 30 * 60          # media hora sin tocarla y se cierra sola
PASOS_POR_TANDA = 5           # cuántos pasos avanza una llamada por defecto

_sesiones: dict[str, dict] = {}
_guard = threading.Lock()


def _limpiar_caducadas() -> None:
    ahora = time.time()
    muertas = [sid for sid, s in _sesiones.items() if ahora - s["visto"] > VIDA_MAX_S]
    for sid in muertas:
        _sesiones.pop(sid, None)
    # Si aun así sobran, se van las más antiguas: es preferible perder el
    # borrador de alguien que dejó la pestaña abierta a quedarnos sin memoria.
    if len(_sesiones) > MAX_SESIONES:
        por_edad = sorted(_sesiones.items(), key=lambda kv: kv[1]["visto"])
        for sid, _ in por_edad[: len(_sesiones) - MAX_SESIONES]:
            _sesiones.pop(sid, None)


def _obtener(sesion_id: str) -> dict:
    with _guard:
        s = _sesiones.get(sesion_id)
        if not s:
            raise ErrorAPI(
                "La sesión de simulación no existe o ya caducó. "
                "Vuelve a lanzarla desde el principio.",
                estado_http=404, servicio="Simulación interactiva",
            )
        s["visto"] = time.time()
        return s


def _resumen(s: dict, iteraciones_nuevas: list) -> dict:
    """Lo que se le manda al navegador en cada tanda.

    Solo las iteraciones NUEVAS: la consola ya tiene las anteriores y volver a
    mandarlas multiplicaría el tráfico por el número de tandas.
    """
    ult = s["iteraciones"][-1] if s["iteraciones"] else None
    return {
        "sesion_id": s["id"],
        "paso_actual": s["paso"],
        "num_iteraciones": s["parametros"]["num_iteraciones"],
        "terminada": s["terminada"],
        "motivo_fin": s["motivo_fin"],
        "iteraciones": iteraciones_nuevas,
        "guion": s["guion"],
        "resumen": {
            "celdas_ardiendo": ult["num_celdas_ardiendo"] if ult else 0,
            "celdas_quemadas": ult["num_celdas_quemadas"] if ult else 0,
            "apagadas_por_lluvia": s["apagadas_por_lluvia"],
            "saltos_pavesas": s["saltos_pavesas"],
        },
        "ambiente": ult.get("ambiente") if ult else None,
    }


def _correr_tramo(s: dict, pasos: int) -> list:
    """Avanza la sesión `pasos` iteraciones y actualiza su checkpoint."""
    objetivo = min(s["paso"] + pasos, s["parametros"]["num_iteraciones"])
    if objetivo <= s["paso"]:
        return []

    resultado = ejecutar_automata(
        s["grid"], dict(s["parametros"]),
        {
            **s["opciones"],
            "guion": s["guion"],
            "desde_paso": s["paso"],
            "hasta_paso": objetivo,
            "estado_inicial": s["estado"],
            "devolver_estado": True,
        },
    )
    meta = resultado["metadatos"]
    # La primera iteración del tramo es la foto de partida, que la consola ya
    # tiene: se descarta para no duplicarla.
    nuevas = resultado["iteraciones"][1:]

    s["estado"] = meta["estado"]
    s["paso"] = meta["ultimo_paso"]
    s["iteraciones"].extend(nuevas)
    s["apagadas_por_lluvia"] += meta.get("celdas_apagadas_por_lluvia", 0)
    # Los saltos de pavesas se ACUMULAN entre tandas. Los metadatos de cada
    # tramo solo traen los suyos; si al guardar se tomara el último, un
    # escenario de 40 pasos partido en ocho tandas declararía los saltos de los
    # últimos cinco pasos y nada más.
    s["saltos_lista"].extend(meta.get("eventos_spotting") or [])
    s["saltos_pavesas"] = len(s["saltos_lista"])
    s["metadatos"] = meta

    if meta.get("interrumpido_por") == "extinguido":
        s["terminada"] = True
        s["motivo_fin"] = "El incendio se extinguió: no queda ninguna celda ardiendo."
    elif meta.get("interrumpido_por") == "limite_celdas":
        s["terminada"] = True
        s["motivo_fin"] = "Se alcanzó el límite de celdas de la simulación."
    elif s["paso"] >= s["parametros"]["num_iteraciones"]:
        s["terminada"] = True
        s["motivo_fin"] = "Se completaron todas las iteraciones previstas."

    return nuevas


# ---------------------------------------------------------------------------
# API de la sesión
# ---------------------------------------------------------------------------
def crear(grid: list[dict], parametros: dict, opciones: Optional[dict] = None,
          guion: Optional[list] = None, pasos_iniciales: int = 0,
          usuario: Optional[str] = None) -> dict:
    """Abre una sesión en el paso 0. `pasos_iniciales` avanza de entrada."""
    with _guard:
        _limpiar_caducadas()

    sesion = {
        "id": uuid.uuid4().hex[:16],
        "creada": time.time(),
        "visto": time.time(),
        "usuario": usuario,
        "grid": grid,
        "parametros": dict(parametros),
        "opciones": dict(opciones or {}),
        "guion": list(guion or []),
        "estado": None,
        "paso": 0,
        "iteraciones": [],
        "apagadas_por_lluvia": 0,
        "saltos_pavesas": 0,
        "saltos_lista": [],
        "terminada": False,
        "motivo_fin": None,
        "metadatos": {},
    }

    # El paso 0 (el foco solo, sin propagar) se publica siempre: es el punto de
    # partida que la consola pinta antes de que el usuario pulse nada.
    inicial = ejecutar_automata(
        sesion["grid"], dict(sesion["parametros"]),
        {**sesion["opciones"], "guion": sesion["guion"],
         "desde_paso": 0, "hasta_paso": 0, "devolver_estado": True},
    )
    sesion["estado"] = inicial["metadatos"]["estado"]
    sesion["iteraciones"] = list(inicial["iteraciones"])
    sesion["metadatos"] = inicial["metadatos"]

    with _guard:
        _sesiones[sesion["id"]] = sesion

    nuevas = list(sesion["iteraciones"])
    if pasos_iniciales:
        nuevas += _correr_tramo(sesion, pasos_iniciales)
    return _resumen(sesion, nuevas)


def avanzar(sesion_id: str, pasos: int = PASOS_POR_TANDA) -> dict:
    s = _obtener(sesion_id)
    if s["terminada"]:
        return _resumen(s, [])
    nuevas = _correr_tramo(s, max(1, int(pasos)))
    return _resumen(s, nuevas)


def inyectar(sesion_id: str, id_escenario: str, intensidad: Optional[str] = None,
             ajustes: Optional[dict] = None, duracion_pasos: Optional[int] = None,
             paso_inicio: Optional[int] = None) -> dict:
    """Mete un escenario en la línea de tiempo, a partir del paso actual.

    No se puede inyectar hacia atrás: los pasos ya calculados están calculados,
    y reescribirlos sería falsificar la corrida. Si alguien quiere ver qué
    habría pasado con la tormenta cinco pasos antes, eso es otra simulación.
    """
    s = _obtener(sesion_id)
    if s["terminada"]:
        raise ErrorAPI("La simulación ya terminó: no se le pueden añadir sucesos.",
                       estado_http=409, servicio="Simulación interactiva")

    inicio = s["paso"] if paso_inicio is None else int(paso_inicio)
    if inicio < s["paso"]:
        raise ErrorAPI(
            f"No se puede inyectar un suceso en el paso {inicio}: la simulación ya va "
            f"por el {s['paso']} y esos pasos ya están calculados.",
            estado_http=400, servicio="Simulación interactiva")

    try:
        evento = esc.crear_evento(id_escenario, inicio, intensidad=intensidad,
                                  ajustes=ajustes, duracion_pasos=duracion_pasos)
    except ValueError as e:
        # Un id o una intensidad que no existen son un error de QUIEN LLAMA, no
        # una avería del servidor: sin esto salía un 500 y el mensaje útil
        # («hay suave/fuerte/extrema») se perdía en el traceback.
        raise ErrorAPI(str(e), estado_http=400, servicio="Simulación interactiva") from e
    s["guion"].append(evento)
    return {**_resumen(s, []), "evento": evento}


def quitar_evento(sesion_id: str, indice: int) -> dict:
    """Retira un evento del guion — solo si todavía no ha empezado a actuar."""
    s = _obtener(sesion_id)
    if indice < 0 or indice >= len(s["guion"]):
        raise ErrorAPI("Ese suceso no está en el guion.", estado_http=404,
                       servicio="Simulación interactiva")
    ev = s["guion"][indice]
    if ev["paso_inicio"] <= s["paso"]:
        raise ErrorAPI(
            f"«{ev['nombre']}» ya actuó sobre la simulación (empezó en el paso "
            f"{ev['paso_inicio']} y vamos por el {s['paso']}). Quitarlo ahora no "
            "desharía su efecto.",
            estado_http=409, servicio="Simulación interactiva")
    s["guion"].pop(indice)
    return _resumen(s, [])


def estado(sesion_id: str, desde: int = 0) -> dict:
    """La sesión entera desde una iteración dada (para recargar la consola)."""
    s = _obtener(sesion_id)
    return _resumen(s, [it for it in s["iteraciones"] if it["iteracion"] >= desde])


def completar(sesion_id: str) -> dict:
    """Corre lo que quede de un tirón: «ya he visto bastante, termina»."""
    s = _obtener(sesion_id)
    if s["terminada"]:
        return _resumen(s, [])
    pendientes = s["parametros"]["num_iteraciones"] - s["paso"]
    nuevas = _correr_tramo(s, max(1, pendientes))
    return _resumen(s, nuevas)


def resultado_completo(sesion_id: str) -> dict:
    """Todo lo necesario para guardar la sesión como escenario en Supabase."""
    s = _obtener(sesion_id)
    return {
        "iteraciones": s["iteraciones"],
        "metadatos": {
            **s["metadatos"],
            "guion": s["guion"],
            # Totales de TODA la sesión, no del último tramo (ver `_correr_tramo`).
            "eventos_spotting": s["saltos_lista"],
            "celdas_apagadas_por_lluvia": s["apagadas_por_lluvia"],
            "resumen_terreno": s["opciones"].get("resumen_terreno"),
            "constantes": s["metadatos"].get("constantes"),
        },
        "parametros": s["parametros"],
    }


def cerrar(sesion_id: str) -> None:
    with _guard:
        _sesiones.pop(sesion_id, None)


def estado_del_almacen() -> dict:
    """Para el endpoint de estado: cuántas sesiones hay vivas."""
    with _guard:
        return {
            "sesiones_abiertas": len(_sesiones),
            "maximo": MAX_SESIONES,
            "vida_max_s": VIDA_MAX_S,
        }
