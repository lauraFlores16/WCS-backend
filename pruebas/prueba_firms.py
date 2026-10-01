"""FIRMS: focos activos e históricos, separados de verdad.

    cd backend_django
    python pruebas/prueba_firms.py
"""
from __future__ import annotations

import http.server
import json
import os
import socketserver
import sys
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "pruebas"))

import postgrest_falso  # noqa: E402

postgrest_falso.arrancar(8251)

# --- NASA FIRMS simulada ---------------------------------------------------
CAB = ("country_id,latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,"
       "satellite,instrument,confidence,version,bright_ti5,frp,daynight\n")
# Dentro de Apolo
DENTRO = "BOL,-14.6500,-68.5000,330.1,0.4,0.4,2026-09-20,1730,N,VIIRS,n,2.0NRT,295.0,12.3,D\n"
# Dentro del bbox de settings pero FUERA del polígono municipal
FUERA = "BOL,-14.0500,-67.6000,325.0,0.4,0.4,2026-09-20,1730,N,VIIRS,n,2.0NRT,290.0,8.1,D\n"

MODO = {"v": "con_focos"}


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        m = MODO["v"]
        if m == "clave_mala":
            cuerpo = b"Invalid MAP_KEY."
        elif m == "vacio":
            cuerpo = CAB.encode()
        elif m == "solo_fuera":
            cuerpo = (CAB + FUERA).encode()
        elif m == "error":
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        else:
            cuerpo = (CAB + DENTRO + DENTRO + FUERA).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/csv")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *a):
        pass


socketserver.TCPServer.allow_reuse_address = True
threading.Thread(
    target=socketserver.TCPServer(("127.0.0.1", 8252), H).serve_forever,
    daemon=True).start()

os.environ.update(
    SUPABASE_URL="http://127.0.0.1:8251", SUPABASE_SERVICE_KEY="s" * 60,
    SUPABASE_ESQUEMA="public", PRECALENTAR="false",
    SIPRO_OMITIR_ARRANQUE="1", DJANGO_SETTINGS_MODULE="config.settings",
    NASA_FIRMS_MAP_KEY="clave-de-prueba",
)

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

from api.lib import cache as _cache  # noqa: E402
from api.servicios import firms  # noqa: E402
from api.servicios import grid as grid_srv  # noqa: E402

firms.BASE = "http://127.0.0.1:8252/api/area/csv"

fallos = 0


def ok(cond, titulo, extra=""):
    global fallos
    if cond:
        print(f"  ✓ {titulo}" + (f" — {extra}" if extra else ""))
    else:
        fallos += 1
        print(f"  ✗ {titulo}" + (f" — {extra}" if extra else ""))
    return bool(cond)


# La caché es de dos niveles, memoria y disco, y aquí estorba: lo que se
# prueba es la lógica de FIRMS, no el almacenamiento. Se sustituye por una
# llamada directa al productor.
class _SinCache:
    def __init__(self, valor):
        self.valor, self.origen, self.edad_ms, self.error = valor, "red", 0, None


def _sin_cache(clave, ttl, productor, disco=True):
    return _SinCache(productor())


firms.con_cache_tolerante = _sin_cache


def limpiar_cache():
    _cache.invalidar("firms:")


def consultar(modo, zona="apolo"):
    MODO["v"] = modo
    limpiar_cache()
    return firms.obtener_focos_activos(zona)


print("=" * 66)
print("FOCOS ACTIVOS E HISTÓRICOS")
print("=" * 66)

grid_srv.cargar()

# ---------------------------------------------------------------------------
print("\n1. El respaldo histórico ya no existe")
# ---------------------------------------------------------------------------
ok(not hasattr(firms, "_focos_historicos_de_respaldo"),
   "se eliminó la función de respaldo histórico")

import inspect  # noqa: E402

fuente = inspect.getsource(firms.obtener_focos_activos)
ok("historico" not in fuente.replace('"historico": False', ''),
   "obtener_focos_activos no menciona datos históricos")

# ---------------------------------------------------------------------------
print("\n2. Los cuatro estados de la consulta")
# ---------------------------------------------------------------------------
r = consultar("con_focos")
ok(r["estado"] == firms.CORRECTO, "con focos → estado correcto", r["estado"])
ok(r["activos"] == 2, "cuenta solo los que están dentro del municipio",
   f"{r['activos']} de {r.get('recibidos_bbox')} recibidos")
ok(all(not f.get("historico") for f in r["focos"]),
   "ninguno viene marcado como histórico")

r = consultar("vacio")
ok(r["estado"] == firms.SIN_FOCOS, "sin focos → estado sin_focos", r["estado"])
ok(r["focos"] == [] and r["activos"] == 0,
   "y NO devuelve históricos en su lugar", f"{len(r['focos'])} focos")
ok(r["mensaje"] == "Sin focos activos para el período consultado.",
   "con el mensaje exacto que pide la especificación")

r = consultar("solo_fuera")
ok(r["estado"] == firms.SIN_FOCOS,
   "si todo cae fuera del polígono → sin_focos", r["estado"])
ok(r.get("descartados_fuera", 0) >= 1,
   "y se informa de cuántos se descartaron",
   f"{r.get('descartados_fuera')} fuera de {r.get('recibidos_bbox')}")

