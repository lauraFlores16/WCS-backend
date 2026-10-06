"""
Trabajos en segundo plano lanzados desde el dashboard, con su estado guardado
en la tabla validacion_trabajos (así se puede consultar desde cualquier
pestaña o tras recargar la página).

Uno a la vez: el plan gratis de Render tiene 0,1 CPU y 512 MB, y dos descargas
o dos corridas simultáneas se estorbarían. Los demás esperan en cola.

Si el servidor se reinicia a mitad (un despliegue, o Render dormido), el
trabajo queda marcado como «interrumpido» y se puede relanzar.
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone

from ..almacen import db

_turno = threading.Semaphore(1)
_vivos: set[str] = set()
_cerrojo = threading.Lock()
SIN_LATIDO = timedelta(minutes=3)      # sin latido en 3 min = el proceso murió
LATIDO_S = 45


def _ahora():
    return datetime.now(timezone.utc)


def lanzar(tipo: str, objetivo: str, funcion, usuario: str | None = None) -> dict:
    """`funcion(avance)` hace el trabajo; `avance(fraccion, mensaje)` informa.
    Devuelve el trabajo ya existente si ese mismo objetivo sigue en marcha."""
    for t in db.vz_listar_trabajos(30):
        if t["objetivo"] == objetivo and t["estado"] in ("en_cola", "ejecutando") and not _muerto(t):
            return t
    tid = uuid.uuid4().hex[:12]
    t = {"id": tid, "tipo": tipo, "objetivo": objetivo, "estado": "en_cola", "progreso": 0.0,
         "mensaje": "En cola…", "log": [], "usuario": usuario}
    db.vz_guardar_trabajo(t)
    with _cerrojo:
        _vivos.add(tid)

    def correr():
        ultimo = [0.0]
        log: list = []

        def avance(frac: float, mensaje: str, forzar: bool = False):
            t["_progreso"], t["_mensaje"] = round(min(max(frac, 0), 1), 3), mensaje
            log.append({"t": _ahora().strftime("%H:%M:%S"), "m": mensaje})
            del log[:-60]
            if forzar or time.time() - ultimo[0] > 2.0:
                ultimo[0] = time.time()
                try:
                    db.vz_guardar_trabajo({**t, "estado": "ejecutando", "progreso": round(min(max(frac, 0), 1), 3),
                                           "mensaje": mensaje, "log": log})
                except Exception as e:  # noqa: BLE001
                    print(f"[trabajos] no se pudo guardar el avance: {e}")

        fin = threading.Event()

        def latido():
            # Aunque un paso tarde (una petición lenta, 30 repeticiones), la
            # fila se refresca: así otro proceso del servidor no lo da por muerto.
            while not fin.wait(LATIDO_S):
                try:
                    db.vz_guardar_trabajo({**t, "estado": "ejecutando" if t.get("_en_marcha") else "en_cola",
                                           "progreso": t.get("_progreso", 0.0),
                                           "mensaje": t.get("_mensaje", "En cola…"), "log": log})
                except Exception:  # noqa: BLE001
                    pass

        threading.Thread(target=latido, daemon=True).start()
        with _turno:
            t["_en_marcha"] = True
            try:
                avance(0.0, "Empezando…", True)
                resultado = funcion(avance)
                db.vz_guardar_trabajo({**t, "estado": "terminado", "progreso": 1.0, "log": log,
                                       "mensaje": (resultado or {}).get("mensaje", "Terminado"),
                                       "resultado": resultado})
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                log.append({"t": _ahora().strftime("%H:%M:%S"), "m": f"ERROR: {e}"})
                db.vz_guardar_trabajo({**t, "estado": "error", "log": log,
                                       "mensaje": "Falló", "error": f"{type(e).__name__}: {e}"})
            finally:
                fin.set()
                with _cerrojo:
                    _vivos.discard(tid)

    threading.Thread(target=correr, daemon=True, name=f"trabajo-{tid}").start()
    return t


def _muerto(t: dict) -> bool:
    try:
        visto = datetime.fromisoformat(str(t.get("actualizado_en")).replace("Z", "+00:00"))
    except ValueError:
        return True
    return _ahora() - visto > SIN_LATIDO


def estado(tid: str) -> dict | None:
    t = db.vz_leer_trabajo(tid)
    if not t:
        return None
    if t["estado"] in ("en_cola", "ejecutando") and _muerto(t):
        # Nadie lo actualiza desde hace minutos: el servidor se reinició
        # (despliegue o Render dormido). No va a terminar solo.
        t = {**t, "estado": "interrumpido",
             "mensaje": "El servidor se reinició y el trabajo se cortó. Vuelve a lanzarlo."}
        db.vz_guardar_trabajo(t)
    return t
