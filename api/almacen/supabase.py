"""
============================================================================
CLIENTE SUPABASE — vía API REST (PostgREST), sin SDK
============================================================================
Puerto de backend/almacen/supabase.js. No usamos `supabase-py` a propósito:
para lo que necesita el backend (unos cuantos CRUD sobre las tablas del
esquema `public`) basta con la API REST que Supabase ya expone, y así la
única dependencia HTTP del proyecto sigue siendo `requests`.

El backend se conecta con la SERVICE ROLE KEY, que omite RLS. Esa clave
NUNCA sale del servidor: el navegador habla con Django por HTTP y jamás con
Supabase directamente.
============================================================================
"""
from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import quote

import requests
from django.conf import settings

from ..errores import ErrorAPI

_sesion_http: Optional[requests.Session] = None


def enc(valor: Any) -> str:
    """Escapa un valor para meterlo en un filtro PostgREST (`id=eq.<valor>`)."""
    return quote(str(valor), safe="")


def configurado() -> bool:
    return bool(settings.SUPABASE["url"] and settings.SUPABASE["service_key"])


def _cabeceras() -> dict:
    clave = settings.SUPABASE["service_key"]
    esquema = settings.SUPABASE["esquema"]
    return {
        "apikey": clave,
        "Authorization": f"Bearer {clave}",
        "Content-Type": "application/json",
        # Esquema donde viven las tablas. `public` es el que Supabase expone
        # por defecto; mandarlo explícito no molesta y documenta la intención.
        "Accept-Profile": esquema,
        "Content-Profile": esquema,
    }


def _http() -> requests.Session:
    global _sesion_http
    if _sesion_http is None:
        # Una sola sesión reutiliza la conexión TCP/TLS con Supabase en vez de
        # renegociarla en cada consulta.
        _sesion_http = requests.Session()
    return _sesion_http


def _url(tabla: str, query: str = "") -> str:
    base = settings.SUPABASE["url"]
    return f"{base}/rest/v1/{tabla}" + (f"?{query}" if query else "")


# --- Traducción de los errores de PostgREST -------------------------------
# PostgREST contesta con códigos propios que no dicen nada por sí solos, y
# varios de ellos apuntan a un problema de configuración —no de datos— que
# tiene una solución concreta. Traducirlos aquí evita el rato perdido buscando
# tablas que sí existen.
def _pista(codigo: str, mensaje: str, estado: int) -> str:
    base = settings.SUPABASE["url"]

    if codigo == "PGRST125":
        # Ruta con segmentos de más. Casi siempre: SUPABASE_URL ya incluía
        # /rest/v1, así que la petición sale a /rest/v1/rest/v1/<tabla>.
        return (
            "\n  → La ruta de la petición no es válida. Revisa SUPABASE_URL en "
            "backend_django/.env:\n"
            f"    debe ser SOLO la URL del proyecto ({base}), sin /rest/v1 al "
            "final.\n"
            "    En Supabase → Project Settings → API es el campo «Project URL», "
            "no el de «RESTful API»."
        )

    if codigo == "PGRST205" or "schema cache" in mensaje:
        return (
            "\n  → La tabla no está en el esquema que se está consultando. "
            "Comprueba que\n"
            "    ejecutaste el DDL del proyecto en el SQL Editor de Supabase y "
            "que las\n"
            f"    tablas están en el esquema «{settings.SUPABASE['esquema']}» "
            "(SUPABASE_ESQUEMA)."
        )

    if estado == 401 or codigo in ("PGRST301", "42501"):
        return (
            "\n  → Supabase rechazó la clave. En backend_django/.env, "
            "SUPABASE_SERVICE_KEY\n"
            "    tiene que ser la «service_role (secret)», no la «anon public»: "
            "la anon\n"
            "    está sujeta a RLS y no ve estas tablas.\n"
            "    Project Settings → API → Project API keys."
        )

    if codigo == "PGRST102":
        return (
            "\n  → Las filas del lote no tienen las mismas claves. Debería "
            "haberlo evitado\n"
            "    homogeneizar(); si sale esto, hay un INSERT que no pasa por ahí."
        )

    return ""


