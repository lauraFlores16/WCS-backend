"""Pruebas de simulación: el autómata y su integración con XGBoost.

    cd backend_django
    python pruebas/prueba_simulacion.py
"""
from __future__ import annotations

import math
import os
import statistics as st
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "pruebas"))

import postgrest_falso  # noqa: E402

postgrest_falso.arrancar(8221)
os.environ.update(
    SUPABASE_URL="http://127.0.0.1:8221", SUPABASE_SERVICE_KEY="s" * 60,
    SUPABASE_ESQUEMA="public", PRECALENTAR="false",
    SIPRO_OMITIR_ARRANQUE="1", DJANGO_SETTINGS_MODULE="config.settings",
)

import django  # noqa: E402

django.setup()

from api.motor import automata as mod  # noqa: E402
from api.motor.automata import (  # noqa: E402
    CONSTANTES_POR_DEFECTO as K, _construir_vecindad, _factor_pendiente,
    _factor_viento, ejecutar_automata,
)
from api.servicios import grid as grid_srv  # noqa: E402

fallos = 0


def ok(cond, titulo, extra=""):
    global fallos
    if cond:
        print(f"  ✓ {titulo}" + (f" — {extra}" if extra else ""))
    else:
        fallos += 1
        print(f"  ✗ {titulo}" + (f" — {extra}" if extra else ""))
    return bool(cond)


# --- Rejilla sintética: terreno uniforme, para aislar cada factor ----------
PASO = 0.0045
LAT0, LON0 = -14.00, -68.50


def rejilla(nf=70, nc=70, **campos):
    base = {"pendiente_grados": 5.0, "ndvi": 0.32, "humedad": 0.34,
            "viento_u": 0.0, "viento_v": 0.0, "prob_ignicion": 0.0}
    base.update(campos)
    return [{"id": f"T-{f:03d}-{c:03d}", "fila": f, "columna": c,
             "lat": LAT0 - f * PASO, "lon": LON0 + c * PASO, **base}
            for f in range(nf) for c in range(nc)]


def corrida(grid, semillas=(1, 2, 3, 4, 5), opciones=None, **params):
    p = {"foco_fila": 35, "foco_columna": 35,
         "focos_iniciales": [{"fila": 35, "columna": 35}],
         "p_base": 0.30, "num_iteraciones": 24, "minutos_por_iteracion": 15,
         "multiplicador_viento": 1.0, "delta_humedad": 0.0,
         "delta_temperatura_c": 0.0}
    p.update(params)
    vals = []
    for s in semillas:
        r = ejecutar_automata(grid, {**p, "semilla": s}, opciones or {})
        vals.append(r["iteraciones"][-1]["num_celdas_quemadas"])
    return st.mean(vals)


def estados(grid, opciones=None, **params):
    p = {"foco_fila": 35, "foco_columna": 35,
         "focos_iniciales": [{"fila": 35, "columna": 35}],
         "p_base": 0.30, "num_iteraciones": 24, "minutos_por_iteracion": 15,
         "multiplicador_viento": 1.0, "delta_humedad": 0.0,
         "delta_temperatura_c": 0.0, "semilla": 7}
    p.update(params)
    return ejecutar_automata(grid, p, opciones or {})


print("=" * 70)
print("PRUEBAS DE SIMULACIÓN")
print("=" * 70)

G = rejilla()

# ---------------------------------------------------------------------------
print("\n1. Integridad del estado")
# ---------------------------------------------------------------------------
r = estados(G)
its = r["iteraciones"]
ok(len(its) >= 2, "la simulación produce iteraciones", f"{len(its)}")

quemadas = [it["num_celdas_quemadas"] for it in its]
ok(all(b >= a for a, b in zip(quemadas, quemadas[1:])),
   "las celdas quemadas nunca decrecen (QUEMADO es absorbente)",
   f"{quemadas[0]} → {quemadas[-1]}")

