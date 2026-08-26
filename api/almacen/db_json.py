"""
============================================================================
ALMACÉN — JSON en disco — puerto de backend/almacen/db-json.js
============================================================================
⚠️  NO ESTÁ CONECTADO. El almacén del sistema es Supabase (db_supabase.py).
    `db.py` no cae a este archivo automáticamente, a propósito: así nunca se
    escribe en disco creyendo que se está escribiendo en la base de datos.

Se conserva porque implementa el MISMO contrato y sirve para dos cosas:
  · Leer o migrar los datos de un despliegue anterior que aún tenga sus JSON.
  · Levantar el backend sin conexión en un desarrollo puntual, cambiando a
    mano el import de `db.py` (y sabiendo lo que se hace).

Guarda cada "tabla" como un JSON en `DATOS_DIR` (almacen_datos/).
============================================================================
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from django.conf import settings

from ..errores import ErrorAPI
from .hash import es_hash, hashear
from .hash import verificar as verificar_hash
from .permisos_defecto import MATRIZ_DEFECTO, normalizar_matriz

MODO_ALMACEN = "json"

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lock_de(nombre: str) -> threading.Lock:
    with _locks_guard:
        if nombre not in _locks:
            _locks[nombre] = threading.Lock()
        return _locks[nombre]


def _ruta(nombre: str) -> Path:
    d = Path(settings.DATOS_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{nombre}.json"


def _leer(nombre: str, por_defecto):
    try:
        return json.loads(_ruta(nombre).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return por_defecto


def _escribir(nombre: str, datos) -> None:
    with _lock_de(nombre):
        destino = _ruta(nombre)
        temporal = destino.with_suffix(destino.suffix + ".tmp")
        temporal.write_text(json.dumps(datos), encoding="utf-8")
        temporal.replace(destino)


# ---------------------------------------------------------------------------
# Escenarios
# ---------------------------------------------------------------------------
MAX_ESCENARIOS = 200


def guardar_escenario(escenario: dict) -> dict:
    todos = _leer("escenarios", [])
    sin_duplicado = [e for e in todos if e["escenario_id"] != escenario["escenario_id"]]
    sin_duplicado.insert(0, escenario)
    _escribir("escenarios", sin_duplicado[:MAX_ESCENARIOS])
    return escenario


def listar_escenarios() -> list[dict]:
    return _leer("escenarios", [])


def listar_escenarios_resumen() -> list[dict]:
    """Mismo contrato que en db_supabase: fichas ligeras para el historial."""
    prioridad = {"roja": 3, "naranja": 2, "amarilla": 1}
    salida = []
    for esc in _leer("escenarios", []):
        pasos = esc.get("iteraciones_delta") or esc.get("iteraciones") or []
        ultima = pasos[-1] if pasos else None
        niveles = [a["nivel"] for a in (esc.get("alertas") or []) if a.get("nivel")]
        salida.append({
            "escenario_id": esc["escenario_id"],
            "nombre": esc.get("nombre"),
            "creado_por": esc.get("creado_por"),
            "creado_en": esc.get("creado_en"),
            "parametros": esc.get("parametros"),
            "area_final_ha": esc.get("area_final_ha"),
            "num_iteraciones": ultima["iteracion"] if ultima else 0,
            "alerta_maxima": max(niveles, key=lambda n: prioridad.get(n, 0)) if niveles else None,
        })
    return salida


def obtener_escenario(id_: str) -> Optional[dict]:
    return next((e for e in _leer("escenarios", []) if e["escenario_id"] == id_), None)


def borrar_escenario(id_: str) -> None:
    todos = _leer("escenarios", [])
    _escribir("escenarios", [e for e in todos if e["escenario_id"] != id_])


# ---------------------------------------------------------------------------
# Alertas
# ---------------------------------------------------------------------------
def guardar_alertas(lista: list[dict]) -> None:
    if not lista:
        return
    todas = _leer("alertas", [])
    for a in lista:
        todas.insert(0, {"id": str(uuid.uuid4()), "creada_en": _ahora_iso(), **a})
    _escribir("alertas", todas[:2000])


def alertas_de_escenario(escenario_id: str) -> list[dict]:
    return [a for a in _leer("alertas", []) if a.get("escenario_id") == escenario_id]


def alertas_de_riesgo(limite: int = 50) -> list[dict]:
    return [a for a in _leer("alertas", []) if a.get("origen") == "riesgo"][:limite]


def reemplazar_alertas_riesgo(lista: list[dict]) -> list[dict]:
    todas = _leer("alertas", [])
    sin_riesgo = [a for a in todas if a.get("origen") != "riesgo"]
    nuevas = [{"id": str(uuid.uuid4()), "creada_en": _ahora_iso(), "origen": "riesgo", **a} for a in (lista or [])]
    _escribir("alertas", (nuevas + sin_riesgo)[:2000])
    return nuevas


# ---------------------------------------------------------------------------
# Calibración
# ---------------------------------------------------------------------------
def guardar_calibracion(resultado: dict) -> dict:
    _escribir("calibracion", resultado)
    historial = _leer("calibracion_historial", [])
    historial.insert(0, {
        "fecha": resultado.get("fecha"), "f1": resultado.get("f1"),
        "metodo": resultado.get("metodo"), "constantes": resultado.get("constantes"),
        "p_base": resultado.get("p_base"),
    })
    _escribir("calibracion_historial", historial[:50])
    return resultado


def leer_calibracion() -> Optional[dict]:
    return _leer("calibracion", None)


def leer_historial_calibracion() -> list[dict]:
    return _leer("calibracion_historial", [])


# ---------------------------------------------------------------------------
# Bitácora
# ---------------------------------------------------------------------------
def registrar_bitacora(entrada: dict) -> None:
    todas = _leer("bitacora", [])
    todas.insert(0, {"id": str(uuid.uuid4()), "fecha": _ahora_iso(), **entrada})
    _escribir("bitacora", todas[:500])


def listar_bitacora() -> list[dict]:
    return _leer("bitacora", [])


# ---------------------------------------------------------------------------
# Informes
# ---------------------------------------------------------------------------
def guardar_informe(informe: dict) -> dict:
    todos = _leer("informes", [])
    fila = {"id": str(uuid.uuid4()), "generado_en": _ahora_iso(), **informe}
    todos.insert(0, fila)
    _escribir("informes", todos[:200])
    return fila


def listar_informes() -> list[dict]:
    return _leer("informes", [])


def obtener_informe(id_: str) -> Optional[dict]:
    return next((r for r in _leer("informes", []) if r["id"] == id_), None)


def informe_de_escenario(escenario_id: str) -> Optional[dict]:
    return next((r for r in _leer("informes", []) if r.get("escenario_id") == escenario_id), None)


# ---------------------------------------------------------------------------
# Sesiones
# ---------------------------------------------------------------------------
def crear_sesion(sesion: dict) -> dict:
    todas = _leer("sesiones", [])
    todas.insert(0, sesion)
    _escribir("sesiones", todas[:1000])
    return sesion


def obtener_sesion(id_: str) -> Optional[dict]:
    return next((s for s in _leer("sesiones", []) if s["id"] == id_), None)


def revocar_sesion(id_: str) -> None:
    todas = _leer("sesiones", [])
    for s in todas:
        if s["id"] == id_:
            s["revocada"] = True
            _escribir("sesiones", todas)
            return


def revocar_sesiones_de_usuario(usuario_id: str) -> None:
    todas = _leer("sesiones", [])
    tocadas = 0
    for s in todas:
        if s.get("usuario_id") == usuario_id and not s.get("revocada"):
            s["revocada"] = True
            tocadas += 1
    if tocadas:
        _escribir("sesiones", todas)


def limpiar_sesiones() -> int:
    ahora = datetime.now(timezone.utc)
    todas = _leer("sesiones", [])
    vivas = []
    for s in todas:
        try:
            expira = datetime.fromisoformat(s["expira_en"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            expira = ahora
        if not s.get("revocada") and expira > ahora:
            vivas.append(s)
    if len(vivas) != len(todas):
        _escribir("sesiones", vivas)
    return len(todas) - len(vivas)


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------
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


def _usuarios_crudos() -> list[dict]:
    usuarios = _leer("usuarios", None)
    if not usuarios:
        usuarios = [dict(u) for u in _SEMILLA_USUARIOS]
        _escribir("usuarios", usuarios)
    return usuarios


def migrar_passwords() -> int:
    usuarios = _usuarios_crudos()
    cambiadas = 0
    for u in usuarios:
        if not es_hash(u["password"]):
            u["password"] = hashear(u["password"])
            cambiadas += 1
    if cambiadas:
        _escribir("usuarios", usuarios)
    return cambiadas


def _sin_password(u: dict) -> dict:
    return {k: v for k, v in u.items() if k != "password"}


def listar_usuarios(con_password: bool = False) -> list[dict]:
    usuarios = _usuarios_crudos()
    return usuarios if con_password else [_sin_password(u) for u in usuarios]


def crear_usuario(datos: dict) -> dict:
    usuarios = _usuarios_crudos()
    if any(u["email"] == datos.get("email") for u in usuarios):
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
    _escribir("usuarios", usuarios + [nuevo])
    return _sin_password(nuevo)


def actualizar_usuario(id_: str, cambios: dict) -> dict:
    usuarios = _usuarios_crudos()
    i = next((i for i, u in enumerate(usuarios) if u["id"] == id_), -1)
    if i == -1:
        raise ErrorAPI("Usuario no encontrado", estado_http=404)
    permitidos = {}
    if cambios.get("nombre") is not None:
        permitidos["nombre"] = cambios["nombre"]
    if cambios.get("rol") is not None:
        permitidos["rol"] = cambios["rol"]
    if cambios.get("activo") is not None:
        permitidos["activo"] = cambios["activo"]
    usuarios[i] = {**usuarios[i], **permitidos}
    _escribir("usuarios", usuarios)
    return _sin_password(usuarios[i])


def desactivar_usuario(id_: str) -> dict:
    return actualizar_usuario(id_, {"activo": False})


def verificar_credenciales(email: str, password: str) -> Optional[dict]:
    usuarios = _usuarios_crudos()
    u = next((x for x in usuarios if x["email"] == email), None)
    if not u or not verificar_hash(password, u["password"]):
        return None
    if u.get("activo") is False:
        raise ErrorAPI("Esta cuenta está desactivada. Contacta con el administrador.", estado_http=403)
    return {"id": u["id"], "usuario_id": u["email"], "nombre": u["nombre"], "rol": u["rol"], "email": u["email"]}


def marcar_ultimo_acceso(id_: str) -> None:
    usuarios = _usuarios_crudos()
    for u in usuarios:
        if u["id"] == id_:
            u["ultimo_acceso"] = _ahora_iso()
            _escribir("usuarios", usuarios)
            return


def restablecer_password(id_: str, nueva_password: str) -> dict:
    usuarios = _usuarios_crudos()
    i = next((i for i, u in enumerate(usuarios) if u["id"] == id_), -1)
    if i == -1:
        raise ErrorAPI("Usuario no encontrado", estado_http=404)
    usuarios[i]["password"] = hashear(nueva_password)
    _escribir("usuarios", usuarios)
    return _sin_password(usuarios[i])


# ---------------------------------------------------------------------------
# Matriz de permisos por rol
# ---------------------------------------------------------------------------
def leer_matriz_permisos() -> dict:
    guardada = _leer("permisos", None)
    return normalizar_matriz(guardada or MATRIZ_DEFECTO)


def guardar_matriz_permisos(matriz: dict) -> dict:
    normalizada = normalizar_matriz(matriz)
    _escribir("permisos", normalizada)
    return normalizada
