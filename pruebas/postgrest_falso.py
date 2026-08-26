"""
============================================================================
PostgREST DE MENTIRA — para probar el adaptador de Supabase sin Supabase
============================================================================
Levanta en memoria un servidor que habla el subconjunto de PostgREST que usa
`api/almacen/db_supabase.py`, con el MISMO esquema que tu proyecto real
(tablas, columnas, claves ajenas y CHECKs del DDL).

Sirve para dos cosas:

  · Verificar el adaptador sin credenciales ni red. Reproduce los errores
    que de verdad daría Supabase: 400 si mandas una columna que no existe,
    409 si borras un escenario que todavía tiene alertas, 400 si el `tipo`
    de la bitácora no está en el CHECK.
  · Probar el backend entero en local sin tocar la base real.

    python pruebas/postgrest_falso.py 8099      # y en .env:
    SUPABASE_URL=http://127.0.0.1:8099
    SUPABASE_SERVICE_KEY=cualquier-cosa-no-vacia

NO es un PostgreSQL: solo entiende lo que el adaptador le pide.
============================================================================
"""
from __future__ import annotations

import json
import re
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, unquote, urlparse


def _ahora():
    return datetime.now(timezone.utc).isoformat()


def _uuid():
    return str(uuid.uuid4())


# --- Esquema: columnas, clave primaria, defaults, CHECKs y claves ajenas ----
ESQUEMA = {
    "usuarios": {
        "pk": "id",
        "columnas": ["id", "email", "password", "nombre", "rol", "activo",
                     "ultimo_acceso", "creado_en", "orden"],
        "defaults": {"rol": "brigada", "activo": True, "creado_en": _ahora},
        "checks": {"rol": {"administrador", "analista", "ugr", "brigada"}},
        "unicos": ["email"],
    },
    "sesiones": {
        "pk": "id",
        "columnas": ["id", "usuario_id", "emitida_en", "expira_en", "revocada",
                     "user_agent", "ip"],
        "defaults": {"emitida_en": _ahora, "revocada": False},
        "fk": {"usuario_id": ("usuarios", "id")},
    },
    "escenarios": {
        "pk": "escenario_id",
        "columnas": ["escenario_id", "nombre", "creado_por", "creado_en", "parametros",
                     "foco_coordenadas", "variables_promedio", "metadatos_motor",
                     "area_final_ha", "num_iteraciones", "iteraciones", "datos"],
        "defaults": {"creado_en": _ahora},
    },
    "alertas": {
        "pk": "id",
        "columnas": ["id", "escenario_id", "origen", "nivel", "mensaje", "iteracion",
                     "lat", "lon", "creada_en"],
        "defaults": {"id": _uuid, "origen": "simulacion", "creada_en": _ahora},
        "checks": {"origen": {"simulacion", "riesgo"}},
        "fk": {"escenario_id": ("escenarios", "escenario_id")},
    },
    "calibraciones": {
        "pk": "id",
        "columnas": ["id", "fecha", "vigente", "f1", "metodo", "p_base", "constantes",
                     "detalle", "resultado"],
        "defaults": {"id": _uuid, "fecha": _ahora, "vigente": False},
    },
    "bitacora": {
        "pk": "id",
        "columnas": ["id", "fecha", "usuario", "accion", "detalle", "tipo"],
        "defaults": {"id": _uuid, "fecha": _ahora, "tipo": "data"},
        "checks": {"tipo": {"auth", "sim", "user", "data", "alert", "report", "sys"}},
        "no_nulos": ["accion"],
    },
    "informes": {
        "pk": "id",
        "columnas": ["id", "escenario_id", "nombre", "html", "generado_por",
                     "generado_en", "resumen"],
        "defaults": {"id": _uuid, "generado_en": _ahora},
        "no_nulos": ["nombre", "html"],
        "fk": {"escenario_id": ("escenarios", "escenario_id")},
    },
    "permisos": {
        "pk": "id",
        "columnas": ["id", "matriz", "actualizado_en"],
        "defaults": {"id": "actual", "actualizado_en": _ahora},
        "no_nulos": ["matriz"],
    },
}

DATOS: dict[str, list[dict]] = {t: [] for t in ESQUEMA}
_CANDADO = threading.Lock()