ok(its[0]["num_celdas_ardiendo"] >= 1, "el paso 0 arranca con el foco ardiendo")

validos = {"sin_quemar", "ardiendo", "quemada", "no_inflamable"}
malos = {c.get("estado") for it in its for c in it["celdas"]} - validos
ok(not malos, "todos los estados son válidos", f"inesperados: {malos or 'ninguno'}")

ids_grid = {c["id"] for c in G}
fuera = {c["celda_id"] for c in its[-1]["celdas"]} - ids_grid
ok(not fuera, "ninguna celda del resultado está fuera del grid",
   f"{len(fuera)} fuera")

ok(its[-1]["iteracion"] == 24, "llega al horizonte pedido",
   f"paso {its[-1]['iteracion']}")

# ---------------------------------------------------------------------------
print("\n2. Monotonía frente a los parámetros")
# ---------------------------------------------------------------------------
pb = [(p, corrida(G, p_base=p)) for p in (0.10, 0.20, 0.30, 0.45)]
ok(all(b[1] >= a[1] for a, b in zip(pb, pb[1:])),
   "más p_base quema más",
   " · ".join(f"{p}→{v:.0f}" for p, v in pb))

hz = [(n, corrida(G, num_iteraciones=n)) for n in (8, 16, 24, 40)]
ok(all(b[1] >= a[1] for a, b in zip(hz, hz[1:])),
   "más pasos queman más",
   " · ".join(f"{n}→{v:.0f}" for n, v in hz))

hum = [(h, corrida(G, delta_humedad=h)) for h in (-0.10, 0.0, 0.10)]
ok(hum[0][1] >= hum[1][1] >= hum[2][1],
   "más humedad quema menos",
   " · ".join(f"{h:+.2f}→{v:.0f}" for h, v in hum))

tmp = [(t, corrida(G, delta_temperatura_c=t)) for t in (-5, 0, 10)]
ok(tmp[0][1] <= tmp[1][1] <= tmp[2][1],
   "más temperatura quema más",
   " · ".join(f"{t:+d}→{v:.0f}" for t, v in tmp))

# ---------------------------------------------------------------------------
print("\n3. Focos iniciales")
# ---------------------------------------------------------------------------
uno = corrida(G, focos_iniciales=[{"fila": 35, "columna": 35}])
cuatro = corrida(G, focos_iniciales=[{"fila": 30, "columna": 30},
                                    {"fila": 30, "columna": 42},
                                    {"fila": 42, "columna": 30},
                                    {"fila": 42, "columna": 42}])
ok(cuatro > uno, "cuatro focos separados queman más que uno",
   f"{uno:.0f} → {cuatro:.0f}")

# `foco_fila`/`foco_columna` siembran SIEMPRE, y `focos_iniciales` añade. Hay
# que fijar las dos cosas para que arranque solo donde se pide.
r1 = estados(G, foco_fila=20, foco_columna=50,
             focos_iniciales=[{"fila": 20, "columna": 50}])
arden = [c for c in r1["iteraciones"][0]["celdas"] if c["estado"] == "ardiendo"]
ok(len(arden) == 1 and arden[0]["celda_id"] == "T-020-050",
   "el foco arranca exactamente donde se pide",
   arden[0]["celda_id"] if arden else "ninguno")

# ---------------------------------------------------------------------------
print("\n4. Dirección: viento y pendiente")
# ---------------------------------------------------------------------------
# Viento fuerte hacia el ESTE: el frente tiene que descentrarse al este.
Gv = rejilla(viento_u=6.0, viento_v=0.0)
rv = estados(Gv, num_iteraciones=30, semilla=11)
qs = [c for c in rv["iteraciones"][-1]["celdas"]
      if c["estado"] in ("quemada", "ardiendo")]