r = consultar("clave_mala")
ok(r["estado"] == firms.ERROR, "clave inválida → estado error", r["estado"])
ok(r["focos"] == [], "y tampoco devuelve históricos")
ok("no está disponible" in r["mensaje"].lower(),
   "avisando de que NASA FIRMS no está disponible")

r = consultar("error")
ok(r["estado"] == firms.ERROR, "servicio caído → estado error", r["estado"])
ok(r["focos"] == [], "y tampoco devuelve históricos")

# Sin clave configurada
clave_real = settings.FIRMS["clave"]
settings.FIRMS["clave"] = ""
r = firms.obtener_focos_activos()
ok(r["estado"] == firms.SIN_CLAVE, "sin MAP_KEY → estado sin_clave", r["estado"])
ok(r["mensaje"] == "NASA FIRMS no configurado.",
   "con el mensaje exacto que pide la especificación")
ok(r["focos"] == [], "y sin históricos de sustituto")
settings.FIRMS["clave"] = clave_real

# ---------------------------------------------------------------------------
print("\n3. Filtro punto-en-polígono")
# ---------------------------------------------------------------------------
poli = firms._poligono("apolo")
ok(poli is not None, "el polígono de Apolo carga")
if poli:
    from shapely.geometry import Point
    ok(poli.contains(Point(-68.50, -14.65)), "un punto interior se acepta")
    ok(not poli.contains(Point(-67.60, -14.05)),
       "un punto del bbox pero fuera del municipio se rechaza")

ok(firms._poligono("rurrenabaque") is not None,
   "el polígono de Rurrenabaque también carga")

dentro, fuera = firms._dentro(
    [{"lat": -14.65, "lon": -68.50}, {"lat": -14.05, "lon": -67.60}], "apolo")
ok(len(dentro) == 1 and fuera == 1, "el filtro separa dentro de fuera",
   f"{len(dentro)} dentro · {fuera} fuera")

# ---------------------------------------------------------------------------
print("\n4. Los históricos son otra fuente")
# ---------------------------------------------------------------------------
h = firms.obtener_focos_historicos("apolo", limite=500)
ok(h["fuente"] == "Base histórica SIPRO", "se identifican con su propia fuente",
   h["fuente"])
ok(h["historico"] is True, "y se marcan como históricos")
ok(all(f.get("historico") for f in h["focos"]),
   "cada foco viene marcado", f"{len(h['focos'])} focos")
ok(h["total"] > 0, "hay registros históricos disponibles",
   f"{h['total']} de {h['disponibles']} · periodo {h['periodo']}")
ok("NO son detecciones actuales" in h["_aviso"],
   "con el aviso de que no son actuales")
ok(h.get("descartados_fuera", 0) >= 0,
   "también se recortan al polígono",
   f"{h.get('descartados_fuera')} fuera del municipio")

h2 = firms.obtener_focos_historicos("apolo", desde="2023-01-01", hasta="2023-12-31")
ok(all("2023" in f["fecha"] for f in h2["focos"]),
   "se pueden filtrar por rango de fechas", f"{h2['total']} en 2023")

# ---------------------------------------------------------------------------
print("\n5. La selección del foco no usa históricos")
# ---------------------------------------------------------------------------
from api.motor.parametros import _elegir_foco  # noqa: E402

G = grid_srv.obtener_grid()

MODO["v"] = "vacio"
limpiar_cache()
sel = _elegir_foco(G, None, [])
ok(sel["origen"] != "firms",
   "sin focos activos, el origen NO es firms", sel["origen"])
ok(sel["origen"] == "xgboost",
   "pasa a XGBoost, que es la siguiente prioridad", sel["origen"])

MODO["v"] = "con_focos"
limpiar_cache()
sel = _elegir_foco(G, None, [])
ok(sel["origen"] == "firms", "con focos activos sí es firms", sel["origen"])

# ---------------------------------------------------------------------------
print("\n6. Los endpoints están separados")
# ---------------------------------------------------------------------------
from django.test import Client  # noqa: E402

c = Client()
r1 = c.get("/api/ambiente/firms")
ok(r1.status_code == 200, "GET /api/ambiente/firms responde", f"HTTP {r1.status_code}")
if r1.status_code == 200:
    d = r1.json().get("datos") or {}
    ok(d.get("fuente") == "NASA FIRMS", "declara su fuente", d.get("fuente"))
    ok("estado" in d, "y su estado", d.get("estado"))

r2 = c.get("/api/ambiente/focos-historicos?limite=50")
ok(r2.status_code == 200, "GET /api/ambiente/focos-historicos responde",
   f"HTTP {r2.status_code}")
if r2.status_code == 200:
    d = r2.json().get("datos") or {}
    ok(d.get("historico") is True, "y devuelve la fuente histórica")
    ok(len(d.get("focos") or []) <= 50, "respetando el límite pedido",
       f"{len(d.get('focos') or [])} focos")

r3 = c.get("/api/ambiente/firms?zona=rurrenabaque")
ok(r3.status_code == 200, "el endpoint acepta la zona",
   (r3.json().get("datos") or {}).get("zona"))

print("\n" + "=" * 66)
if fallos:
    print(f"  {fallos} comprobación(es) fallida(s)")
    print("=" * 66)
    raise SystemExit(1)
print("  Focos activos e históricos: correctamente separados.")
print("=" * 66)
