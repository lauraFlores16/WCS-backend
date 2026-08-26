"""
============================================================================
TOKENS DE SESIÓN — puerto de la sección de auth de backend/rutas/index.js
============================================================================
Token JWT-lite firmado con HMAC (sin dependencias externas, igual que en
Node), con un identificador de sesión (jti) que además existe como fila en
`almacen_datos/sesiones.json`. Eso permite revocar una sesión (logout,
desactivar cuenta) sin esperar a que el JWT caduque solo.

La cookie es httpOnly: el JavaScript del navegador no la lee (anti-XSS). Por
eso el frontend no guarda nada de la sesión en localStorage — ver
frontend/src/local/cliente.js.
============================================================================
"""
from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import json
import time
import uuid
from typing import Callable, Optional

from django.conf import settings
from django.http import HttpRequest, JsonResponse

from .almacen import db as almacen_db

COOKIE_SESION = "sipro_sesion"
DURACION_SESION_S = 12 * 60 * 60  # 12 h


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    relleno = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + relleno)


def firmar(carga: dict) -> str:
    cuerpo = _b64url_encode(json.dumps(carga).encode("utf-8"))
    firma = _b64url_encode(
        hmac.new(settings.JWT_SECRETO.encode("utf-8"), cuerpo.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{cuerpo}.{firma}"


def decodificar_firmado(token: Optional[str]) -> Optional[dict]:
    if not token or "." not in token:
        return None
    cuerpo, _, firma = token.partition(".")
    if not cuerpo or not firma:
        return None
    esperada = _b64url_encode(
        hmac.new(settings.JWT_SECRETO.encode("utf-8"), cuerpo.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(firma, esperada):
        return None
    try:
        carga = json.loads(_b64url_decode(cuerpo))
    except (ValueError, UnicodeDecodeError):
        return None
    if carga.get("expira") and time.time() * 1000 > carga["expira"]:
        return None
    return carga


def opciones_cookie() -> dict:
    en_produccion = not settings.DEBUG
    return {
        "httponly": True,
        "samesite": "None" if en_produccion else "Lax",
        "secure": en_produccion,
        "max_age": DURACION_SESION_S,
        "path": "/",
    }


def emitir_token(usuario: dict, request: HttpRequest) -> str:
    jti = str(uuid.uuid4())
    expira = time.time() * 1000 + DURACION_SESION_S * 1000
    almacen_db.crear_sesion({
        "id": jti,
        "usuario_id": usuario.get("id") or usuario.get("usuario_id"),
        "emitida_en": _iso_de_ms(time.time() * 1000),
        "expira_en": _iso_de_ms(expira),
        "user_agent": (request.headers.get("user-agent") or "")[:300],
        "ip": (request.headers.get("x-forwarded-for") or request.META.get("REMOTE_ADDR") or "")[:60],
        "revocada": False,
    })
    return firmar({**usuario, "jti": jti, "expira": expira})


def _iso_de_ms(ms: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def verificar_peticion(request: HttpRequest) -> Optional[dict]:
    token = request.COOKIES.get(COOKIE_SESION) or (request.headers.get("Authorization") or "").replace("Bearer ", "")
    carga = decodificar_firmado(token)
    if not carga or not carga.get("jti"):
        return None
    sesion = almacen_db.obtener_sesion(carga["jti"])
    if not sesion or sesion.get("revocada"):
        return None
    from datetime import datetime
    try:
        expira_en = datetime.fromisoformat(sesion["expira_en"].replace("Z", "+00:00"))
        if expira_en.timestamp() * 1000 < time.time() * 1000:
            return None
    except (KeyError, ValueError):
        return None
    return carga


def requiere_sesion(vista: Callable) -> Callable:
    @functools.wraps(vista)
    def envoltura(request: HttpRequest, *args, **kwargs):
        try:
            usuario = verificar_peticion(request)
        except Exception as e:  # noqa: BLE001
            print(f"[auth] {e}")
            return JsonResponse({"ok": False, "error": "Sesión no válida o expirada"}, status=401)
        if not usuario:
            return JsonResponse({"ok": False, "error": "Sesión no válida o expirada"}, status=401)
        request.usuario = usuario
        return vista(request, *args, **kwargs)
    return envoltura


def requiere_admin(vista: Callable) -> Callable:
    @functools.wraps(vista)
    def envoltura(request: HttpRequest, *args, **kwargs):
        if getattr(request, "usuario", {}).get("rol") != "administrador":
            return JsonResponse({"ok": False, "error": "Solo un administrador puede gestionar usuarios"}, status=403)
        return vista(request, *args, **kwargs)
    return envoltura


def tiene_permiso(usuario: Optional[dict], permiso: str) -> bool:
    """¿El rol de este usuario tiene el permiso, según la matriz guardada?

    Sin excepciones por rol: el administrador pasa por la matriz igual que
    todos. Es lo que impide que entre a las rutas operativas escribiendo la
    URL a mano, y lo que convierte la matriz en control de acceso de verdad en
    vez de en una preferencia de la interfaz.
    """
    if not usuario:
        return False
    try:
        matriz = almacen_db.leer_matriz_permisos()
    except Exception as e:  # noqa: BLE001
        print(f"[permisos] no se pudo leer la matriz: {e}")
        return False
    return bool((matriz.get(usuario.get("rol")) or {}).get(permiso))


def exigir_permiso(request: HttpRequest, permiso: str) -> Optional[JsonResponse]:
    """Comprueba sesión + permiso. Devuelve la respuesta de error, o None si pasa.

    Se usa al principio de las vistas operativas:

        error = exigir_permiso(request, "ejecutar_simulacion")
        if error: return error
    """
    usuario = verificar_peticion(request)
    if not usuario:
        return JsonResponse({"ok": False, "error": "Sesión no válida o expirada"}, status=401)
    if not tiene_permiso(usuario, permiso):
        return JsonResponse({
            "ok": False,
            "error": f"Tu perfil ({usuario.get('rol')}) no tiene permiso para esta acción.",
            "permiso_requerido": permiso,
        }, status=403)
    request.usuario = usuario
    return None