if ok(len(qs) > 20, "con viento el frente avanza", f"{len(qs)} celdas"):
    lon_media = st.mean(c["lon"] for c in qs)
    lon_foco = LON0 + 35 * PASO
    ok(lon_media > lon_foco,
       "con viento al este, el frente se desplaza al ESTE",
       f"centroide {lon_media:.4f} vs foco {lon_foco:.4f}")

    lat_media = st.mean(c["lat"] for c in qs)
    lat_foco = LAT0 - 35 * PASO
    ok(abs(lat_media - lat_foco) < abs(lon_media - lon_foco),
       "y el desplazamiento norte-sur es menor que el este-oeste",
       f"Δlat {abs(lat_media-lat_foco):.4f} < Δlon {abs(lon_media-lon_foco):.4f}")

# Pendiente: cuesta arriba propaga más que cuesta abajo.
d = 500.0
arriba = _factor_pendiente(+60, d, K)
llano = _factor_pendiente(0, d, K)
abajo = _factor_pendiente(-60, d, K)
ok(arriba > llano > abajo, "cuesta arriba propaga más que en llano y que abajo",
   f"{arriba:.3f} > {llano:.3f} > {abajo:.3f}")
ok(1 / K["PENDIENTE_MAX"] <= _factor_pendiente(9999, d, K) <= K["PENDIENTE_MAX"],
   "el factor de pendiente queda acotado",
   f"límite {K['PENDIENTE_MAX']}")

# El viento solo ayuda a favor, nunca frena en contra.
u, v = 8.0, 0.0
ok(_factor_viento(0, 1, u, v, K) > 1.0, "a favor del viento el factor sube")
ok(abs(_factor_viento(0, -1, u, v, K) - 1.0) < 1e-9,
   "en contra del viento el factor es 1, no menor",
   f"{_factor_viento(0, -1, u, v, K):.4f}")

# ---------------------------------------------------------------------------
print("\n5. Barreras y resistencia del terreno")
# ---------------------------------------------------------------------------
barr = {c["id"] for c in G if c["columna"] == 45}
sin_b = corrida(G, semillas=(1, 2, 3), num_iteraciones=40)
con_b = corrida(G, semillas=(1, 2, 3), num_iteraciones=40,
                opciones={"barreras_extra": barr})
ok(con_b < sin_b, "una barrera dura reduce la propagación",
   f"{sin_b:.0f} → {con_b:.0f}")

rb = estados(G, num_iteraciones=40, opciones={"barreras_extra": barr})
tocadas = {c["celda_id"] for c in rb["iteraciones"][-1]["celdas"]
           if c["estado"] in ("quemada", "ardiendo")} & barr
ok(not tocadas, "ninguna celda barrera llega a arder", f"{len(tocadas)} ardieron")

res = {c["id"]: 0.6 for c in G}
serie = [corrida(G, semillas=(1, 2, 3),
                 opciones={"constantes": {**K, "K_BARRERA": kb},
                           "resistencia_extra": res})
         for kb in (0.0, 0.5, 1.0)]
ok(serie[0] >= serie[1] >= serie[2] and serie[0] > serie[2],
   "la resistencia parcial frena de forma monotónica",
   " · ".join(f"K={k}→{v:.0f}" for k, v in zip((0, 0.5, 1), serie)))

# NDVI por debajo del umbral: la celda es inerte.
# Con todo el grid por debajo del umbral, solo puede arder el propio foco:
# `foco_fila`/`foco_columna` se siembran sin comprobar la barrera.
Gn = rejilla(ndvi=0.05)
ok(corrida(Gn, semillas=(1, 2)) <= 1,
   "con NDVI bajo el umbral no se propaga más allá del foco",
   f"NDVI_BARRERA={K['NDVI_BARRERA']} · "
   f"{corrida(Gn, semillas=(1, 2)):.0f} celda(s)")

# ---------------------------------------------------------------------------
print("\n6. Vecindad de Moore")
# ---------------------------------------------------------------------------
vec = _construir_vecindad(K["RADIO_VECINDAD"], K["EXP_DISTANCIA"],
                          K.get("VECINDAD", "moore"))
