"""
============================================================================
ADAPTADOR SUPABASE — el almacén del sistema
============================================================================
Puerto de backend/almacen/db-supabase.js, ajustado al esquema `public` real
(el DDL que pasaste). Cada función tiene la misma firma que su gemela del
adaptador JSON, así que el resto del backend no sabe qué hay detrás.

Tres cosas que aquí se hacen mejor que en la versión Node:

  1. COLUMNAS EN BLANCO (`solo_columnas`). PostgREST devuelve 400 si le
     mandas una clave que no es columna. El motor produce alertas con
     `celda_id` y `creado_en`, que en la tabla no existen (`creada_en` sí).
     La versión Node insertaba el diccionario entero y habría fallado; aquí
     se mapea y se filtra en un solo sitio.
  2. BORRADO RESPETANDO LAS CLAVES AJENAS. `alertas` e `informes` apuntan a
     `escenarios` sin ON DELETE CASCADE, así que borrar un escenario sin
     limpiar antes sus hijos da error de FK. `borrar_escenario` los borra en
     orden.
  3. LISTADO BARATO. El historial ya no descarga el JSON completo de las
     iteraciones de cada escenario: lee las columnas resumidas
     (`area_final_ha`, `num_iteraciones`) y el nivel de alerta con una sola
     consulta agregada del lado del cliente.

Las alertas viven SOLO en la tabla `alertas` (que es para lo que está, con su
FK a `escenarios`), no duplicadas dentro del JSON del escenario: al leer un
escenario se rellenan desde ahí.
============================================================================
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional

from django.conf import settings

from ..errores import ErrorAPI
from . import supabase as sb
from .hash import es_hash, hashear
from .hash import verificar as verificar_hash
from .permisos_defecto import MATRIZ_DEFECTO, normalizar_matriz

MODO_ALMACEN = "supabase"

enc = sb.enc

# --- Columnas reales de cada tabla (del DDL) -------------------------------
COL_USUARIOS = ("id", "email", "password", "nombre", "rol", "activo",
                "ultimo_acceso", "creado_en", "orden")
COL_SESIONES = ("id", "usuario_id", "emitida_en", "expira_en", "revocada",
                "user_agent", "ip")
COL_ESCENARIOS = ("escenario_id", "nombre", "creado_por", "creado_en", "parametros",
                  "foco_coordenadas", "variables_promedio", "metadatos_motor",
                  "area_final_ha", "num_iteraciones", "iteraciones", "datos")
COL_ALERTAS = ("escenario_id", "origen", "nivel", "mensaje", "iteracion",
               "lat", "lon", "creada_en")
COL_CALIBRACIONES = ("fecha", "vigente", "f1", "metodo", "p_base", "constantes",
                     "detalle", "resultado")
COL_BITACORA = ("fecha", "usuario", "accion", "detalle", "tipo")
COL_INFORMES = ("escenario_id", "nombre", "html", "generado_por", "generado_en", "resumen")

TIPOS_BITACORA = {"auth", "sim", "user", "data", "alert", "report", "sys"}


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _limpio(valor: Any) -> Any:
    """NaN / Infinity no son JSON válido y PostgREST los rechaza."""
    if isinstance(valor, float) and not math.isfinite(valor):
        return None
    if isinstance(valor, dict):
        return {k: _limpio(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_limpio(v) for v in valor]
    return valor


# ===========================================================================
# Escenarios
# ===========================================================================
def _a_fila_escenario(e: dict) -> dict:
    """Reparte el escenario entre columnas indexables y el `datos` jsonb.

    Las iteraciones comprimidas (deltas) van a la columna `iteraciones`, que
    es justo para eso; `datos` se queda con lo que no tiene columna propia
    (descripcion, diagnostico, horas_serie_viento…). Las alertas NO van aquí:
    viven en su tabla.
    """
    propias = set(COL_ESCENARIOS) | {"iteraciones_delta", "alertas"}
    resto = {k: v for k, v in e.items() if k not in propias}

    deltas = e.get("iteraciones_delta") or e.get("iteraciones") or []
    # Número de pasos ejecutados = el índice de la última iteración.
    num_iter = e.get("num_iteraciones")
    if num_iter is None and deltas:
        num_iter = deltas[-1].get("iteracion", len(deltas) - 1)

    fila = {
        "escenario_id": e["escenario_id"],
        "nombre": e.get("nombre"),
        "creado_por": e.get("creado_por"),
        "creado_en": e.get("creado_en") or _ahora_iso(),
        "parametros": e.get("parametros"),
        "foco_coordenadas": e.get("foco_coordenadas"),
        "variables_promedio": e.get("variables_promedio"),
        "metadatos_motor": e.get("metadatos_motor"),
        "area_final_ha": e.get("area_final_ha"),
        "num_iteraciones": num_iter,
        "iteraciones": deltas,
        "datos": resto,
    }
    return _limpio(fila)


def _de_fila_escenario(f: Optional[dict]) -> Optional[dict]:
    if not f:
        return None
    fila = dict(f)
    datos = fila.pop("datos", None) or {}
    # La columna `iteraciones` guarda los deltas: se devuelve con el nombre que
    # espera el motor (`iteraciones_delta`), y sin dejar la clave `iteraciones`
    # puesta, porque reconstruir_iteraciones() la interpretaría como
    # iteraciones ya expandidas.
    deltas = fila.pop("iteraciones", None)
    escenario = {**fila, **datos}
    if deltas:
        escenario["iteraciones_delta"] = deltas
    return escenario


def guardar_escenario(escenario: dict) -> dict:
    sb.upsert("escenarios", _a_fila_escenario(escenario), on_conflict="escenario_id")
    return escenario


def listar_escenarios() -> list[dict]:
    filas = sb.select("escenarios", "select=*&order=creado_en.desc&limit=200")
    escenarios = [_de_fila_escenario(f) for f in filas]
    _adjuntar_alertas(escenarios)
    return escenarios


def listar_escenarios_resumen() -> list[dict]:
    """Listado del historial SIN traerse las iteraciones completas.

    Es lo que consume `GET /api/escenarios`. Con 200 escenarios de 24 pasos,
    `select=*` movería decenas de MB por la red en cada carga de la pantalla.
    """
    filas = sb.select(
        "escenarios",
        "select=escenario_id,nombre,creado_por,creado_en,parametros,"
        "area_final_ha,num_iteraciones&order=creado_en.desc&limit=200",
    )
    if not filas:
        return []

    # Nivel de alerta máximo por escenario, en UNA consulta.
    prioridad = {"roja": 3, "naranja": 2, "amarilla": 1}
    peor: dict[str, str] = {}
    for a in sb.select("alertas", "select=escenario_id,nivel&origen=eq.simulacion&limit=5000"):
        eid, nivel = a.get("escenario_id"), a.get("nivel")
        if not eid or not nivel:
            continue
        if prioridad.get(nivel, 0) > prioridad.get(peor.get(eid, ""), 0):
            peor[eid] = nivel

    return [{**f, "alerta_maxima": peor.get(f["escenario_id"])} for f in filas]


def obtener_escenario(id_: str) -> Optional[dict]:
    esc = _de_fila_escenario(
        sb.select_uno("escenarios", f"select=*&escenario_id=eq.{enc(id_)}"))
    if esc:
        _adjuntar_alertas([esc])
    return esc


def _adjuntar_alertas(escenarios: list[dict]) -> None:
    """Rellena `escenario["alertas"]` desde la tabla `alertas`."""
    for esc in escenarios:
        if not esc:
            continue
        esc["alertas"] = alertas_de_escenario(esc["escenario_id"])


def borrar_escenario(id_: str) -> None:
    # `alertas.escenario_id` e `informes.escenario_id` son claves ajenas sin
    # ON DELETE CASCADE: hay que vaciar los hijos antes que el padre.
    filtro = f"escenario_id=eq.{enc(id_)}"
    sb.eliminar("alertas", filtro)
    sb.eliminar("informes", filtro)
    sb.eliminar("escenarios", filtro)


# ===========================================================================
# Alertas
# ===========================================================================
def _a_fila_alerta(a: dict, origen: str) -> dict:
    fila = dict(a)
    # El motor emite `creado_en`; la columna se llama `creada_en`.
    if "creada_en" not in fila and fila.get("creado_en"):
        fila["creada_en"] = fila["creado_en"]
    fila["origen"] = fila.get("origen") or origen
    fila.setdefault("creada_en", _ahora_iso())
    # `celda_id` no tiene columna: la celda concreta ya va nombrada dentro de
    # `mensaje`, y el detalle completo queda en el JSON del escenario.
    return _limpio(sb.solo_columnas(fila, COL_ALERTAS))


def guardar_alertas(lista: list[dict]) -> None:
    if not lista:
        return
    sb.insertar("alertas", [_a_fila_alerta(a, "simulacion") for a in lista])


def alertas_de_escenario(escenario_id: str) -> list[dict]:
    return sb.select(
        "alertas",
        f"select=*&escenario_id=eq.{enc(escenario_id)}&order=iteracion.asc&limit=2000")


def alertas_de_riesgo(limite: int = 50) -> list[dict]:
    return sb.select(
        "alertas", f"select=*&origen=eq.riesgo&order=creada_en.desc&limit={int(limite)}")


def reemplazar_alertas_riesgo(lista: list[dict]) -> list[dict]:
    # Se recalculan en cada refresco: se sustituyen las anteriores para no
    # acumular duplicados.
    sb.eliminar("alertas", "origen=eq.riesgo")
    if not lista:
        return []
    return sb.insertar("alertas", [_a_fila_alerta(a, "riesgo") for a in lista])


# ===========================================================================
# Calibración
# ===========================================================================
def guardar_calibracion(resultado: dict) -> dict:
    # La nueva pasa a ser la vigente; las demás dejan de serlo.
    sb.actualizar_varias("calibraciones", "vigente=eq.true", {"vigente": False})
    sb.insertar("calibraciones", _limpio({
        "fecha": resultado.get("fecha") or _ahora_iso(),
        "vigente": True,
        "f1": resultado.get("f1"),
        "metodo": resultado.get("metodo"),
        "p_base": resultado.get("p_base"),
        "constantes": resultado.get("constantes"),
        "detalle": resultado.get("detalle"),
        "resultado": resultado,
    }))
    return resultado


def leer_calibracion() -> Optional[dict]:
    fila = sb.select_uno("calibraciones", "select=resultado&vigente=eq.true")
    return (fila or {}).get("resultado")


def leer_historial_calibracion() -> list[dict]:
    return sb.select("calibraciones",
                     "select=fecha,f1,metodo,p_base,constantes&order=fecha.desc&limit=50")


# ===========================================================================
# Bitácora
# ===========================================================================
def registrar_bitacora(entrada: dict) -> None:
    tipo = entrada.get("tipo") or "data"
    if tipo not in TIPOS_BITACORA:
        # La tabla tiene un CHECK; un tipo nuevo tiraría la petición entera y
        # perderíamos el registro. Mejor guardarlo como 'data'.
        tipo = "data"
    sb.insertar("bitacora", {
        "fecha": _ahora_iso(),
        "usuario": entrada.get("usuario"),
        "accion": entrada.get("accion") or "(sin acción)",
        "detalle": entrada.get("detalle"),
        "tipo": tipo,
    })


def listar_bitacora() -> list[dict]:
    return sb.select("bitacora", "select=*&order=fecha.desc&limit=500")


# ===========================================================================
# Informes
# ===========================================================================
def guardar_informe(informe: dict) -> dict:
    return sb.insertar("informes", _limpio({
        "escenario_id": informe.get("escenario_id"),
        "nombre": informe["nombre"],
        "html": informe["html"],
        "generado_por": informe.get("generado_por"),
        "generado_en": _ahora_iso(),
        "resumen": informe.get("resumen"),
    }))


def listar_informes() -> list[dict]:
    # Sin el HTML: la lista solo necesita las fichas.
    return sb.select(
        "informes",
        "select=id,escenario_id,nombre,generado_por,generado_en,resumen"
        "&order=generado_en.desc&limit=200")


def obtener_informe(id_: str) -> Optional[dict]:
    return sb.select_uno("informes", f"select=*&id=eq.{enc(id_)}")


def informe_de_escenario(escenario_id: str) -> Optional[dict]:
    return sb.select_uno(
        "informes",
        f"select=*&escenario_id=eq.{enc(escenario_id)}&order=generado_en.desc")


# ===========================================================================
# Sesiones
# ===========================================================================
def crear_sesion(sesion: dict) -> dict:
    sb.insertar("sesiones", sb.solo_columnas({
        "id": sesion["id"],
        "usuario_id": sesion["usuario_id"],
        "emitida_en": sesion.get("emitida_en") or _ahora_iso(),
        "expira_en": sesion["expira_en"],
        "revocada": False,
        "user_agent": sesion.get("user_agent"),
        "ip": sesion.get("ip"),
    }, COL_SESIONES))
    return sesion


def obtener_sesion(id_: str) -> Optional[dict]:
    return sb.select_uno("sesiones", f"select=*&id=eq.{enc(id_)}")


def revocar_sesion(id_: str) -> None:
    sb.actualizar("sesiones", f"id=eq.{enc(id_)}", {"revocada": True})


def revocar_sesiones_de_usuario(usuario_id: str) -> None:
    sb.actualizar_varias(
        "sesiones", f"usuario_id=eq.{enc(usuario_id)}&revocada=eq.false",
        {"revocada": True})


def limpiar_sesiones() -> int:
    ahora = _ahora_iso()
    borradas = sb.eliminar("sesiones", f"or=(revocada.eq.true,expira_en.lt.{enc(ahora)})")
    return len(borradas)


# ===========================================================================
# Usuarios
# ===========================================================================
USUARIOS_DEMO = [
    {"email": "admin@demo.sipro.com", "password": "demo1234", "nombre": "Administradora Demo", "rol": "administrador"},
    {"email": "analista@demo.sipro.com", "password": "demo1234", "nombre": "Analista Demo", "rol": "analista"},
    {"email": "ugr@demo.sipro.com", "password": "demo1234", "nombre": "UGR Demo", "rol": "ugr"},
    {"email": "brigada@demo.sipro.com", "password": "demo1234", "nombre": "Brigada Demo", "rol": "brigada"},
]
_SEMILLA_USUARIOS = [
    {"id": f"u-{u['rol']}", "email": u["email"], "password": u["password"], "nombre": u["nombre"],
     "rol": u["rol"], "activo": True, "creado_en": "2025-01-01T00:00:00Z", "orden": i}
    for i, u in enumerate(USUARIOS_DEMO)
]


def migrar_passwords() -> int:
    """Siembra los usuarios demo si la tabla está vacía y hashea lo que siga en claro."""
    usuarios = sb.select("usuarios", "select=*&order=orden.asc")
    if not usuarios:
        if not settings.SEMBRAR_USUARIOS_DEMO:
            return 0
        sb.upsert("usuarios",
                  [{**u, "password": hashear(u["password"])} for u in _SEMILLA_USUARIOS],
                  on_conflict="id")
        return len(_SEMILLA_USUARIOS)

    cambiadas = 0
    for u in usuarios:
        if not es_hash(u.get("password")):
            sb.actualizar("usuarios", f"id=eq.{enc(u['id'])}",
                          {"password": hashear(u["password"])})
            cambiadas += 1
    return cambiadas


def listar_usuarios(con_password: bool = False) -> list[dict]:
    cols = "*" if con_password else "id,email,nombre,rol,activo,ultimo_acceso,creado_en,orden"
    return sb.select("usuarios", f"select={cols}&order=orden.asc.nullslast&order=creado_en.asc")


def _sin_password(u: Optional[dict]) -> Optional[dict]:
    if not u:
        return None
    return {k: v for k, v in u.items() if k != "password"}


def crear_usuario(datos: dict) -> dict:
    existe = sb.select_uno("usuarios", f"select=id&email=eq.{enc(datos.get('email'))}")
    if existe:
        raise ErrorAPI("Ya existe un usuario con ese correo.", estado_http=400)
    nuevo = {
        "id": "u-" + str(int(datetime.now().timestamp() * 1000)),
        "email": datos.get("email"),
        "password": hashear(datos.get("password") or "demo1234"),
        "nombre": datos.get("nombre"),
        "rol": datos.get("rol"),
        "activo": bool(datos["activo"]) if datos.get("activo") is not None else True,
        "creado_en": _ahora_iso(),
    }
    sb.insertar("usuarios", sb.solo_columnas(nuevo, COL_USUARIOS))
    return _sin_password(nuevo)


def actualizar_usuario(id_: str, cambios: dict) -> dict:
    permitidos = {}
    if cambios.get("nombre") is not None:
        permitidos["nombre"] = cambios["nombre"]
    if cambios.get("rol") is not None:
        permitidos["rol"] = cambios["rol"]
    if cambios.get("activo") is not None:
        permitidos["activo"] = cambios["activo"]
    if not permitidos:
        actual = sb.select_uno("usuarios", f"select=*&id=eq.{enc(id_)}")
        if not actual:
            raise ErrorAPI("Usuario no encontrado", estado_http=404)
        return _sin_password(actual)

    fila = sb.actualizar("usuarios", f"id=eq.{enc(id_)}", permitidos)
    if not fila:
        raise ErrorAPI("Usuario no encontrado", estado_http=404)
    return _sin_password(fila)


def desactivar_usuario(id_: str) -> dict:
    return actualizar_usuario(id_, {"activo": False})


def verificar_credenciales(email: str, password: str) -> Optional[dict]:
    u = sb.select_uno("usuarios", f"select=*&email=eq.{enc(email)}")
    if not u or not verificar_hash(password, u.get("password")):
        return None
    if u.get("activo") is False:
        raise ErrorAPI("Esta cuenta está desactivada. Contacta con el administrador.",
                       estado_http=403)
    return {"id": u["id"], "usuario_id": u["email"], "nombre": u["nombre"],
            "rol": u["rol"], "email": u["email"]}


def marcar_ultimo_acceso(id_: str) -> None:
    sb.actualizar("usuarios", f"id=eq.{enc(id_)}", {"ultimo_acceso": _ahora_iso()})


def restablecer_password(id_: str, nueva_password: str) -> dict:
    fila = sb.actualizar("usuarios", f"id=eq.{enc(id_)}",
                         {"password": hashear(nueva_password)})
    if not fila:
        raise ErrorAPI("Usuario no encontrado", estado_http=404)
    return _sin_password(fila)


# ===========================================================================
# Matriz de permisos por rol (una sola fila JSONB en la tabla `permisos`)
# ===========================================================================
def leer_matriz_permisos() -> dict:
    fila = sb.select_uno("permisos", "select=matriz&id=eq.actual")
    return normalizar_matriz((fila or {}).get("matriz") or MATRIZ_DEFECTO)


def guardar_matriz_permisos(matriz: dict) -> dict:
    normalizada = normalizar_matriz(matriz)
    sb.upsert("permisos",
              {"id": "actual", "matriz": normalizada, "actualizado_en": _ahora_iso()},
              on_conflict="id")
    return normalizada
