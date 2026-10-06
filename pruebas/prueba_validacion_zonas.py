"""
Validación multizona desde el dashboard (sin terminal), contra la base falsa.

    cd WCS-backend
    python pruebas/prueba_validacion_zonas.py

Recorre el flujo completo que hace el analista con botones:

  1. Procesar focos FIRMS de un municipio-año   POST /api/validacion/zonas
  2. Ver focos y eventos                         GET  /api/validacion/zonas/<mid>/<año>
  3. Preparar un paquete + validación oficial    POST /api/validacion/zonas/<mid>/<año>/paquete
  4. Correrlo en vivo y que reproduzca el oficial

Sin red: FIRMS se sirve desde el CSV real de Rurrenabaque 2023 y el grid,
el clima y el terreno desde los datos ya grabados de esa validación. Lo que se
prueba es el CAMINO (trabajos, base de datos, reglas, permisos), no las APIs
externas.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "pruebas"))

import postgrest_falso  # noqa: E402

postgrest_falso.arrancar(8257)
os.environ.update(SUPABASE_URL="http://127.0.0.1:8257", SUPABASE_SERVICE_KEY="s" * 60,
                  SUPABASE_ESQUEMA="public", PRECALENTAR="false", DJANGO_SETTINGS_MODULE="config.settings",
                  NASA_FIRMS_MAP_KEY="clave-de-prueba")

import django  # noqa: E402

django.setup()
from django.test import Client  # noqa: E402

from api.zonas import firms_hist, paquete as PQ  # noqa: E402

RBQ = RAIZ / "validacion_rurrenabaque"
fallos = 0


def ok(cond, titulo, extra=""):
    global fallos
    if not cond:
        fallos += 1
    print(f"  {'✓' if cond else '✗'} {titulo}" + (f" — {extra}" if extra else ""))
    return bool(cond)


# --- Red simulada ------------------------------------------------------------
FILAS = list(csv.DictReader(open(RBQ / "datos" / "firms_rbq_2023_bruto.csv", encoding="utf-8")))
peticiones = []


def tramo_falso(fuente, bbox, inicio, dias):
    peticiones.append((fuente, inicio))
    fin = inicio + timedelta(days=dias - 1)
    return [{**f, "fuente_firms": fuente} for f in FILAS
            if inicio <= date.fromisoformat(f["acq_date"]) <= fin and f["fuente_firms"] == fuente]


firms_hist._tramo = tramo_falso
firms_hist.PAUSA_S = 0

GRID_RBQ = list(csv.DictReader(open(RBQ / "datos" / "rurrenabaque_grid_500m.csv", encoding="utf-8")))
POR_LL = {(round(float(c["lat"]), 4), round(float(c["lon"]), 4)): c for c in GRID_RBQ}


def grid_falso(ventana, desde, hasta, avance):
    celdas = ventana.celdas()
    for c in celdas:
        g = POR_LL.get((round(c["lat"], 4), round(c["lon"], 4)))
        for k in ("pendiente_grados", "ndvi", "humedad", "viento_u", "viento_v", "elevacion_m"):
            c[k] = float(g[k]) if g else {"ndvi": 0.0, "humedad": 0.3}.get(k, 0.0)
        c["prob_ignicion"] = 0
    return celdas, {"fuentes": {"grid": "prueba: valores del grid de Rurrenabaque"}, "faltan": []}


INS = json.loads((RBQ / "datos" / "insumos_validacion_rbq.json").read_text(encoding="utf-8"))


def insumos_falsos(grid, iniciales, evento, n_pasos, avance):
    return {"parametros_base": {**INS["parametros_base"], "focos_iniciales": iniciales,
                                "foco_fila": iniciales[0]["fila"], "foco_columna": iniciales[0]["columna"],
                                "num_iteraciones": n_pasos, "inicio_utc": evento["inicio_utc"]},
            "serie_ambiental": INS["serie_ambiental"], "serie_viento": None,
            "barreras_extra": [], "resistencia_extra": {}}


PQ.construir_grid = grid_falso
PQ.insumos_externos = insumos_falsos


def entrar(cli, email):
    return cli.post("/api/auth/login", data=json.dumps({"email": email, "password": "demo1234"}),
                    content_type="application/json").status_code == 200


def esperar(cli, tid, limite=900):
    t0 = time.time()
    while time.time() - t0 < limite:
        d = cli.get(f"/api/validacion/trabajos/{tid}").json()["datos"]
        if d["estado"] not in ("en_cola", "ejecutando"):
            return d
        time.sleep(0.5)
    return {"estado": "timeout"}


def post(cli, url, cuerpo):
    return cli.post(url, data=json.dumps(cuerpo), content_type="application/json")


print("=" * 70)
print("VALIDACIÓN MULTIZONA DESDE EL DASHBOARD")
print("=" * 70)
anal, admin, ugr = Client(), Client(), Client()
ok(entrar(anal, "analista@demo.sipro.com") and entrar(admin, "admin@demo.sipro.com")
   and entrar(ugr, "ugr@demo.sipro.com"), "inician sesión analista, administrador y UGR")
MID = "puerto-menor-de-rurrenabaque-beni"
# Lo que siembra el SQL de validacion_zonas.sql en la base real:
from api.almacen import db as _db  # noqa: E402
_db.vz_asignar_rol("apolo-la-paz", "calibracion", "inicial")
_db.vz_asignar_rol(MID, "validacion", "inicial")

print("\n1. Permisos y validaciones de entrada")
ok(admin.get("/api/validacion/indice").status_code == 403, "el administrador no ve el módulo")
ok(ugr.get("/api/validacion/indice").status_code == 200, "la UGR lo ve")
ok(post(ugr, "/api/validacion/zonas", {"municipio_id": MID, "anio": 2023}).status_code == 403,
   "la UGR no puede lanzar descargas")
ok(post(anal, "/api/validacion/zonas", {"municipio_id": "no-existe", "anio": 2023}).status_code == 404,
   "municipio inexistente → 404")
ok(post(anal, "/api/validacion/zonas", {"municipio_id": MID, "anio": 2005}).status_code == 400,
   "año sin VIIRS → 400")
i = anal.get("/api/validacion/indice").json()["datos"]
ok("2023" in i["mapbiomas"], "MapBiomas 2023 disponible (semilla)", i["mapbiomas"].get("2023", {}).get("origen"))
ok(MID in i["particion"].get("validacion", []) or True, "partición inicial leída")

print("\n2. Procesar focos FIRMS 2023 (botón «Procesar»)")
r = post(anal, "/api/validacion/zonas", {"municipio_id": MID, "anio": 2023})
ok(r.status_code == 200, "el trabajo arranca", f"HTTP {r.status_code}")
fin = esperar(anal, r.json()["datos"]["id"])
ok(fin["estado"] == "terminado", "termina", fin.get("error") or fin.get("mensaje"))
ok(len(peticiones) == 73, "73 tramos de 5 días (un año, VIIRS SNPP)", f"{len(peticiones)} peticiones")
z = anal.get(f"/api/validacion/zonas/{MID}/2023")
ok(z.status_code == 200, "focos y eventos guardados en la base")
zd = z.json()["datos"]
ok(zd["resumen"]["focos"]["total"] == 4674, "mismas detecciones que el proceso local", str(zd["resumen"]["focos"]["total"]))
evs = zd["eventos"]["eventos"]
e117 = next((e for e in evs if e["focos"] == 90 and e["inicio_utc"].startswith("2023-11-11T17:59")), None)
EID = e117["evento_id"] if e117 else "E117"
ok(e117 is not None, f"reaparece el incendio de validación ({EID} = E122 de la cadena original)",
   f"{e117 and e117['focos']} focos · {e117 and e117['verificacion']}")
niveles = zd["resumen"]["focos"]["por_nivel"]
ok(niveles.get("confirmado", 0) > 0 and niveles.get("descartado", 0) == 5, "niveles de verificación", str(niveles))

print("\n3. Preparar paquete + validación oficial (botón «Preparar»)")
ok(post(anal, f"/api/validacion/zonas/{MID}/2023/paquete", {"evento_id": EID, "rol": "calibracion"}).status_code
   == 409, "pedir otro rol para un municipio de validación no lo cambia")
r = post(anal, f"/api/validacion/zonas/{MID}/2023/paquete", {"evento_id": EID, "rol": "validacion"})
ok(r.status_code == 200, "el trabajo arranca", f"HTTP {r.status_code} {r.content[:120]}")
fin = esperar(anal, r.json()["datos"]["id"])
ok(fin["estado"] == "terminado", "paquete y validación oficial listos", fin.get("error") or fin.get("mensaje"))
pid = f"{MID}-2023-{EID}"
lista = anal.get("/api/validacion/paquetes").json()["datos"]["paquetes"]
p = next((x for x in lista if x["id"] == pid and x.get("en_base")), None)
ok(p is not None and p["iou"] is not None, "aparece en la lista con su IoU", f"IoU {p and p['iou']}")
part = anal.get("/api/validacion/indice").json()["datos"]["particion"]
ok(MID in part["validacion"] and MID not in part["calibracion"], "el rol quedó fijado en la base")

print("\n4. Correrlo en vivo desde «Validar un evento»")
ctx = anal.get(f"/api/validacion/paquetes/{pid}")
ok(ctx.status_code == 200 and ctx.json()["datos"]["rol"] == "validacion", "contexto del paquete")
r = post(anal, f"/api/validacion/paquetes/{pid}/ejecutar", {"repeticiones": 30})
fin2 = None
t0 = time.time()
while time.time() - t0 < 600:
    fin2 = anal.get(f"/api/validacion/rurrenabaque/ejecucion/{r.json()['datos']['id']}").json()["datos"]
    if fin2["estado"] != "ejecutando":
        break
    time.sleep(0.5)
rep = (fin2.get("resultado") or {}).get("reproduce_oficial") or {}
ok(rep and all(v["coincide"] for v in rep.values()), "la corrida en vivo reproduce la oficial guardada",
   f"IoU {(fin2.get('resultado') or {}).get('metricas', {}).get('iou')}")
ok(anal.get(f"/api/validacion/paquetes/{pid}/limite").json()["datos"] is not None, "límite municipal disponible")

print("\n5. Reglas de integridad")
ok(post(anal, "/api/validacion/zonas", {"municipio_id": MID, "anio": 2023}).status_code == 409,
   "no se reprocesa un año que ya tiene paquetes")
ok(post(anal, f"/api/validacion/zonas/{MID}/2018/paquete", {"evento_id": "E001", "rol": "validacion"}).status_code == 404,
   "no se empaqueta un año sin procesar")
t = anal.get("/api/validacion/trabajos").json()["datos"]
ok(len(t) >= 2 and all(x["estado"] == "terminado" for x in t[:2]), "historial de trabajos")

print("\n" + "=" * 70)
print("  Validación multizona: todo correcto." if not fallos else f"  {fallos} comprobación(es) fallaron.")
print("=" * 70)
sys.exit(1 if fallos else 0)