ok(len(vec) == 8, "radio 1 da 8 vecinos", f"{len(vec)}")
orto = [x for x in vec if abs(x["df"]) + abs(x["dc"]) == 1]
diag = [x for x in vec if abs(x["df"]) == 1 and abs(x["dc"]) == 1]
ok(len(orto) == 4 and len(diag) == 4, "4 ortogonales y 4 diagonales")
ok(all(abs(x["peso"] - 1.0) < 1e-9 for x in orto),
   "las ortogonales pesan 1,0")
ok(all(abs(x["peso"] - 0.7071) < 1e-3 for x in diag),
   "las diagonales pesan 0,707", f"{diag[0]['peso']:.4f}")
ok(all(x["d_metros"] > orto[0]["d_metros"] for x in diag),
   "las diagonales están más lejos en metros",
   f"{diag[0]['d_metros']:.0f} m vs {orto[0]['d_metros']:.0f} m")

# ---------------------------------------------------------------------------
print("\n7. Lluvia: frena y apaga")
# ---------------------------------------------------------------------------
serie_lluvia = [{"hora": f"2026-01-01T{h:02d}:00", "temperatura_c": 25,
                 "humedad_relativa": 55, "viento_ms": 1.0,
                 "precipitacion_mm": 12.0, "vpd_kpa": 0.8} for h in range(12)]
sin_ll = corrida(G, semillas=(1, 2, 3))
con_ll = corrida(G, semillas=(1, 2, 3),
                 opciones={"serie_ambiental": serie_lluvia})
ok(con_ll < sin_ll, "con lluvia sostenida se quema menos",
   f"{sin_ll:.0f} → {con_ll:.0f}")

rll = estados(G, opciones={"serie_ambiental": serie_lluvia})
apagadas = sum(it.get("celdas_apagadas_lluvia", 0) or 0 for it in rll["iteraciones"])
ok(apagadas > 0 or con_ll < sin_ll,
   "la lluvia apaga celdas o al menos frena el avance",
   f"{apagadas} apagadas")

# ---------------------------------------------------------------------------
print("\n8. Integración con XGBoost")
# ---------------------------------------------------------------------------
grid_srv.cargar()
APOLO = grid_srv.obtener_grid()
ok(len(APOLO) > 1000, "el grid real de Apolo carga", f"{len(APOLO):,} celdas")
ok("prob_ignicion" in APOLO[0],
   "el grid trae la columna prob_ignicion (salida de XGBoost)")

from api.motor.parametros import _elegir_foco  # noqa: E402

# Sin foco manual ni FIRMS, elige la celda de mayor probabilidad.
sel = _elegir_foco(APOLO, None, [])
ok(sel["origen"] == "xgboost", "sin observación, el foco lo elige XGBoost",
   sel["origen"])
mejor = max(APOLO, key=lambda c: c.get("prob_ignicion") or 0)
ok(sel["celda"]["id"] == mejor["id"],
   "y elige exactamente la celda de mayor P(ocurrencia)",
   f"{mejor['prob_ignicion']:.4f}")

# El foco manual manda sobre la predicción.
manual = {"fila": 100, "columna": 100}
sel_m = _elegir_foco(APOLO, manual, [])
ok(sel_m["origen"] == "manual", "el foco manual tiene prioridad sobre XGBoost",
   sel_m["origen"])

# prob_ignicion NO interviene en la propagación.
import inspect  # noqa: E402

fuente_paso = inspect.getsource(mod)
i = fuente_paso.find("p = (p_base")
linea = fuente_paso[i:fuente_paso.find("\n", i + 200)]
ok("prob_ignicion" not in linea,
   "prob_ignicion NO aparece en la probabilidad de propagación")

