"""
============================================================================
COLA DE PETICIONES SALIENTES — puerto de backend/lib/cola.js
============================================================================
Aquí se arregla el mismo problema que en el prototipo Node: un solo proceso
llama a las APIs externas (Open-Meteo, Overpass, NASA FIRMS), con ritmo
controlado por host, reintentos con espera exponencial y un cortacircuitos
que deja de intentar tras varios fallos seguidos.

Django (WSGI, hilos) no tiene un *event loop* como Node, pero el efecto es el
mismo: cada host tiene un `threading.Lock` que serializa las peticiones que
le llegan, así que un segundo hilo pidiendo el mismo host espera su turno en
vez de dispararse en paralelo — el equivalente de la cola de tareas del
original.
============================================================================
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Optional
from urllib.parse import urlparse

import requests

# La elevación tiene presupuesto PROPIO aunque comparta host con el pronóstico.
# Si no, un lote de DEM que se pasa de cuota abre el cortacircuitos de
# api.open-meteo.com entero y te deja también sin meteorología, que es lo que
# pasaba: dos servicios distintos castigados por el consumo de uno solo.
LIMITES = {
    "api.open-meteo.com": {"intervalo_s": 0.12, "por_minuto": 120},
    "api.open-meteo.com/v1/elevation": {"intervalo_s": 0.5, "por_minuto": 40},
    "archive-api.open-meteo.com": {"intervalo_s": 0.25, "por_minuto": 120},
    "overpass-api.de": {"intervalo_s": 2.0, "por_minuto": 12},
    "firms.modaps.eosdis.nasa.gov": {"intervalo_s": 1.0, "por_minuto": 30},
    "_defecto": {"intervalo_s": 0.3, "por_minuto": 100},
}

# Overpass pide explícitamente un User-Agent descriptivo y rechaza los
# genéricos (`python-requests/2.x`) — es una de las causas de los 406.
USER_AGENT = os.environ.get(
    "HTTP_USER_AGENT",
    "SIPRO-FIRE/2.0 (sistema academico de prediccion de incendios; Apolo, Bolivia)",
)

REINTENTOS_MAX = 4

# Un 429 NO se reintenta como un fallo de red. Reintentar un límite de cuota
# es alimentar el propio problema: cada reintento cuenta contra el límite que
# intentas sortear. Con 17 lotes de DEM, 4 reintentos por lote eran hasta 85
# peticiones y minutos de espera para acabar igual. Con 1, el cortacircuitos
# se abre en ~6 peticiones y se corta la sangría.
REINTENTOS_LIMITE_TASA = 1
ESPERA_BASE_S = 1.5
ESPERA_MAX_S = 20  # tope: si Retry-After pide más, se abandona y se reintenta luego

FALLOS_PARA_ABRIR = 3
ENFRIAMIENTO_S = {429: 90, 503: 60, "defecto": 120}


class ErrorExterno(Exception):
    def __init__(self, mensaje: str, estado: int = 503, servicio: Optional[str] = None,
                 reintentar_en_s: Optional[int] = None):
        super().__init__(mensaje)
        self.estado_http = estado
        self.servicio = servicio
        self.reintentar_en_s = reintentar_en_s


class _EstadoHost:
    def __init__(self):
        self.lock = threading.Lock()
        self.ultima_s = 0.0
        self.marcas: list[float] = []
        self.fallos_seguidos = 0
        self.abierto_hasta = 0.0
        self.ultimo_error: Optional[str] = None
        self.ultimo_ok_s: Optional[float] = None


_estados: dict[str, _EstadoHost] = {}
_estados_guard = threading.Lock()


def _estado_de(host: str) -> _EstadoHost:
    with _estados_guard:
        if host not in _estados:
            _estados[host] = _EstadoHost()
        return _estados[host]


def clave_limite(url: str) -> str:
    """Con qué presupuesto se contabiliza esta URL.

    Normalmente el host. La excepción es la API de elevación de Open-Meteo:
    comparte host con el pronóstico pero se consume a ráfagas de ~17 lotes por
    ventana de DEM, así que lleva contador y cortacircuitos propios. Sin esto,
    quedarse sin cuota de elevación te dejaba también sin meteorología.
    """
    partes = urlparse(url)
    if partes.netloc == "api.open-meteo.com" and partes.path.startswith("/v1/elevation"):
        return "api.open-meteo.com/v1/elevation"
    return partes.netloc


def pedir(url: str, metodo: str = "GET", headers: Optional[dict] = None,
          data: Optional[Any] = None, etiqueta: Optional[str] = None,
          timeout_s: float = 30.0) -> requests.Response:
    host = clave_limite(url)
    estado = _estado_de(host)
    servicio = etiqueta or host

    if estado.abierto_hasta > time.time():
        segundos = int(estado.abierto_hasta - time.time()) + 1
        raise ErrorExterno(
            f"{servicio} no está respondiendo. Se reintentará en {segundos} s.",
            estado=503, servicio=servicio, reintentar_en_s=segundos,
        )

    with estado.lock:
        # Puede haberse abierto el circuito mientras esperábamos el turno.
        if estado.abierto_hasta > time.time():
            segundos = int(estado.abierto_hasta - time.time()) + 1
            raise ErrorExterno(
                f"{servicio} no está respondiendo. Se reintentará en {segundos} s.",
                estado=503, servicio=servicio, reintentar_en_s=segundos,
            )

        limite = LIMITES.get(host, LIMITES["_defecto"])

        desde_ultima = time.time() - estado.ultima_s
        if desde_ultima < limite["intervalo_s"]:
            time.sleep(limite["intervalo_s"] - desde_ultima)

        ahora = time.time()
        estado.marcas = [t for t in estado.marcas if ahora - t < 60]
        if len(estado.marcas) >= limite["por_minuto"]:
            esperar = 60 - (ahora - estado.marcas[0]) + 0.05
            print(f"[cola] {host}: tope por minuto alcanzado, esperando {round(esperar)}s")
            time.sleep(max(esperar, 0))

        estado.ultima_s = time.time()
        estado.marcas.append(estado.ultima_s)

        try:
            respuesta = _ejecutar_con_reintentos(url, metodo, headers, data, servicio, timeout_s)
            estado.fallos_seguidos = 0
            estado.ultimo_error = None
            estado.ultimo_ok_s = time.time()
            return respuesta
        except ErrorExterno as e:
            estado.fallos_seguidos += 1
            estado.ultimo_error = str(e)
            if estado.fallos_seguidos >= FALLOS_PARA_ABRIR:
                espera = ENFRIAMIENTO_S.get(e.estado_http, ENFRIAMIENTO_S["defecto"])
                estado.abierto_hasta = time.time() + espera
                print(f"[cola] {host}: {estado.fallos_seguidos} fallos seguidos, "
                      f"se deja de intentar durante {espera}s")
            raise


def _ejecutar_con_reintentos(url: str, metodo: str, headers: Optional[dict], data: Optional[Any],
                              etiqueta: str, timeout_s: float) -> requests.Response:
    cabeceras = {"User-Agent": USER_AGENT, **(headers or {})}
    intento = 0

    while True:
        try:
            r = requests.request(metodo, url, headers=cabeceras, data=data, timeout=timeout_s)
        except requests.RequestException as e:
            # Fallo de red o timeout: aquí sí compensa insistir, no estamos
            # gastando cuota de nadie.
            if intento >= REINTENTOS_MAX:
                raise ErrorExterno(f"No se pudo contactar con {etiqueta}: {e}",
                                   estado=504, servicio=etiqueta) from e
            time.sleep(ESPERA_BASE_S * (2 ** intento))
            intento += 1
            continue

        if r.status_code in (429, 503, 504):
            # 429 = límite de cuota. Cada reintento cuenta contra ese mismo
            # límite, así que se insiste UNA vez y se cede: el cortacircuitos
            # de arriba se encarga de no volver a intentarlo en un rato.
            es_limite_tasa = r.status_code == 429
            tope = REINTENTOS_LIMITE_TASA if es_limite_tasa else REINTENTOS_MAX

            cabecera = r.headers.get("retry-after")
            try:
                espera = float(cabecera) if cabecera else ESPERA_BASE_S * (2 ** intento)
            except ValueError:
                espera = ESPERA_BASE_S * (2 ** intento)

            # Si el servidor pide esperar más de lo razonable, no se bloquea la
            # petición del usuario: se abandona y ya se reintentará más tarde.
            if intento >= tope or espera > ESPERA_MAX_S:
                raise ErrorExterno(
                    f"{etiqueta} está limitando las peticiones (HTTP {r.status_code}).",
                    estado=503, servicio=etiqueta, reintentar_en_s=int(espera) + 1,
                )

            print(f"[cola] {etiqueta} devolvió {r.status_code}; reintento {intento + 1}/{tope} "
                  f"en {round(espera)}s")
            time.sleep(espera)
            intento += 1
            continue

        if not r.ok:
            raise ErrorExterno(f"{etiqueta} respondió {r.status_code}: {r.text[:200]}",
                                estado=502, servicio=etiqueta)
        return r


def estado_colas() -> dict:
    salida = {}
    ahora = time.time()
    with _estados_guard:
        items = list(_estados.items())
    for host, e in items:
        abierto = e.abierto_hasta > ahora
        limite = LIMITES.get(host, LIMITES["_defecto"])
        salida[host] = {
            "disponible": not abierto,
            "peticiones_ultimo_minuto": len([t for t in e.marcas if ahora - t < 60]),
            "limite_por_minuto": limite["por_minuto"],
            "fallos_seguidos": e.fallos_seguidos,
            "ultimo_error": e.ultimo_error,
            "reintenta_en_s": int(e.abierto_hasta - ahora) if abierto else 0,
            "ultimo_ok": (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(e.ultimo_ok_s)) if e.ultimo_ok_s else None
            ),
        }
    return salida