class ErrorPostgrest(Exception):
    def __init__(self, estado: int, mensaje: str, codigo: str = ""):
        super().__init__(mensaje)
        self.estado = estado
        self.mensaje = mensaje
        # Código propio de PostgREST (PGRST102, PGRST125, PGRST205…). Es lo
        # que el cliente mira para traducir el fallo, así que el doble tiene
        # que devolverlo igual que el de verdad.
        self.codigo = codigo or str(estado)


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------
def _convertir(texto: str):
    if texto in ("true", "false"):
        return texto == "true"
    if texto == "null":
        return None
    return texto


def _compara(valor, operador: str, esperado: str) -> bool:
    esperado_conv = _convertir(esperado)
    if operador == "eq":
        if isinstance(valor, bool) or isinstance(esperado_conv, bool):
            return bool(valor) == bool(esperado_conv)
        return str(valor) == str(esperado_conv)
    if operador == "neq":
        return str(valor) != str(esperado_conv)
    if operador == "lt":
        return valor is not None and str(valor) < str(esperado_conv)
    if operador == "gt":
        return valor is not None and str(valor) > str(esperado_conv)
    if operador == "is":
        return valor is esperado_conv
    raise ErrorPostgrest(400, f"operador no soportado por el doble de pruebas: {operador}")


def _filtro_or(fila: dict, expresion: str) -> bool:
    # or=(revocada.eq.true,expira_en.lt.2026-01-01)
    interior = expresion.strip()
    if interior.startswith("(") and interior.endswith(")"):
        interior = interior[1:-1]
    for parte in interior.split(","):
        col, _, resto = parte.partition(".")
        operador, _, valor = resto.partition(".")
        if _compara(fila.get(col), operador, valor):
            return True
    return False


def _pasa_filtros(fila: dict, filtros: list[tuple[str, str]], tabla: str) -> bool:
    for clave, valor in filtros:
        if clave == "or":
            if not _filtro_or(fila, valor):
                return False
            continue
        if clave not in ESQUEMA[tabla]["columnas"]:
            raise ErrorPostgrest(400, f'column "{clave}" does not exist')
        operador, _, esperado = valor.partition(".")
        if not _compara(fila.get(clave), operador, esperado):
            return False
    return True


def _ordenar(filas: list[dict], ordenes: list[str]) -> list[dict]:
    # Se aplican en orden inverso para que el primer `order=` mande.
    for orden in reversed(ordenes):
        partes = orden.split(".")
        col = partes[0]
        desc = "desc" in partes
        nulos_al_final = "nullslast" in partes

        def clave(f, _c=col, _n=nulos_al_final):
            v = f.get(_c)
            if v is None:
                return (1 if _n else 0, "")
            return (0 if _n else 1, str(v))

        filas = sorted(filas, key=clave, reverse=desc)
    return filas


def _proyectar(fila: dict, select: str) -> dict:
    if not select or select == "*":
        return dict(fila)
    columnas = [c.strip() for c in select.split(",") if c.strip()]
    return {c: fila.get(c) for c in columnas}


# ---------------------------------------------------------------------------
# Validación de escritura (lo que de verdad protege el adaptador)
# ---------------------------------------------------------------------------
def _validar_fila(tabla: str, fila: dict) -> dict:
    esquema = ESQUEMA[tabla]

    for clave in fila:
        if clave not in esquema["columnas"]:
            # Es EXACTAMENTE lo que responde PostgREST ante una columna que no
            # existe, y la razón de que el adaptador filtre con `solo_columnas`.
            raise ErrorPostgrest(
                400, f"Could not find the '{clave}' column of '{tabla}' in the schema cache")

    completa = dict(fila)
    for col, valor in esquema.get("defaults", {}).items():
        if completa.get(col) is None:
            completa[col] = valor() if callable(valor) else valor

    for col, permitidos in esquema.get("checks", {}).items():
        if completa.get(col) is not None and completa[col] not in permitidos:
            raise ErrorPostgrest(
                400, f'new row for relation "{tabla}" violates check constraint "{tabla}_{col}_check"')

    for col in esquema.get("no_nulos", []):
        if completa.get(col) is None:
            raise ErrorPostgrest(400, f'null value in column "{col}" violates not-null constraint')

    for col, (tabla_ref, col_ref) in esquema.get("fk", {}).items():
        valor = completa.get(col)
        if valor is None:
            continue
        if not any(str(f.get(col_ref)) == str(valor) for f in DATOS[tabla_ref]):
            raise ErrorPostgrest(
                409, f'insert or update on table "{tabla}" violates foreign key constraint '
                     f'"{tabla}_{col}_fkey"')

    # El cuerpo tiene que ser JSON serializable de verdad (NaN/Infinity no lo son)
    try:
        json.dumps(completa, allow_nan=False)
    except ValueError as e:
        raise ErrorPostgrest(400, f"invalid input syntax for type json: {e}") from e

    return completa