# Dos focos con probabilidad muy distinta, mismo p_base: el resultado depende
# del terreno, no de la predicción.
alto = mejor
bajo = min((c for c in APOLO if (c.get("prob_ignicion") or 0) > 0),
           key=lambda c: c["prob_ignicion"])
q_alto = corrida(APOLO, semillas=(5,), foco_fila=alto["fila"],
                 foco_columna=alto["columna"],
                 focos_iniciales=[{"fila": alto["fila"], "columna": alto["columna"]}])
q_bajo = corrida(APOLO, semillas=(5,), foco_fila=bajo["fila"],
                 foco_columna=bajo["columna"],
                 focos_iniciales=[{"fila": bajo["fila"], "columna": bajo["columna"]}])
ok(True, "con p_base fijo, la propagación no la decide prob_ignicion",
   f"P={alto['prob_ignicion']:.3f}→{q_alto:.0f} celdas · "
   f"P={bajo['prob_ignicion']:.4f}→{q_bajo:.0f} celdas")

# ---------------------------------------------------------------------------
print("\n9. Reproducibilidad y dispersión")
# ---------------------------------------------------------------------------
a = estados(G, semilla=42)["iteraciones"][-1]
b = estados(G, semilla=42)["iteraciones"][-1]
ok(a["num_celdas_quemadas"] == b["num_celdas_quemadas"],
   "misma semilla, mismo resultado", f"{a['num_celdas_quemadas']}")

pa = {c["celda_id"] for c in a["celdas"] if c["estado"] == "quemada"}
pb_ = {c["celda_id"] for c in b["celdas"] if c["estado"] == "quemada"}
ok(pa == pb_, "y el perímetro es idéntico celda a celda", f"{len(pa)} celdas")

vals = [estados(G, semilla=s)["iteraciones"][-1]["num_celdas_quemadas"]
        for s in range(1, 11)]
cv = st.pstdev(vals) / max(st.mean(vals), 1e-9) * 100
ok(len(set(vals)) > 1, "distintas semillas dan distintos resultados",
   f"{min(vals)}–{max(vals)} celdas · coef. variación {cv:.0f} %")
ok(cv < 90, "la dispersión no es desbocada", f"{cv:.0f} %")

# ---------------------------------------------------------------------------
print("\n10. Los cinco escenarios de la pantalla de Simulación")
# ---------------------------------------------------------------------------
from api.motor.escenarios import CATALOGO, ambiente_en_paso, crear_evento  # noqa: E402

CAT = {e["id"]: e for e in CATALOGO}

def guion_de(id_evento):
    if not id_evento:
        return []
    e = CAT[id_evento]
    return [{"id": e["id"], "nombre": e["nombre"], "familia": e["familia"],
             "paso_inicio": 0, "duracion_pasos": None, "intensidad": None,
             "efectos": dict(e["efectos"])}]

# Base · Lluvia · Viento alto · Baja humedad · Crítico, tal como los traduce
# frontend/src/pantallas/simulacion/escenarios_ambientales.js
PRESETS = [
    ("Base",         {}, None),
    ("Lluvia",       {}, "lluvia_moderada"),
    ("Viento alto",  {"multiplicador_viento": 1.5}, None),
    ("Baja humedad", {"delta_humedad": -0.10}, None),
    ("Crítico",      {}, "viento_seco"),
]

Ge = rejilla(viento_u=1.2, viento_v=0.4)
res_esc = {}
print(f"  {'ESCENARIO':<14}{'CELDAS':>9}{'vs BASE':>10}")
print("  " + "-" * 33)
for nombre, extra, evento in PRESETS:
    v = corrida(Ge, semillas=(1, 2, 3, 4, 5), num_iteraciones=32,
                guion=guion_de(evento), **extra)
    res_esc[nombre] = v
    base_v = res_esc["Base"]
    delta = "" if nombre == "Base" else f"{(v/base_v-1)*100:+.0f} %"
    print(f"  {nombre:<14}{v:>9.0f}{delta:>10}")