def _ejecutar(metodo: str, tabla: str, query: str = "", cuerpo: Any = None,
              cabeceras: Optional[dict] = None) -> Any:
    if not configurado():
        raise ErrorAPI(
            "Supabase no está configurado: faltan SUPABASE_URL y/o "
            "SUPABASE_SERVICE_KEY en backend_django/.env",
            estado_http=503, servicio="Supabase",
        )

    todas = {**_cabeceras(), **(cabeceras or {})}
    try:
        r = _http().request(
            metodo, _url(tabla, query), headers=todas,
            data=json.dumps(cuerpo, default=str) if cuerpo is not None else None,
            timeout=settings.SUPABASE["timeout_s"],
        )
    except requests.RequestException as e:
        raise ErrorAPI(f"No se pudo contactar con Supabase: {e}",
                       estado_http=503, servicio="Supabase") from e

    if not r.ok:
        codigo = ""
        mensaje = ""
        try:
            cuerpo_error = r.json()
            if isinstance(cuerpo_error, dict):
                codigo = str(cuerpo_error.get("code") or "")
                mensaje = str(cuerpo_error.get("message") or "")
        except ValueError:
            pass
        raise ErrorAPI(
            f"Supabase {metodo} {tabla} → {r.status_code}: {r.text[:300]}"
            + _pista(codigo, mensaje, r.status_code),
            estado_http=502, servicio="Supabase",
        )

    if r.status_code == 204 or not r.text:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def homogeneizar(filas: list[dict]) -> list[dict]:
    """Iguala las claves de todas las filas de un lote, rellenando con None.

    PostgREST construye UN solo INSERT con UNA sola lista de columnas, así que
    exige que todos los objetos del lote tengan exactamente el mismo conjunto
    de claves; si no, responde:

        PGRST102 · "All object keys must match"

    Y es justo lo que produce el motor: las alertas rojas llevan `lat`/`lon`
    (apuntan a una celda concreta) y las amarillas/naranjas no (hablan del
    incendio entero). Rellenar los huecos con None deja las columnas que
    sobran a NULL, que es exactamente lo que queremos.
    """
    if len(filas) < 2:
        return filas
    todas = set()
    for f in filas:
        todas.update(f.keys())
    return [{clave: f.get(clave) for clave in todas} for f in filas]


# --- Operaciones de alto nivel --------------------------------------------
def select(tabla: str, query: str = "") -> list:
    """SELECT. `query` es una cadena PostgREST: 'order=creado_en.desc&limit=10'."""
    return _ejecutar("GET", tabla, query) or []


def select_uno(tabla: str, query: str = "") -> Optional[dict]:
    """SELECT de una sola fila (o None)."""
    separador = "&" if query else ""
    filas = _ejecutar("GET", tabla, f"{query}{separador}limit=1")
    return filas[0] if filas else None


def insertar(tabla: str, fila: Any) -> Any:
    """INSERT. Devuelve la fila insertada (o la lista, si se insertó una lista)."""
    es_lista = isinstance(fila, list)
    cuerpo = homogeneizar(fila) if es_lista else [fila]
    filas = _ejecutar("POST", tabla, cuerpo=cuerpo,
                      cabeceras={"Prefer": "return=representation"})
    if es_lista:
        return filas or []
    return filas[0] if filas else None


def upsert(tabla: str, fila: Any, on_conflict: str = "id") -> Any:
    """UPSERT (INSERT o UPDATE por conflicto de clave)."""
    es_lista = isinstance(fila, list)
    cuerpo = homogeneizar(fila) if es_lista else [fila]
    filas = _ejecutar("POST", tabla, query=f"on_conflict={on_conflict}",
                      cuerpo=cuerpo,
                      cabeceras={"Prefer": "resolution=merge-duplicates,return=representation"})
    if es_lista:
        return filas or []
    return filas[0] if filas else None


def actualizar(tabla: str, filtro: str, cambios: dict) -> Optional[dict]:
    """UPDATE con filtro PostgREST ('id=eq.u-123'). Devuelve la fila resultante."""
    filas = _ejecutar("PATCH", tabla, query=filtro, cuerpo=cambios,
                      cabeceras={"Prefer": "return=representation"})
    return filas[0] if filas else None


def actualizar_varias(tabla: str, filtro: str, cambios: dict) -> list:
    """UPDATE que puede afectar a varias filas. Devuelve todas las afectadas."""
    return _ejecutar("PATCH", tabla, query=filtro, cuerpo=cambios,
                     cabeceras={"Prefer": "return=representation"}) or []


def eliminar(tabla: str, filtro: str) -> list:
    """DELETE con filtro. Devuelve las filas borradas (para poder contarlas)."""
    return _ejecutar("DELETE", tabla, query=filtro,
                     cabeceras={"Prefer": "return=representation"}) or []


def solo_columnas(fila: dict, columnas: tuple[str, ...]) -> dict:
    """Deja pasar únicamente las claves que existen como columna en la tabla.

    PostgREST devuelve 400 si le mandas una clave que no es columna, y el
    motor produce diccionarios con campos extra (`celda_id` en las alertas,
    por ejemplo). Filtrar aquí evita que un campo nuevo del motor rompa el
    guardado, y deja el punto de mapeo en un solo sitio.
    """
    return {k: v for k, v in fila.items() if k in columnas}


# ---------------------------------------------------------------------------
# Comprobación de arranque
# ---------------------------------------------------------------------------
TABLAS_ESPERADAS = (
    "usuarios", "sesiones", "escenarios", "alertas",
    "calibraciones", "bitacora", "informes", "permisos",
)


def comprobar_conexion() -> dict:
    """Consulta trivial contra cada tabla: confirma URL, clave y esquema.

    Devuelve {tabla: None | "mensaje de error"} para que el comando
    `verificar_supabase` pueda decir exactamente qué falta.
    """
    resultado: dict[str, Optional[str]] = {}
    for tabla in TABLAS_ESPERADAS:
        try:
            _ejecutar("GET", tabla, "select=*&limit=1")
            resultado[tabla] = None
        except ErrorAPI as e:
            resultado[tabla] = str(e)
    return resultado