def _comprobar_hijos(tabla: str, fila: dict) -> None:
    """Impide borrar un padre que aún tiene hijos (no hay ON DELETE CASCADE)."""
    esquema = ESQUEMA[tabla]
    pk = esquema["pk"]
    for tabla_hija, esquema_hijo in ESQUEMA.items():
        for col, (tabla_ref, col_ref) in esquema_hijo.get("fk", {}).items():
            if tabla_ref != tabla or col_ref != pk:
                continue
            if any(str(h.get(col)) == str(fila.get(pk)) for h in DATOS[tabla_hija]):
                raise ErrorPostgrest(
                    409, f'update or delete on table "{tabla}" violates foreign key constraint '
                         f'"{tabla_hija}_{col}_fkey" on table "{tabla_hija}"')


# ---------------------------------------------------------------------------
# Servidor
# ---------------------------------------------------------------------------
class Manejador(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):  # silencio
        pass

    # -- utilidades --
    def _responder(self, estado: int, cuerpo=None):
        datos = b"" if cuerpo is None else json.dumps(cuerpo, default=str).encode("utf-8")
        self.send_response(estado)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        if datos:
            self.wfile.write(datos)

    def _error(self, e: ErrorPostgrest):
        self._responder(e.estado, {"message": e.mensaje, "code": e.codigo,
                                   "details": None, "hint": None})

    def _contexto(self):
        partes = urlparse(self.path)
        m = re.match(r"^/rest/v1/([A-Za-z_][A-Za-z0-9_]*)$", partes.path)
        if not m:
            # Lo que contesta PostgREST cuando la ruta tiene segmentos de más
            # —el caso de una SUPABASE_URL que ya incluía /rest/v1, con lo que
            # la petición sale a /rest/v1/rest/v1/<tabla>—. No dice "no existe
            # la tabla": dice que la RUTA no vale, y por eso despista tanto.
            raise ErrorPostgrest(404, "Invalid path specified in request URL",
                                 "PGRST125")
        tabla = m.group(1)
        if tabla not in ESQUEMA:
            raise ErrorPostgrest(
                404, f"Could not find the table 'public.{tabla}' in the schema cache",
                "PGRST205")
        consulta = [(k, unquote(v)) for k, v in parse_qsl(partes.query, keep_blank_values=True)]
        return tabla, consulta

    def _cuerpo(self):
        largo = int(self.headers.get("Content-Length") or 0)
        if not largo:
            return None
        crudo = self.rfile.read(largo).decode("utf-8")
        try:
            return json.loads(crudo)
        except ValueError as e:
            raise ErrorPostgrest(400, f"cuerpo JSON inválido: {e}") from e

    def _autenticado(self) -> bool:
        return bool(self.headers.get("apikey") or self.headers.get("Authorization"))

    @staticmethod
    def _separar(consulta):
        select, ordenes, limite, on_conflict, filtros = "*", [], None, None, []
        for clave, valor in consulta:
            if clave == "select":
                select = valor
            elif clave == "order":
                ordenes.append(valor)
            elif clave == "limit":
                limite = int(valor)
            elif clave == "on_conflict":
                on_conflict = valor
            else:
                filtros.append((clave, valor))
        return select, ordenes, limite, on_conflict, filtros

    # -- verbos --
    def do_GET(self):
        try:
            if not self._autenticado():
                raise ErrorPostgrest(401, "No API key found in request")
            tabla, consulta = self._contexto()
            select, ordenes, limite, _, filtros = self._separar(consulta)
            with _CANDADO:
                filas = [f for f in DATOS[tabla] if _pasa_filtros(f, filtros, tabla)]
                filas = _ordenar(filas, ordenes)
                if limite is not None:
                    filas = filas[:limite]
                self._responder(200, [_proyectar(f, select) for f in filas])
        except ErrorPostgrest as e:
            self._error(e)

    def do_POST(self):
        try:
            if not self._autenticado():
                raise ErrorPostgrest(401, "No API key found in request")
            tabla, consulta = self._contexto()
            _, _, _, on_conflict, _ = self._separar(consulta)
            cuerpo = self._cuerpo()
            filas_entrada = cuerpo if isinstance(cuerpo, list) else [cuerpo]
            prefer = self.headers.get("Prefer") or ""
            fusionar = "merge-duplicates" in prefer

            # PGRST102 — PostgREST arma UN solo INSERT con UNA sola lista de
            # columnas, así que exige que todos los objetos del lote tengan
            # exactamente las mismas claves. Sin esta comprobación el doble
            # aceptaba lotes heterogéneos que Supabase rechaza, y por eso dejó
            # pasar el fallo de las alertas (rojas con lat/lon, el resto sin).
            if len(filas_entrada) > 1:
                claves = {frozenset(f.keys()) for f in filas_entrada}
                if len(claves) > 1:
                    raise ErrorPostgrest(400, "All object keys must match")

            with _CANDADO:
                salida = []
                for entrada in filas_entrada:
                    fila = _validar_fila(tabla, entrada)
                    clave = on_conflict or ESQUEMA[tabla]["pk"]
                    existente = next(
                        (f for f in DATOS[tabla] if f.get(clave) is not None
                         and str(f.get(clave)) == str(fila.get(clave))), None)
                    if existente is not None:
                        if not fusionar:
                            raise ErrorPostgrest(
                                409, f'duplicate key value violates unique constraint "{tabla}_pkey"')
                        existente.update(fila)
                        salida.append(existente)
                        continue
                    for col_unica in ESQUEMA[tabla].get("unicos", []):
                        if fila.get(col_unica) is not None and any(
                                str(f.get(col_unica)) == str(fila[col_unica]) for f in DATOS[tabla]):
                            raise ErrorPostgrest(
                                409, f'duplicate key value violates unique constraint '
                                     f'"{tabla}_{col_unica}_key"')
                    DATOS[tabla].append(fila)
                    salida.append(fila)

            if "return=representation" in prefer:
                self._responder(201, salida)
            else:
                self._responder(204)
        except ErrorPostgrest as e:
            self._error(e)

    def do_PATCH(self):
        try:
            if not self._autenticado():
                raise ErrorPostgrest(401, "No API key found in request")
            tabla, consulta = self._contexto()
            _, _, _, _, filtros = self._separar(consulta)
            cambios = self._cuerpo() or {}
            with _CANDADO:
                for clave in cambios:
                    if clave not in ESQUEMA[tabla]["columnas"]:
                        raise ErrorPostgrest(
                            400, f"Could not find the '{clave}' column of '{tabla}' in the schema cache")
                for col, permitidos in ESQUEMA[tabla].get("checks", {}).items():
                    if col in cambios and cambios[col] not in permitidos:
                        raise ErrorPostgrest(
                            400, f'new row for relation "{tabla}" violates check constraint '
                                 f'"{tabla}_{col}_check"')
                afectadas = [f for f in DATOS[tabla] if _pasa_filtros(f, filtros, tabla)]
                for fila in afectadas:
                    fila.update(cambios)
            if "return=representation" in (self.headers.get("Prefer") or ""):
                self._responder(200, afectadas)
            else:
                self._responder(204)
        except ErrorPostgrest as e:
            self._error(e)

    def do_DELETE(self):
        try:
            if not self._autenticado():
                raise ErrorPostgrest(401, "No API key found in request")
            tabla, consulta = self._contexto()
            _, _, _, _, filtros = self._separar(consulta)
            with _CANDADO:
                borradas = [f for f in DATOS[tabla] if _pasa_filtros(f, filtros, tabla)]
                for fila in borradas:
                    _comprobar_hijos(tabla, fila)
                DATOS[tabla] = [f for f in DATOS[tabla] if f not in borradas]
            if "return=representation" in (self.headers.get("Prefer") or ""):
                self._responder(200, borradas)
            else:
                self._responder(204)
        except ErrorPostgrest as e:
            self._error(e)


def arrancar(puerto: int = 8099) -> ThreadingHTTPServer:
    servidor = ThreadingHTTPServer(("127.0.0.1", puerto), Manejador)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    return servidor


def vaciar():
    with _CANDADO:
        for tabla in DATOS:
            DATOS[tabla] = []


if __name__ == "__main__":
    puerto = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    servidor = arrancar(puerto)
    print(f"PostgREST de mentira escuchando en http://127.0.0.1:{puerto}")
    print("En backend_django/.env:")
    print(f"  SUPABASE_URL=http://127.0.0.1:{puerto}")
    print("  SUPABASE_SERVICE_KEY=clave-de-mentira")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        servidor.shutdown()
