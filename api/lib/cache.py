"""
============================================================================
CACHÉ EN DOS NIVELES + DEDUPLICACIÓN — puerto de backend/lib/cache.js
============================================================================
  1. MEMORIA: lo consultado hace poco se responde en microsegundos.
  2. DISCO: sobrevive a reiniciar el servidor. El DEM no cambia nunca, así
     que se guarda sin caducidad.
  3. VUELO ÚNICO (single-flight): si llegan varias peticiones idénticas
     mientras la primera todavía está en curso, todas esperan el mismo
     resultado en vez de disparar varias llamadas externas. Aquí se logra
     con un `threading.Lock` por clave (Django puede atender peticiones en
     hilos concurrentes).
============================================================================
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from django.conf import settings

_memoria: dict[str, dict] = {}
_memoria_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_de(clave: str) -> threading.Lock:
    with _locks_guard:
        if clave not in _locks:
            _locks[clave] = threading.Lock()
        return _locks[clave]


def _ruta_disco(clave: str) -> Path:
    h = hashlib.sha1(clave.encode("utf-8")).hexdigest()
    return Path(settings.CACHE_DIR) / f"{h}.json"


def _leer_disco(clave: str, ignorar_caducidad: bool = False) -> Optional[dict]:
    try:
        d = json.loads(_ruta_disco(clave).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not ignorar_caducidad and d.get("expira") and time.time() * 1000 > d["expira"]:
        return None
    return d


def _escribir_disco(clave: str, valor: Any, expira_ms: Optional[float]) -> None:
    try:
        Path(settings.CACHE_DIR).mkdir(parents=True, exist_ok=True)
        _ruta_disco(clave).write_text(
            json.dumps({"clave": clave, "valor": valor, "expira": expira_ms, "guardado": time.time() * 1000}),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"[cache] no se pudo escribir en disco: {e}")


@dataclass
class ResultadoCache:
    valor: Any
    origen: str  # memoria | disco | red | en-vuelo | cache-caducada
    edad_ms: float
    error: Optional[Exception] = None


def con_cache(clave: str, ttl_segundos: Optional[float], productor: Callable[[], Any],
              disco: bool = True) -> ResultadoCache:
    ttl_ms = ttl_segundos * 1000 if ttl_segundos else None

    with _memoria_guard:
        en_memoria = _memoria.get(clave)
        if en_memoria and (not en_memoria["expira"] or time.time() * 1000 < en_memoria["expira"]):
            return ResultadoCache(en_memoria["valor"], "memoria", time.time() * 1000 - en_memoria["guardado"])

    lock = _lock_de(clave)
    ya_ocupado = lock.locked()
    with lock:
        # Puede que, mientras esperábamos el lock, otro hilo ya haya dejado el
        # valor en memoria (esto es el "vuelo único").
        with _memoria_guard:
            en_memoria = _memoria.get(clave)
            if en_memoria and (not en_memoria["expira"] or time.time() * 1000 < en_memoria["expira"]):
                origen = "en-vuelo" if ya_ocupado else "memoria"
                return ResultadoCache(en_memoria["valor"], origen, time.time() * 1000 - en_memoria["guardado"])

        if disco:
            en_disco = _leer_disco(clave)
            if en_disco:
                with _memoria_guard:
                    _memoria[clave] = {"valor": en_disco["valor"], "expira": en_disco.get("expira"),
                                        "guardado": en_disco["guardado"]}
                return ResultadoCache(en_disco["valor"], "disco", time.time() * 1000 - en_disco["guardado"])

        valor = productor()
        expira = time.time() * 1000 + ttl_ms if ttl_ms else None
        guardado = time.time() * 1000
        with _memoria_guard:
            _memoria[clave] = {"valor": valor, "expira": expira, "guardado": guardado}
        if disco:
            _escribir_disco(clave, valor, expira)
        return ResultadoCache(valor, "red", 0)


def con_cache_tolerante(clave: str, ttl_segundos: Optional[float], productor: Callable[[], Any],
                         disco: bool = True) -> ResultadoCache:
    """Igual que con_cache, pero si el productor falla y hay una copia
    CADUCADA, la devuelve marcada como tal en lugar de fallar."""
    try:
        return con_cache(clave, ttl_segundos, productor, disco)
    except Exception as error:  # noqa: BLE001
        with _memoria_guard:
            rancio = _memoria.get(clave)
        if not rancio:
            rancio = _leer_disco(clave, ignorar_caducidad=True)
        if rancio:
            return ResultadoCache(rancio["valor"], "cache-caducada",
                                   time.time() * 1000 - rancio.get("guardado", 0), error=error)
        raise


def invalidar(prefijo: str) -> None:
    with _memoria_guard:
        for clave in list(_memoria.keys()):
            if clave.startswith(prefijo):
                del _memoria[clave]


def estado_cache() -> dict:
    with _memoria_guard:
        entradas = len(_memoria)
    with _locks_guard:
        en_vuelo = sum(1 for l in _locks.values() if l.locked())
    return {"entradas_en_memoria": entradas, "peticiones_en_vuelo": en_vuelo}
