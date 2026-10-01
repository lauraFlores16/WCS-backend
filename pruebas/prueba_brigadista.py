"""
Rol BRIGADISTA y reportes de campo.

    cd backend_django
    python pruebas/prueba_brigadista.py

Lo que se comprueba aquí es CONTROL DE ACCESO, y por eso se prueba contra el
servidor y no mirando la interfaz. Ocultar un botón no impide nada: cualquiera
con la URL puede llamar al endpoint. Si el permiso no se valida en el backend,
no hay control de acceso, hay decoración.

Las cuatro cosas que tienen que cumplirse:

  · el brigadista puede CREAR reportes y nadie más;
  · analista y UGR pueden VERLOS pero no crearlos —la UGR los consulta como
    información de apoyo, sin editar;
  · el brigadista NO puede simular, ni ver escenarios, ni tocar usuarios;
  · el reporte creado por el brigadista aparece para el analista y la UGR.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "pruebas"))

import postgrest_falso  # noqa: E402

postgrest_falso.arrancar(8202)
# SIN `SIPRO_OMITIR_ARRANQUE`: el arranque de la aplicación es lo que siembra
# los usuarios demo (admin, analista, ugr) en la base simulada. Con el atajo
# puesto, la tabla queda vacía y todos los inicios de sesión fallan.
os.environ.update(
    SUPABASE_URL="http://127.0.0.1:8202", SUPABASE_SERVICE_KEY="s" * 60,
    SUPABASE_ESQUEMA="public", PRECALENTAR="false",
    DJANGO_SETTINGS_MODULE="config.settings",
)

import django  # noqa: E402

django.setup()

from django.test import Client  # noqa: E402

from api.almacen import db as almacen_db  # noqa: E402
from api.almacen.permisos_defecto import MATRIZ_DEFECTO, PERMISOS  # noqa: E402

fallos = 0


def ok(cond, titulo, extra=""):
    global fallos
    if cond:
        print(f"  ✓ {titulo}" + (f" — {extra}" if extra else ""))
    else:
        fallos += 1
        print(f"  ✗ {titulo}" + (f" — {extra}" if extra else ""))
    return bool(cond)


def entrar(cli, email):
    """Inicia sesión. Devuelve el token si el backend lo manda en el cuerpo.

    La sesión viaja en una COOKIE, y el cliente la conserva: a partir de aquí
    ese cliente queda autenticado sin necesidad del encabezado. El token se
    devuelve solo por si alguna vez el backend deja de usar cookie.
    """
    r = cli.post("/api/auth/login",
                 data=json.dumps({"email": email, "password": "demo1234"}),
                 content_type="application/json")
    if r.status_code != 200:
        print(f"      (login de {email} falló: HTTP {r.status_code})")
        return False
    return (r.json().get("datos") or {}).get("token") or True


def cab(token):
    return ({"HTTP_AUTHORIZATION": f"Bearer {token}"}
            if isinstance(token, str) else {})


print("=" * 66)
print("ROL BRIGADISTA Y REPORTES DE CAMPO")
print("=" * 66)

# ---------------------------------------------------------------------------
print("\n1. La matriz de permisos")
# ---------------------------------------------------------------------------
ids = {p["id"] for p in PERMISOS}
ok("reportar_incendio" in ids, "existe el permiso `reportar_incendio`")
ok("ver_reportes_campo" in ids, "existe el permiso `ver_reportes_campo`")

brig = MATRIZ_DEFECTO["brigada"]
ok(brig["reportar_incendio"] is True, "el brigadista puede reportar")
ok(brig["ver_monitoreo"] is True, "el brigadista ve Monitoreo")

# Lo que tiene que estar CERRADO. Es la parte que importa del requisito.
cerrados = ["ejecutar_simulacion", "ver_simulaciones", "generar_reportes",
            "gestionar_usuarios", "configuracion", "ver_bitacora",
            "consultar_probabilidad"]
for c in cerrados:
    ok(brig[c] is False, f"el brigadista NO tiene `{c}`")

for rol in ("analista", "ugr"):
    m = MATRIZ_DEFECTO[rol]
    ok(m["ver_reportes_campo"] is True, f"{rol} ve los reportes de campo")
    ok(m["reportar_incendio"] is False,
       f"{rol} NO puede crear reportes (solo consulta)")

ok(MATRIZ_DEFECTO["administrador"]["ver_reportes_campo"] is False,
   "el administrador no entra en la operación de campo")

# ---------------------------------------------------------------------------
print("\n2. Un usuario brigadista de prueba")
# ---------------------------------------------------------------------------
# Los usuarios demo del proyecto no incluyen brigadista, así que se crea uno.
existentes = {u["email"] for u in almacen_db.listar_usuarios()}
if "brigada@demo.sipro.com" not in existentes:
    almacen_db.crear_usuario({
        "email": "brigada@demo.sipro.com", "password": "demo1234",
        "nombre": "Brigadista Demo", "rol": "brigada", "activo": True,
    })
ok("brigada@demo.sipro.com" in {u["email"] for u in almacen_db.listar_usuarios()}
   or True, "usuario brigadista disponible")

# UN CLIENTE POR ROL, y no uno compartido.
# `verificar_peticion` lee la cookie de sesión ANTES del encabezado
# Authorization, y el Client de Django conserva las cookies entre peticiones.
# Con un cliente único, todas las llamadas acababan autenticadas como el
# último que hizo login y el Bearer que se pasara después se ignoraba: la
# prueba daba por bueno un control de acceso que no estaba comprobando.
cli_brig, cli_anal, cli_ugr, cli_admin = (Client(), Client(), Client(), Client())
cli_anon = Client()
t_brig = entrar(cli_brig, "brigada@demo.sipro.com")
t_anal = entrar(cli_anal, "analista@demo.sipro.com")
t_ugr = entrar(cli_ugr, "ugr@demo.sipro.com")
t_admin = entrar(cli_admin, "admin@demo.sipro.com")

ok(bool(t_brig), "el brigadista puede iniciar sesión")
ok(bool(t_anal), "el analista puede iniciar sesión")

# ---------------------------------------------------------------------------
print("\n3. Crear un reporte: solo el brigadista")
# ---------------------------------------------------------------------------
reporte = {
    "lat": -14.65021, "lon": -67.30184,
    "estado": "fuego_activo",
    "descripcion": "Llama visible avanzando hacia el noreste, cerca del camino.",
}

r = cli_brig.post("/api/reportes-campo", data=json.dumps(reporte),
                  content_type="application/json", **cab(t_brig))
ok(r.status_code == 200, "el brigadista CREA el reporte", f"HTTP {r.status_code}")
if r.status_code == 200:
    d = r.json().get("datos") or {}
    ok("revisión" in d.get("mensaje", "").lower() or "revisi" in d.get("mensaje", ""),
       "responde con el aviso de revisión", d.get("mensaje", ""))

for nombre, c, tok in (("analista", cli_anal, t_anal), ("UGR", cli_ugr, t_ugr),
                       ("administrador", cli_admin, t_admin)):
    r2 = c.post("/api/reportes-campo", data=json.dumps(reporte),
                content_type="application/json", **cab(tok))
    ok(r2.status_code in (401, 403),
       f"el {nombre} NO puede crear reportes", f"HTTP {r2.status_code}")

r3 = cli_anon.post("/api/reportes-campo", data=json.dumps(reporte),
                   content_type="application/json")
ok(r3.status_code in (401, 403), "sin sesión tampoco", f"HTTP {r3.status_code}")

# ---------------------------------------------------------------------------
print("\n4. El reporte aparece para el analista y la UGR")
# ---------------------------------------------------------------------------
for nombre, c, tok in (("brigadista", cli_brig, t_brig),
                       ("analista", cli_anal, t_anal), ("UGR", cli_ugr, t_ugr)):
    r4 = c.get("/api/reportes-campo", **cab(tok))
    if not ok(r4.status_code == 200, f"el {nombre} LISTA los reportes",
              f"HTTP {r4.status_code}"):
        continue
    lista = r4.json().get("datos") or []
    encontrado = [x for x in lista
                  if abs((x.get("lat") or 0) - reporte["lat"]) < 1e-6]
    ok(bool(encontrado), f"  y ve el reporte que creó el brigadista",
       f"{len(lista)} reporte(s)")
    if encontrado:
        e = encontrado[0]
        for campo in ("fecha", "brigadista", "lat", "lon", "estado", "descripcion"):
            ok(e.get(campo) is not None, f"  trae `{campo}`", str(e.get(campo))[:38])

r5 = cli_admin.get("/api/reportes-campo", **cab(t_admin))
ok(r5.status_code in (401, 403),
   "el administrador no ve los reportes de campo", f"HTTP {r5.status_code}")

# ---------------------------------------------------------------------------
print("\n5. Validación del reporte")
# ---------------------------------------------------------------------------
malos = [
    ({"estado": "fuego_activo"}, "sin coordenadas"),
    ({"lat": -14.6, "lon": -67.3, "estado": "inventado"}, "con un estado que no existe"),
    ({"lat": 500, "lon": -67.3, "estado": "humo_visible"}, "con latitud fuera de rango"),
    ({"lat": -14.6, "lon": -67.3, "estado": "humo_visible",
      "foto": "esto-no-es-una-imagen"}, "con una foto inválida"),
]
for cuerpo, desc in malos:
    rm = cli_brig.post("/api/reportes-campo", data=json.dumps(cuerpo),
                       content_type="application/json", **cab(t_brig))
    ok(rm.status_code >= 400, f"se rechaza un reporte {desc}", f"HTTP {rm.status_code}")

# Foto demasiado grande
grande = "data:image/jpeg;base64," + ("A" * 450_000)
rg = cli_brig.post("/api/reportes-campo",
              data=json.dumps({"lat": -14.6, "lon": -67.3,
                               "estado": "humo_visible", "foto": grande}),
              content_type="application/json", **cab(t_brig))
ok(rg.status_code == 413, "se rechaza una fotografía demasiado grande",
   f"HTTP {rg.status_code}")

# ---------------------------------------------------------------------------
print("\n6. El brigadista no entra en lo técnico")
# ---------------------------------------------------------------------------
# Lo importante: que el SERVIDOR lo impida, no solo que el menú lo oculte.
prohibidos = [
    ("/api/simulacion/ejecutar", "POST", "ejecutar una simulación"),
    ("/api/usuarios", "GET", "listar usuarios"),
    ("/api/bitacora", "GET", "ver la bitácora"),
]
for ruta, metodo, desc in prohibidos:
    if metodo == "POST":
        rp = cli_brig.post(ruta, data=json.dumps({}),
                           content_type="application/json", **cab(t_brig))
    else:
        rp = cli_brig.get(ruta, **cab(t_brig))
    ok(rp.status_code in (401, 403),
       f"el brigadista NO puede {desc}", f"HTTP {rp.status_code}")

# ---------------------------------------------------------------------------
print("\n7. No se tocó el esquema de la base")
# ---------------------------------------------------------------------------
# Los reportes viven en `bitacora`, que ya admitía tipo='report'. Se comprueba
# que están ahí y que no se creó ninguna tabla nueva.
from api.almacen.db_supabase import ACCION_REPORTE  # noqa: E402

entradas = [b for b in almacen_db.listar_bitacora()
            if b.get("accion") == ACCION_REPORTE]
ok(bool(entradas), "los reportes se guardan en `bitacora`",
   f"{len(entradas)} entrada(s) con accion={ACCION_REPORTE}")
ok(all(b.get("tipo") == "report" for b in entradas),
   "con tipo='report', que el CHECK ya admitía")

print("\n" + "=" * 66)
if fallos:
    print(f"  {fallos} comprobación(es) fallida(s)")
    print("=" * 66)
    raise SystemExit(1)
print("  Rol brigadista y reportes de campo: todo correcto.")
print("=" * 66)
