"""Pruebas de aceptación: un criterio por objetivo del trabajo de grado.

    cd backend_django
    python pruebas/prueba_aceptacion.py

Cada bloque comprueba que el sistema hace lo que el objetivo promete. A
diferencia de las de simulación o integración, aquí no se mira cómo está hecho
sino si el resultado sirve: es caja negra desde el punto de vista del usuario.

Las que dependen de datos que aún no existen (validación externa ejecutada,
calibración congelada) se marcan PENDIENTE y no cuentan como fallo, pero se
listan al final para que no se olviden.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PROYECTO = RAIZ.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "pruebas"))

import postgrest_falso  # noqa: E402

postgrest_falso.arrancar(8231)
os.environ.update(
    SUPABASE_URL="http://127.0.0.1:8231", SUPABASE_SERVICE_KEY="s" * 60,
    SUPABASE_ESQUEMA="public", PRECALENTAR="false",
    DJANGO_SETTINGS_MODULE="config.settings",
)

import django  # noqa: E402

django.setup()

from django.test import Client  # noqa: E402

fallos, pendientes = 0, []


def ok(cond, titulo, extra=""):
    global fallos
    if cond:
        print(f"  ✓ {titulo}" + (f" — {extra}" if extra else ""))
    else:
        fallos += 1
        print(f"  ✗ {titulo}" + (f" — {extra}" if extra else ""))
    return bool(cond)


def pendiente(titulo, que_falta):
    pendientes.append((titulo, que_falta))
    print(f"  · {titulo} — PENDIENTE: {que_falta}")


def entrar(email="analista@demo.sipro.com"):
    c = Client()
    r = c.post("/api/auth/login",
               data=json.dumps({"email": email, "password": "demo1234"}),
               content_type="application/json")
    return c if r.status_code == 200 else None


print("=" * 70)
print("PRUEBAS DE ACEPTACIÓN")
print("=" * 70)

cli = entrar()
ok(cli is not None, "un analista puede entrar al sistema")

# ---------------------------------------------------------------------------
print("\nOBJ 2 — Datos y modelo de ocurrencia disponibles")
# ---------------------------------------------------------------------------
from api.servicios import grid as grid_srv  # noqa: E402

grid_srv.cargar()
G = grid_srv.obtener_grid()
ok(len(G) > 30000, "el grid del municipio está cargado", f"{len(G):,} celdas")

COLS = ["id", "fila", "columna", "lat", "lon", "pendiente_grados",
        "ndvi", "humedad", "viento_u", "viento_v", "prob_ignicion"]
ok(all(c in G[0] for c in COLS),
   "el grid trae las once variables del modelo")

p = [float(c["prob_ignicion"] or 0) for c in G]
ok(0 <= min(p) and max(p) <= 1, "las probabilidades están en [0, 1]",
   f"{min(p):.4f}–{max(p):.4f}")
ok(len({round(x, 3) for x in p}) > 50,
   "la probabilidad discrimina entre celdas, no es constante",
   f"{len({round(x,3) for x in p})} valores distintos")

# ---------------------------------------------------------------------------
print("\nOBJ 3 — Modelo de simulación: integración y propagación")
# ---------------------------------------------------------------------------
from api.motor.automata import (  # noqa: E402
    CONSTANTES_POR_DEFECTO as K, _construir_vecindad, ejecutar_automata,
)
from api.motor.parametros import _elegir_foco  # noqa: E402

sel = _elegir_foco(G, None, [])
ok(sel["origen"] in ("firms", "xgboost", "historico"),
   "el sistema propone un foco inicial sin intervención del usuario",
   sel["origen"])
ok("detalle" in sel and sel["detalle"],
   "y explica de dónde salió ese foco", sel["detalle"][:52])

vec = _construir_vecindad(K["RADIO_VECINDAD"], K["EXP_DISTANCIA"],
                          K.get("VECINDAD", "moore"))
ok(len(vec) == 8, "la vecindad es la de Moore, con 8 vecinos", f"{len(vec)}")

r = ejecutar_automata(G, {
    "foco_fila": sel["celda"]["fila"], "foco_columna": sel["celda"]["columna"],
    "p_base": 0.30, "num_iteraciones": 24, "minutos_por_iteracion": 15,
    "multiplicador_viento": 1.0, "delta_humedad": 0.0,
    "delta_temperatura_c": 0.0, "semilla": 1,
})
ult = r["iteraciones"][-1]
ok(ult["num_celdas_quemadas"] > 0,
   "la simulación produce una superficie de propagación",
   f"{ult['num_celdas_quemadas']} celdas · "
   f"{ult['num_celdas_quemadas'] * 0.25:.1f} km²")
ok(len(r["iteraciones"]) == 25,
   "y devuelve la evolución paso a paso", f"{len(r['iteraciones'])} iteraciones")
ok("constantes" in r["metadatos"],
   "el resultado incluye los parámetros con los que se obtuvo")

# ---------------------------------------------------------------------------
print("\nOBJ 4 — Sistema web: los roles ven lo que les toca")
# ---------------------------------------------------------------------------
from api.almacen.permisos_defecto import MATRIZ_DEFECTO, PERMISOS, ROLES  # noqa: E402

ok(set(ROLES) == {"administrador", "analista", "ugr", "brigada"},
   "están los cuatro roles del sistema", ", ".join(ROLES))
ok(len(PERMISOS) >= 12, "la matriz cubre todos los permisos",
   f"{len(PERMISOS)} permisos")

# El brigadista: solo consulta y reporta.
b = MATRIZ_DEFECTO["brigada"]
cerrados = ["ejecutar_simulacion", "ver_simulaciones", "generar_reportes",
            "gestionar_usuarios", "configuracion", "ver_bitacora"]
ok(all(not b[c] for c in cerrados),
   "el brigadista no accede a nada técnico ni administrativo")
ok(b["reportar_incendio"] and b["ver_monitoreo"],
   "pero sí puede consultar el monitoreo y reportar")

# El administrador no opera el modelo.
a = MATRIZ_DEFECTO["administrador"]
ok(not a["ejecutar_simulacion"] and a["gestionar_usuarios"],
   "el administrador gestiona usuarios pero no ejecuta simulaciones")

# Y se cumple en el servidor, no solo en el menú.
cb = entrar("brigada@demo.sipro.com")
if cb is None:
    from api.almacen import db
    db.crear_usuario({"email": "brigada@demo.sipro.com", "password": "demo1234",
                      "nombre": "Brigadista", "rol": "brigada", "activo": True})
    cb = entrar("brigada@demo.sipro.com")
if cb:
    r1 = cb.post("/api/simulacion/ejecutar", data="{}",
                 content_type="application/json")
    ok(r1.status_code in (401, 403),
       "el servidor rechaza que un brigadista simule", f"HTTP {r1.status_code}")
    r2 = cb.post("/api/reportes-campo", content_type="application/json",
                 data=json.dumps({"lat": -14.65, "lon": -68.3,
                                  "estado": "humo_visible",
                                  "descripcion": "prueba de aceptación"}))
    ok(r2.status_code == 200, "y sí acepta su reporte de campo",
       f"HTTP {r2.status_code}")
    if r2.status_code == 200:
        msg = (r2.json().get("datos") or {}).get("mensaje", "")
        ok("revisi" in msg.lower(), "avisando de que queda para revisión", msg)
else:
    pendiente("control de acceso del brigadista por HTTP",
              "no se pudo crear el usuario de prueba")

if cli:
    r3 = cli.get("/api/reportes-campo")
    ok(r3.status_code == 200,
       "el analista ve los reportes que llegan del terreno",
       f"{len(r3.json().get('datos') or [])} reporte(s)")

# ---------------------------------------------------------------------------
print("\nOBJ 5 — Escenarios críticos")
# ---------------------------------------------------------------------------
from api.motor.escenarios import CATALOGO, REFERENCIAS  # noqa: E402

ok(len(CATALOGO) >= 12, "hay un catálogo de escenarios críticos",
   f"{len(CATALOGO)} escenarios")
familias = {e["familia"] for e in CATALOGO}
ok(len(familias) >= 4, "cubriendo varias familias de fenómeno",
   ", ".join(sorted(familias)))
ok(all(e.get("justificacion") for e in CATALOGO),
   "cada magnitud viene justificada")
ok(bool(REFERENCIAS), "las magnitudes se apoyan en referencias del territorio")

if cli:
    rc = cli.get("/api/simulacion/escenarios")
    ok(rc.status_code == 200, "el catálogo se sirve a la interfaz",
       f"HTTP {rc.status_code}")

# ---------------------------------------------------------------------------
print("\nOBJ 6 — Validación externa")
# ---------------------------------------------------------------------------
VAL = RAIZ / "validacion_rurrenabaque"
ok((VAL / "datos" / "rbq_roi.json").exists(),
   "el área de validación externa está verificada")
if (VAL / "datos" / "rbq_roi.json").exists():
    roi = json.loads((VAL / "datos" / "rbq_roi.json").read_text(encoding="utf-8"))
    ok(roi["municipio"]["departamento"] != "La Paz",
       "y es un municipio independiente del área de estudio",
       f"{roi['municipio']['nombre_oficial_en_dbf']}, {roi['municipio']['departamento']}")
    a1 = roi["area"]["km2_utm19s"]
    a2 = roi["area"]["km2_geodesica_wgs84"]
    ok(abs(a1 - a2) / a2 < 0.01,
       "su superficie se comprobó por dos vías independientes",
       f"{a1} y {a2} km²")

cfg = VAL / "config" / "parametros_ca_congelados.json"
if cfg.exists():
    c = json.loads(cfg.read_text(encoding="utf-8"))
    if c.get("p_base") is None:
        pendiente("parámetros congelados para la validación externa",
                  "falta calibrar y ejecutar congelar_parametros.py")
    else:
        ok(c.get("fecha_congelacion") is not None,
           "los parámetros están congelados con su fecha",
           f"p_base {c['p_base']} el {c['fecha_congelacion']}")

met = VAL / "resultados" / "metricas_validacion.json"
if met.exists():
    m = json.loads(met.read_text(encoding="utf-8"))
    ok(m.get("iou") is not None, "hay métricas de validación externa",
       f"IoU {m.get('iou')} · F1 {m.get('f1')}")
else:
    pendiente("métricas de validación externa",
              "falta la cicatriz observada y ejecutar f11 y f12")

# ---------------------------------------------------------------------------
print("\nTRAZABILIDAD — los resultados se pueden citar")
# ---------------------------------------------------------------------------
if cli:
    ra = cli.get("/api/simulacion/parametros-auto")
    if ok(ra.status_code == 200, "los parámetros derivados son consultables",
          f"HTTP {ra.status_code}"):
        d = ra.json().get("datos") or {}
        ok("diagnostico" in d and d["diagnostico"],
           "cada parámetro dice de dónde sale",
           f"{len(d.get('diagnostico') or [])} entradas")
        ok("actualizado" in d, "y cuándo se consultó cada fuente")

print("\n" + "=" * 70)
if pendientes:
    print(f"  {len(pendientes)} criterio(s) PENDIENTE(S):")
    for t, q in pendientes:
        print(f"    · {t}\n        {q}")
    print()
if fallos:
    print(f"  {fallos} criterio(s) NO cumplido(s)")
    print("=" * 70)
    raise SystemExit(1)
print("  Aceptación: todos los criterios verificables se cumplen.")
print("=" * 70)
