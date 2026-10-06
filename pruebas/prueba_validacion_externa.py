"""
Validación externa en vivo (Rurrenabaque E122) — módulo del dashboard.

    cd WCS-backend
    python pruebas/prueba_validacion_externa.py

Comprueba tres cosas:

  1. CONTROL DE ACCESO: el analista ve y ejecuta; el administrador no.
  2. REPRODUCIBILIDAD: la corrida lanzada desde la API, con las 30 semillas y
     los parámetros congelados, da EXACTAMENTE las métricas oficiales que
     produjo la cadena f11 + f12 en local. Si no coinciden, algo cambió en el
     motor, en los datos o en los insumos, y la validación ya no es la misma.
  3. COHERENCIA: las métricas cuadran entre sí (matriz, áreas, F1).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "pruebas"))

import postgrest_falso  # noqa: E402

postgrest_falso.arrancar(8243)
os.environ.update(
    SUPABASE_URL="http://127.0.0.1:8243", SUPABASE_SERVICE_KEY="s" * 60,
    SUPABASE_ESQUEMA="public", PRECALENTAR="false",
    DJANGO_SETTINGS_MODULE="config.settings",
)

import django  # noqa: E402

django.setup()

from django.test import Client  # noqa: E402

fallos = 0


def ok(cond, titulo, extra=""):
    global fallos
    if not cond:
        fallos += 1
    print(f"  {'✓' if cond else '✗'} {titulo}" + (f" — {extra}" if extra else ""))
    return bool(cond)


def entrar(cli, email):
    r = cli.post("/api/auth/login", data=json.dumps({"email": email, "password": "demo1234"}),
                 content_type="application/json")
    return r.status_code == 200


def esperar(cli, tid, limite_s=600):
    t0 = time.time()
    while time.time() - t0 < limite_s:
        d = cli.get(f"/api/validacion/rurrenabaque/ejecucion/{tid}").json()["datos"]
        if d["estado"] != "ejecutando":
            return d
        time.sleep(0.5)
    return {"estado": "timeout"}


print("=" * 70)
print("VALIDACIÓN EXTERNA EN VIVO — Rurrenabaque E122")
print("=" * 70)

anal, admin, anon = Client(), Client(), Client()
ok(entrar(anal, "analista@demo.sipro.com"), "el analista inicia sesión")
ok(entrar(admin, "admin@demo.sipro.com"), "el administrador inicia sesión")

print("\n1. Control de acceso")
ok(anon.get("/api/validacion/rurrenabaque").status_code == 401, "sin sesión → 401")
ok(admin.get("/api/validacion/rurrenabaque").status_code == 403, "administrador → 403 (no es operativo)")
ok(admin.post("/api/validacion/rurrenabaque/ejecutar", data="{}",
              content_type="application/json").status_code == 403, "el administrador no puede ejecutarla")
r = anal.get("/api/validacion/rurrenabaque")
ok(r.status_code == 200, "el analista ve el contexto", f"HTTP {r.status_code}")
ctx = r.json()["datos"]

print("\n2. Contexto")
ok(ctx["evento"]["evento_id"] == "E122", "evento E122", ctx["evento"].get("fecha_inicio"))
ok(ctx["parametros"]["p_base"] is not None, "p_base congelado", ctx["parametros"]["p_base"])
ok(ctx["oficial"] and ctx["oficial"].get("iou") is not None, "métricas oficiales disponibles",
   f"IoU {ctx['oficial']['iou']}")
ok(ctx["insumos"]["horas_clima"] > 0, "clima ERA5 grabado", f"{ctx['insumos']['horas_clima']} h")
ok(any(c["observado"] == 1 for c in ctx["celdas"]), "la ventana del mapa contiene la cicatriz",
   f"{len(ctx['celdas'])} celdas")
ok(anal.post("/api/validacion/rurrenabaque/ejecutar", data=json.dumps({"repeticiones": 99}),
             content_type="application/json").status_code == 400, "rechaza más de 30 repeticiones")

print("\n3. Reproducibilidad: 30 repeticiones desde la API")
r = anal.post("/api/validacion/rurrenabaque/ejecutar", data=json.dumps({"repeticiones": 30}),
              content_type="application/json")
ok(r.status_code == 200, "la ejecución arranca")
fin = esperar(anal, r.json()["datos"]["id"])
ok(fin["estado"] == "terminado", "termina sin error", fin.get("error") or "")
res = fin.get("resultado") or {}
rep = res.get("reproduce_oficial") or {}
ok(bool(rep), "compara contra el resultado oficial")
for k, v in rep.items():
    ok(v["coincide"], f"{k} coincide", f"vivo {v['vivo']} · oficial {v['oficial']}")

print("\n4. Coherencia de las métricas")
m = res.get("metricas") or {}
if m:
    ok(m["tp"] + m["fp"] + m["fn"] + m["tn"] == m["celdas_evaluadas"], "la matriz suma las celdas evaluadas")
    ok(abs(m["area_simulada_km2"] - (m["tp"] + m["fp"]) * 0.25) < 1e-9, "área simulada = (TP+FP)·0,25 km²")
    f1 = 2 * m["precision"] * m["recall"] / (m["precision"] + m["recall"])
    ok(abs(f1 - m["f1"]) < 1e-3, "F1 = media armónica de precisión y recall")
    ok(len(res["repeticiones"]) == 30, "30 repeticiones registradas")
    ok(all(len(v) == 30 for v in res["igniciones"].values()), "una hora de ignición por repetición y celda")

print("\n5. Sensibilidad sin spotting (no reemplaza al oficial)")
r = anal.post("/api/validacion/rurrenabaque/ejecutar",
              data=json.dumps({"repeticiones": 30, "spotting": False}), content_type="application/json")
fin = esperar(anal, r.json()["datos"]["id"])
res2 = fin.get("resultado") or {}
ok(res2.get("reproduce_oficial") is None, "no se compara con el oficial (es otra configuración)")
ok(res2.get("metricas", {}).get("iou") is not None, "produce métricas propias",
   f"IoU {res2.get('metricas', {}).get('iou')} · área {res2.get('metricas', {}).get('area_simulada_km2')} km²")
sens = ctx.get("sin_spotting") or {}
if sens and ctx.get("sin_spotting_vigente"):
    ok(res2["metricas"]["iou"] == sens.get("iou"), "coincide con la sensibilidad guardada")
elif sens:
    ok(sens.get("p_base") != ctx["parametros"]["p_base"],
       "la sensibilidad guardada se marca como desactualizada",
       f"se hizo con p_base {sens.get('p_base')}, el congelado es {ctx['parametros']['p_base']}")

print("\n6. Multizona: paquetes de validacion_zonas")
r = anal.get("/api/validacion/paquetes")
ok(r.status_code == 200, "lista de paquetes", f"HTTP {r.status_code}")
paqs = r.json()["datos"]["paquetes"] if r.status_code == 200 else []
ok(any(p["historico"] for p in paqs), "incluye el histórico de Rurrenabaque E122")
nuevos = [p for p in paqs if not p["historico"] and p["iou"] is not None]
ok(admin.get("/api/validacion/paquetes").status_code == 403, "el administrador no los ve")
ok(anal.get("/api/validacion/paquetes/no-existe").status_code == 404, "paquete inexistente → 404")
ok(anal.get("/api/validacion/zonas/..%2Fetc/2023").status_code in (400, 404), "rechaza rutas raras")
for p in nuevos[:2]:
    c = anal.get(f"/api/validacion/paquetes/{p['id']}")
    ok(c.status_code == 200, f"contexto de {p['id']}", c.json()["datos"]["rol"] if c.status_code == 200 else "")
    r = anal.post(f"/api/validacion/paquetes/{p['id']}/ejecutar", data=json.dumps({"repeticiones": 30}),
                  content_type="application/json")
    fin = esperar(anal, r.json()["datos"]["id"])
    rep = (fin.get("resultado") or {}).get("reproduce_oficial") or {}
    ok(rep and all(v["coincide"] for v in rep.values()),
       f"{p['id']}: la corrida del dashboard reproduce la de p3_validar_evento.py",
       f"IoU {(fin.get('resultado') or {}).get('metricas', {}).get('iou')}")

print("\n" + "=" * 70)
print("  Validación externa en vivo: todo correcto." if not fallos
      else f"  {fallos} comprobación(es) fallaron.")
print("=" * 70)
sys.exit(1 if fallos else 0)