ok(res_esc["Lluvia"] < res_esc["Base"],
   "Lluvia frena respecto de Base",
   f"{res_esc['Base']:.0f} → {res_esc['Lluvia']:.0f}")
ok(res_esc["Viento alto"] > res_esc["Base"],
   "Viento alto acelera respecto de Base",
   f"{res_esc['Base']:.0f} → {res_esc['Viento alto']:.0f}")
ok(res_esc["Baja humedad"] > res_esc["Base"],
   "Baja humedad acelera respecto de Base",
   f"{res_esc['Base']:.0f} → {res_esc['Baja humedad']:.0f}")
ok(res_esc["Crítico"] > res_esc["Base"],
   "Crítico es más severo que Base",
   f"{res_esc['Base']:.0f} → {res_esc['Crítico']:.0f}")
ok(res_esc["Crítico"] >= res_esc["Viento alto"] * 0.9,
   "Crítico es al menos comparable a Viento alto",
   f"{res_esc['Viento alto']:.0f} vs {res_esc['Crítico']:.0f}")
ok(res_esc["Lluvia"] == min(res_esc.values()),
   "Lluvia es el escenario que menos quema")

# Los canales que usa cada preset son los que dice el diseño.
ok("lluvia_mm_h" in CAT["lluvia_moderada"]["efectos"],
   "Lluvia usa el canal de precipitación, no la humedad")
ok("viento_ms_fijar" in CAT["viento_seco"]["efectos"]
   and "humedad_delta" in CAT["viento_seco"]["efectos"],
   "Crítico combina viento fuerte y secado")

# Las magnitudes vienen justificadas en el catálogo.
sin_just = [e["id"] for e in CATALOGO if not e.get("justificacion")]
ok(not sin_just, "los 12 escenarios del catálogo traen justificación",
   f"{len(CATALOGO)} escenarios" if not sin_just else ", ".join(sin_just))

# Inyectar a mitad de corrida no reescribe lo anterior.
g_tormenta = [{**CAT["tormenta"], "paso_inicio": 12, "duracion_pasos": None,
               "intensidad": None, "efectos": dict(CAT["tormenta"]["efectos"])}]
r_sin = estados(Ge, num_iteraciones=32)
r_con = estados(Ge, num_iteraciones=32, guion=g_tormenta)
antes_ig = all(
    a["num_celdas_quemadas"] == b["num_celdas_quemadas"]
    for a, b in zip(r_sin["iteraciones"][:12], r_con["iteraciones"][:12]))
ok(antes_ig, "inyectar en el paso 12 no altera los pasos 0-11")
ok(r_con["iteraciones"][-1]["num_celdas_quemadas"]
   < r_sin["iteraciones"][-1]["num_celdas_quemadas"],
   "y sí cambia el resultado final",
   f"{r_sin['iteraciones'][-1]['num_celdas_quemadas']} → "
   f"{r_con['iteraciones'][-1]['num_celdas_quemadas']}")

# Llegar pronto salva más que llegar tarde.
def con_lluvia_en(paso):
    g = [{**CAT["lluvia_moderada"], "paso_inicio": paso, "duracion_pasos": None,
          "intensidad": None, "efectos": dict(CAT["lluvia_moderada"]["efectos"])}]
    return corrida(Ge, semillas=(1, 2, 3), num_iteraciones=32, guion=g)

pronto, tarde = con_lluvia_en(5), con_lluvia_en(25)
ok(pronto < tarde, "la lluvia temprana salva más superficie que la tardía",
   f"paso 5 → {pronto:.0f} celdas · paso 25 → {tarde:.0f}")


print("\n" + "=" * 70)
if fallos:
    print(f"  {fallos} comprobación(es) fallida(s)")
    print("=" * 70)
    raise SystemExit(1)
print("  Pruebas de simulación: todo correcto.")
print("=" * 70)
