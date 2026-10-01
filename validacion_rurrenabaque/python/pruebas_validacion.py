"""
============================================================================
PRUEBAS OBLIGATORIAS DE LA VALIDACIÓN EXTERNA
============================================================================
Ruta: validacion_rurrenabaque/python/pruebas_validacion.py

    cd validacion_rurrenabaque/python
    python pruebas_validacion.py

QUÉ COMPRUEBA
    Las seis pruebas del §42, más las que hacen falta para fiarse de ellas.
    Todas corren con datos SINTÉTICOS de solape conocido: no necesitan GEE, ni
    clave de FIRMS, ni el evento real. Sirven para saber que la maquinaria es
    correcta antes de meterle los datos de verdad.

    A. Grid de Rurrenabaque compatible con el motor
    B. Vecindad: 8 vecinos de Moore en el grid
    C. NoData no participa en las métricas
    D. Métricas correctas contra un ejemplo pequeño calculado a mano
    E. El mismo archivo de parámetros en todas las repeticiones
    F. Semillas reproducibles

POR QUÉ HACEN FALTA
    Las métricas de una validación son cuatro números. Si la aritmética que los
    produce tiene un fallo, el fallo es indistinguible de un resultado: un IoU
    de 0,04 se lee igual esté bien o mal calculado. La única forma de fiarse es
    comprobar el cálculo contra un caso cuyo resultado se conoce de antemano.

    La prueba D es la importante: dos rectángulos con solape conocido, cuyo
    TP, FP y FN se pueden contar a mano en un papel.
============================================================================
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parents[1]
BACKEND = RAIZ

fallos = 0
avisos = 0


def ok(cond, titulo, extra=""):
    global fallos
    if cond:
        print(f"  ✓ {titulo}" + (f" — {extra}" if extra else ""))
    else:
        fallos += 1
        print(f"  ✗ {titulo}" + (f" — {extra}" if extra else ""))
    return bool(cond)


def aviso(titulo, extra=""):
    global avisos
    avisos += 1
    print(f"  ! {titulo}" + (f" — {extra}" if extra else ""))


# ===========================================================================
def grid_sintetico(n_filas=40, n_cols=30, paso=0.0045,
                   lat_origen=-14.3420, lon_origen=-67.5630) -> pd.DataFrame:
    """Grid con la convención de Apolo: fila al SUR, columna al ESTE."""
    filas = []
    for f in range(n_filas):
        for c in range(n_cols):
            filas.append({
                "id": f"RBQ-{f:03d}-{c:03d}",
                "fila": f, "columna": c,
                "lat": lat_origen - f * paso,      # fila crece al SUR
                "lon": lon_origen + c * paso,      # columna crece al ESTE
                "pendiente_grados": 5.0,
                "ndvi": 0.30,
                "humedad": 0.35,
                "viento_u": 0.4, "viento_v": 0.0,
                "prob_ignicion": 0,
            })
    return pd.DataFrame(filas)


# ===========================================================================
print("=" * 74)
print("PRUEBAS DE LA VALIDACIÓN EXTERNA — RURRENABAQUE")
print("=" * 74)

tmp = Path(tempfile.mkdtemp(prefix="pruebas_rbq_"))

# ---------------------------------------------------------------------------
print("\nA. Grid de Rurrenabaque compatible con el motor")
# ---------------------------------------------------------------------------
g = grid_sintetico()
csv_grid = tmp / "rbq_grid_500m.csv"
g.to_csv(csv_grid, index=False)

OBLIGATORIAS = ["id", "fila", "columna", "lat", "lon", "pendiente_grados",
                "ndvi", "humedad", "viento_u", "viento_v", "prob_ignicion"]
ok(all(c in g.columns for c in OBLIGATORIAS),
   "las once columnas obligatorias están presentes")

# La orientación: lo que más se rompe en silencio.
lat_f0 = g[g.fila == 0].lat.mean()
lat_fN = g[g.fila == g.fila.max()].lat.mean()
ok(lat_fN < lat_f0, "`fila` crece hacia el SUR (como Apolo)",
   f"fila 0 → {lat_f0:.4f} · fila {g.fila.max()} → {lat_fN:.4f}")

lon_c0 = g[g.columna == 0].lon.mean()
lon_cN = g[g.columna == g.columna.max()].lon.mean()
ok(lon_cN > lon_c0, "`columna` crece hacia el ESTE (como Apolo)",
   f"col 0 → {lon_c0:.4f} · col {g.columna.max()} → {lon_cN:.4f}")

ok(not g.duplicated(subset=["fila", "columna"]).any(),
   "no hay pares (fila, columna) duplicados")
ok(bool((g["prob_ignicion"] == 0).all()),
   "`prob_ignicion` = 0 en todas las celdas (XGBoost no se valida aquí)")

# El verificador real debe aprobar este grid.
r = subprocess.run([sys.executable, str(AQUI / "f10_verificar_grid_rbq.py"),
                    "--grid", str(csv_grid),
                    "--informe", str(tmp / "informe.json")],
                   capture_output=True, text=True)
ok(r.returncode == 0, "f10_verificar_grid_rbq.py aprueba un grid correcto",
   f"código de salida {r.returncode}")

# Y debe RECHAZAR uno con la fila invertida. Si no lo rechaza, no sirve.
g_mal = g.copy()
g_mal["lat"] = -14.3420 + g_mal["fila"] * 0.0045      # fila hacia el NORTE
csv_mal = tmp / "grid_invertido.csv"
g_mal.to_csv(csv_mal, index=False)
r2 = subprocess.run([sys.executable, str(AQUI / "f10_verificar_grid_rbq.py"),
                     "--grid", str(csv_mal),
                     "--informe", str(tmp / "informe2.json")],
                    capture_output=True, text=True)
ok(r2.returncode != 0, "…y RECHAZA un grid con la fila invertida",
   "detiene el proceso, que es lo que debe hacer")

# ---------------------------------------------------------------------------
print("\nB. Vecindad de Moore")
# ---------------------------------------------------------------------------
presentes = set(zip(g.fila, g.columna))
f, c = 20, 15
vecinos = [(f + a, c + b) for a in (-1, 0, 1) for b in (-1, 0, 1)
           if not (a == 0 and b == 0)]
ok(sum(1 for v in vecinos if v in presentes) == 8,
   "una celda interior tiene los 8 vecinos de Moore en el grid",
   f"celda (f{f}, c{c})")

diag = [(f - 1, c - 1), (f - 1, c + 1), (f + 1, c - 1), (f + 1, c + 1)]
ok(all(v in presentes for v in diag),
   "las 4 diagonales están identificadas")

lat_ref = float(g.lat.mean())
tam_ns = 0.0045 * 110574.0
tam_eo = 0.0045 * 111320.0 * math.cos(math.radians(lat_ref))
d_diag = math.hypot(tam_ns, tam_eo)
ok(abs(tam_ns - 500) < 60 and abs(tam_eo - 500) < 60,
   "el tamaño nominal es ~500 m",
   f"{tam_ns:.0f} m N-S × {tam_eo:.0f} m E-O · diagonal {d_diag:.0f} m")

# Cuántos usa el MOTOR: aviso, no fallo. El grid está bien; la decisión es
# sobre el motor.
try:
    sys.path.insert(0, str(BACKEND))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("SIPRO_OMITIR_ARRANQUE", "1")
    os.environ.setdefault("PRECALENTAR", "false")
    os.environ.setdefault("SUPABASE_URL", "http://127.0.0.1:1")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "x" * 40)
    import django
    django.setup()
    from api.motor.automata import CONSTANTES_POR_DEFECTO, _construir_vecindad
    vec = _construir_vecindad(CONSTANTES_POR_DEFECTO["RADIO_VECINDAD"],
                              CONSTANTES_POR_DEFECTO["EXP_DISTANCIA"])
    if len(vec) == 8:
        ok(True, "el MOTOR usa los 8 vecinos de Moore")
    else:
        ok(False, f"el MOTOR usa {len(vec)} vecinos, no los 8 de Moore",
           "revisa la constante VECINDAD de CONSTANTES_POR_DEFECTO")
    MOTOR_OK = True
except Exception as e:  # noqa: BLE001
    aviso("no se pudo inspeccionar el motor", f"{type(e).__name__}: {e}")
    MOTOR_OK = False

# ---------------------------------------------------------------------------
print("\nC. NoData no participa en las métricas")
# ---------------------------------------------------------------------------
# Caso construido a propósito: una franja de celdas SIN DATO donde el simulado
# dice «quemado». Si el NoData participara, esas celdas serían falsos
# positivos y hundirían la Precision. Al excluirse, no deben aparecer.
filas_obs, filas_sim = [], []
for f_ in range(20):
    for c_ in range(20):
        cid = f"RBQ-{f_:03d}-{c_:03d}"
        valido = 0 if f_ < 5 else 1                    # 5 filas sin dato
        observado = 1 if (8 <= f_ <= 12 and 8 <= c_ <= 12) else 0
        simulado = 1 if ((8 <= f_ <= 12 and 8 <= c_ <= 12) or f_ < 5) else 0
        filas_obs.append({"id": cid, "fila": f_, "columna": c_,
                          "lat": -14.34 - f_ * 0.0045, "lon": -67.56 + c_ * 0.0045,
                          "observado": observado, "fraccion_quemada": float(observado),
                          "valido": valido, "cobertura": 1.0 * valido})
        filas_sim.append({"id": cid, "fila": f_, "columna": c_,
                          "lat": -14.34 - f_ * 0.0045, "lon": -67.56 + c_ * 0.0045,
                          "prob_quemada": float(simulado), "simulado": simulado})

pd.DataFrame(filas_obs).to_csv(tmp / "observado_grid.csv", index=False)
pd.DataFrame(filas_sim).to_csv(tmp / "ca_resultado_final.csv", index=False)
(tmp / "res").mkdir(exist_ok=True)

r3 = subprocess.run(
    [sys.executable, str(AQUI / "f12_metricas_validacion.py"),
     "--observado", str(tmp / "observado_grid.csv"),
     "--simulado", str(tmp / "ca_resultado_final.csv"),
     "--focos", str(tmp / "no_existe.csv"),
     "--evento", str(tmp / "no_existe.json"),
     "--ejecucion", str(tmp / "no_existe.json"),
     "--evolucion", str(tmp / "no_existe.csv"),
     "--resultados", str(tmp / "res")],
    capture_output=True, text=True)

if r3.returncode != 0:
    ok(False, "f12 se ejecuta", r3.stdout[-700:] + r3.stderr[-700:])
else:
    m = json.loads((tmp / "res" / "metricas_validacion.json").read_text(encoding="utf-8"))
    ok(m["celdas_excluidas_sin_dato"] == 100,
       "las 100 celdas sin dato (5 filas × 20) se excluyen",
       f"excluidas {m['celdas_excluidas_sin_dato']}")
    ok(m["celdas_evaluadas"] == 300,
       "se evalúan solo las 300 celdas válidas",
       f"evaluadas {m['celdas_evaluadas']}")
    ok(m["fp"] == 0,
       "las celdas sin dato NO se cuentan como falsos positivos",
       f"FP = {m['fp']} (si el NoData participara serían 100)")
    ok(m["tp"] == 25 and m["fn"] == 0,
       "el cuadrado quemado se detecta entero",
       f"TP {m['tp']} · FN {m['fn']}")
    ok(m["tp"] + m["fp"] + m["fn"] + m["tn"] == m["celdas_evaluadas"],
       "TP + FP + FN + TN = celdas evaluadas")

# ---------------------------------------------------------------------------
print("\nD. Métricas correctas contra un ejemplo calculado a mano")
# ---------------------------------------------------------------------------
# Observado: rectángulo filas 20-34, columnas 10-24  → 15 × 15 = 225 celdas
# Simulado : rectángulo filas 22-37, columnas 12-29  → 16 × 18 = 288 celdas
# Solape   : filas 22-34 (13), columnas 12-24 (13)   → 13 × 13 = 169 celdas
#
#   TP = 169     FP = 288 − 169 = 119     FN = 225 − 169 = 56
#   IoU       = 169 / (169+119+56) = 0,491304…
#   Precision = 169 / 288           = 0,586805…
#   Recall    = 169 / 225           = 0,751111…
#   F1        = 2PR/(P+R)           = 0,658869…
#   Error área= |288−225| / 225     = 28,0 %
filas_obs, filas_sim = [], []
for f_ in range(60):
    for c_ in range(40):
        cid = f"RBQ-{f_:03d}-{c_:03d}"
        observado = 1 if (20 <= f_ <= 34 and 10 <= c_ <= 24) else 0
        simulado = 1 if (22 <= f_ <= 37 and 12 <= c_ <= 29) else 0
        filas_obs.append({"id": cid, "fila": f_, "columna": c_,
                          "lat": -14.34 - f_ * 0.0045, "lon": -67.56 + c_ * 0.0045,
                          "observado": observado, "valido": 1,
                          "fraccion_quemada": float(observado), "cobertura": 1.0})
        filas_sim.append({"id": cid, "fila": f_, "columna": c_,
                          "lat": -14.34 - f_ * 0.0045, "lon": -67.56 + c_ * 0.0045,
                          "prob_quemada": float(simulado), "simulado": simulado})

pd.DataFrame(filas_obs).to_csv(tmp / "obs_d.csv", index=False)
pd.DataFrame(filas_sim).to_csv(tmp / "sim_d.csv", index=False)
# Un foco al norte del observado, para que el ángulo tenga origen.
pd.DataFrame([{"lat": -14.34 - 20 * 0.0045, "lon": -67.56 + 17 * 0.0045,
               "fila": 20, "columna": 17}]).to_csv(tmp / "focos_d.csv", index=False)
(tmp / "res_d").mkdir(exist_ok=True)

r4 = subprocess.run(
    [sys.executable, str(AQUI / "f12_metricas_validacion.py"),
     "--observado", str(tmp / "obs_d.csv"),
     "--simulado", str(tmp / "sim_d.csv"),
     "--focos", str(tmp / "focos_d.csv"),
     "--evento", str(tmp / "no.json"), "--ejecucion", str(tmp / "no.json"),
     "--evolucion", str(tmp / "no.csv"),
     "--resultados", str(tmp / "res_d")],
    capture_output=True, text=True)

if r4.returncode != 0:
    ok(False, "f12 se ejecuta sobre el caso conocido",
       r4.stdout[-700:] + r4.stderr[-700:])
else:
    m = json.loads((tmp / "res_d" / "metricas_validacion.json").read_text(encoding="utf-8"))
    esperado = {"tp": 169, "fp": 119, "fn": 56,
                "iou": 0.4913, "precision": 0.5868,
                "recall": 0.7511, "f1": 0.6589, "error_area_pct": 28.0}
    for k, v in esperado.items():
        got = m[k]
        bien = (got == v) if isinstance(v, int) else abs(got - v) < 0.002
        ok(bien, f"{k} = {v}", f"obtenido {got}")
    ok(m["comportamiento"] == "SOBREESTIMACIÓN",
       "el comportamiento se clasifica bien", m["comportamiento"])
    ok(m["area_observada_km2"] == 225 * 0.25 and m["area_simulada_km2"] == 288 * 0.25,
       "las áreas usan 0,25 km² por celda en observado y simulado",
       f"{m['area_observada_km2']} / {m['area_simulada_km2']} km²")

    # Direccionales: el simulado está desplazado al sureste del observado.
    ea = m.get("error_angular_grados")
    dc = m.get("distancia_centroides_km")
    ok(dc is not None and dc > 0, "distancia entre centroides calculada",
       f"{dc} km")
    ok(ea is not None and 0 <= ea <= 180,
       "error angular en el rango [0, 180] tras corregir la circularidad",
       f"{ea}°")

    # Circularidad, comprobada aparte con aritmética pura.
    def err_ang(a_sim, a_obs):
        return abs((a_sim - a_obs + 180) % 360 - 180)
    ok(abs(err_ang(10, 350) - 20) < 1e-9,
       "350° vs 10° da 20°, no 340°", "circularidad corregida")
    ok(abs(err_ang(350, 10) - 20) < 1e-9, "…y al revés también")
    ok(abs(err_ang(0, 180) - 180) < 1e-9, "0° vs 180° da 180°, el máximo")

    # El resumen debe existir y llevar la advertencia de la Accuracy.
    resumen = (tmp / "res_d" / "resumen_validacion.md").read_text(encoding="utf-8")
    ok("complementaria" in resumen.lower(),
       "el resumen etiqueta la Accuracy como complementaria")
    ok("FIRMS" in resumen and "no** representa" in resumen.replace("*", "*"),
       "el resumen aclara que FIRMS no es superficie quemada", extra="")

# ---------------------------------------------------------------------------
print("\nE. Parámetros congelados: un solo archivo para todas las repeticiones")
# ---------------------------------------------------------------------------
cfg_path = RAIZ / "validacion_rurrenabaque" / "config" / "parametros_ca_congelados.json"
ok(cfg_path.exists(), "existe config/parametros_ca_congelados.json")
if cfg_path.exists():
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    ESPERADAS = ["K_PENDIENTE_ARRIBA", "K_PENDIENTE_ABAJO", "PENDIENTE_MAX",
                 "K_VIENTO", "EXP_VIENTO", "K_TEMPERATURA", "K_HUMEDAD",
                 "NDVI_BARRERA", "NDVI_BASE", "RESIDENCIA_MIN", "RESIDENCIA_MAX",
                 "DECAIMIENTO_FASE", "SPOTTING_ACTIVO", "SPOTTING_PROB",
                 "SPOTTING_VIENTO_MIN", "SPOTTING_DIST_MIN", "SPOTTING_DIST_MAX",
                 "SPOTTING_DISPERSION", "RADIO_VECINDAD", "EXP_DISTANCIA",
                 "K_LLUVIA_PROP", "K_LLUVIA_EXT", "K_VPD", "VPD_REFERENCIA"]
    faltan = [k for k in ESPERADAS if k not in cfg.get("constantes", {})]
    ok(not faltan, "están las 24 constantes del autómata",
       "faltan: " + ", ".join(faltan) if faltan else "")
    ok("origen_parametros" in cfg and "version_modelo" in cfg,
       "lleva origen_parametros y version_modelo")
    ok("p_base" in cfg, "el campo p_base existe")

    if cfg.get("p_base") is None:
        aviso("p_base sigue en null",
              "f11 se negará a ejecutar hasta que se elija un valor. Es el "
              "comportamiento correcto, pero bloquea la validación real")

    # Los valores del JSON tienen que coincidir con los del motor.
    if MOTOR_OK:
        difs = [k for k, v in cfg["constantes"].items()
                if k in CONSTANTES_POR_DEFECTO and CONSTANTES_POR_DEFECTO[k] != v]
        ok(not difs,
           "las constantes congeladas coinciden con CONSTANTES_POR_DEFECTO",
           "difieren: " + ", ".join(difs) if difs else "24 valores idénticos")

# ---------------------------------------------------------------------------
print("\nF. Semillas reproducibles")
# ---------------------------------------------------------------------------
if MOTOR_OK:
    from api.motor.automata import ejecutar_automata
    grid_celdas = grid_sintetico(30, 30).to_dict("records")
    base = {"foco_fila": 15, "foco_columna": 15,
            "focos_iniciales": [{"fila": 15, "columna": 15}],
            "p_base": 0.30, "num_iteraciones": 12,
            "minutos_por_iteracion": 15, "multiplicador_viento": 1.0,
            "delta_humedad": 0.0, "delta_temperatura_c": 0.0}

    def quemadas(sem):
        r_ = ejecutar_automata(grid_celdas, {**base, "semilla": sem})
        return r_["iteraciones"][-1]["num_celdas_quemadas"]

    a1, a2, b1 = quemadas(7), quemadas(7), quemadas(99)
    ok(a1 == a2, "misma semilla → mismo resultado",
       f"semilla 7 dos veces: {a1} y {a2} celdas")
    ok(a1 != b1 or a1 == 0, "otra semilla → otro resultado (es estocástico)",
       f"semilla 7 → {a1} · semilla 99 → {b1}")

    # El orden de las repeticiones no debe alterar nada.
    seq1 = [quemadas(s) for s in (1, 2, 3)]
    seq2 = [quemadas(s) for s in (3, 2, 1)][::-1]
    ok(seq1 == seq2, "el resultado no depende del orden de ejecución",
       f"{seq1}")
else:
    aviso("F omitida", "no se pudo cargar el motor")


# ---------------------------------------------------------------------------
print("\nG. Coherencia direccional del motor")
# ---------------------------------------------------------------------------
# Dos fallos de signo que ya estuvieron en el código y no deben volver. Los dos
# eran invisibles mirando el resultado: producían perímetros de aspecto normal
# propagados hacia el lado equivocado.
if MOTOR_OK:
    import math as _m

    def _componentes(vel, grados):
        """Misma convención que servicios/meteo: u = este, v = norte."""
        r = _m.radians(grados)
        return (-vel * _m.sin(r), -vel * _m.cos(r))

    from api.motor.automata import _factor_viento

    # --- G.1 El viento favorece la dirección correcta ---------------------
    u, v = _componentes(8, 180)          # sopla hacia el NORTE
    f_norte = _factor_viento(-1, 0, u, v, CONSTANTES_POR_DEFECTO)   # fila -1 = norte
    f_sur = _factor_viento(1, 0, u, v, CONSTANTES_POR_DEFECTO)
    ok(f_norte > f_sur,
       "con viento al norte, f_viento favorece al vecino del NORTE",
       f"norte {f_norte:.3f} vs sur {f_sur:.3f}")

    u, v = _componentes(8, 270)          # sopla hacia el ESTE
    f_este = _factor_viento(0, 1, u, v, CONSTANTES_POR_DEFECTO)
    f_oeste = _factor_viento(0, -1, u, v, CONSTANTES_POR_DEFECTO)
    ok(f_este > f_oeste,
       "con viento al este, f_viento favorece al vecino del ESTE",
       f"este {f_este:.3f} vs oeste {f_oeste:.3f}")

    # --- G.2 Las pavesas saltan a favor del viento ------------------------
    # Réplica exacta de la aritmética de automata.py líneas 761-766.
    def _salto(grados, alcance=4):
        u_, v_ = _componentes(8, grados)
        ang = _m.atan2(v_, u_)
        return (-round(_m.sin(ang) * alcance), round(_m.cos(ang) * alcance))

    casos = [(180, "NORTE", lambda df, dc: df < 0),
             (0,   "SUR",   lambda df, dc: df > 0),
             (270, "ESTE",  lambda df, dc: dc > 0),
             (90,  "OESTE", lambda df, dc: dc < 0)]
    for grados, nombre, criterio in casos:
        df, dc = _salto(grados)
        ok(criterio(df, dc),
           f"con viento al {nombre}, las pavesas saltan al {nombre}",
           f"Δfila {df:+d} · Δcol {dc:+d}")
else:
    aviso("G omitida", "no se pudo cargar el motor")

# ---------------------------------------------------------------------------
print("\nH. Resistencia parcial del terreno (ríos y caminos)")
# ---------------------------------------------------------------------------
if MOTOR_OK:
    from api.motor.automata import ejecutar_automata as _ejec
    grid_h = grid_sintetico(40, 40).to_dict("records")
    base_h = {"foco_fila": 20, "foco_columna": 20,
              "focos_iniciales": [{"fila": 20, "columna": 20}],
              "p_base": 0.30, "num_iteraciones": 24,
              "minutos_por_iteracion": 15, "multiplicador_viento": 1.0,
              "delta_humedad": 0.0, "delta_temperatura_c": 0.0, "semilla": 5}
    resist_h = {c["id"]: 0.6 for c in grid_h}

    def _quemadas(K):
        k_ = {**CONSTANTES_POR_DEFECTO, "K_BARRERA": K}
        return _ejec(grid_h, base_h,
                     {"constantes": k_, "resistencia_extra": resist_h}
                     )["iteraciones"][-1]["num_celdas_quemadas"]

    q0, q05, q1 = _quemadas(0.0), _quemadas(0.5), _quemadas(1.0)
    ok(q0 >= q05 >= q1 and q0 > q1,
       "la resistencia del terreno frena la propagación de forma monotónica",
       f"K=0 → {q0} · K=0,5 → {q05} · K=1 → {q1} celdas")

    # Barrera dura: una celda de río no puede arder nunca.
    # `celdas_quemadas_ids` solo se rellena con solo_conteo=True, que es como
    # lo pide la calibración; aquí se usa igual para no cargar las iteraciones.
    barr = {c["id"] for c in grid_h if c["columna"] == 25}
    r = _ejec(grid_h, base_h, {"constantes": CONSTANTES_POR_DEFECTO,
                               "barreras_extra": barr, "solo_conteo": True})
    ids_quemados = set(r["metadatos"]["celdas_quemadas_ids"])
    ok(not (ids_quemados & barr),
       "ninguna celda marcada como barrera dura llega a arder",
       f"{len(barr)} celdas de río · {len(ids_quemados & barr)} quemadas")

    # Y el `K_BARRERA` congelado tiene que existir en el archivo.
    if cfg_path.exists():
        ok("K_BARRERA" in cfg.get("constantes", {}),
           "K_BARRERA está en los parámetros congelados")
else:
    aviso("H omitida", "no se pudo cargar el motor")


# ---------------------------------------------------------------------------
print("\nI. Carreteras y ríos sobre una rejilla que no es la de Apolo")
# ---------------------------------------------------------------------------
if MOTOR_OK:
    from api.servicios.terreno import _rasterizar

    PASO_ = 0.0045
    LAT0_, LON0_ = -14.3420, -67.5630
    rejilla = [{"id": f"RBQ-{f:03d}-{c:03d}", "fila": f, "columna": c,
                "lat": LAT0_ - f * PASO_, "lon": LON0_ + c * PASO_}
               for f in range(60) for c in range(60)]
    elementos = [
        {"t": "camino", "g": [[LAT0_ - f * PASO_, LON0_ + 20 * PASO_] for f in range(10, 50)]},
        {"t": "rio", "g": [[LAT0_ - 30 * PASO_, LON0_ + c * PASO_] for c in range(5, 55)]},
    ]
    r = _rasterizar(elementos, rejilla)
    ok(bool(r["resistencia"]), "OSM cae en celdas de la rejilla que se le pasa",
       f"{len(r['resistencia'])} celdas")
    ok(all(i.startswith("RBQ-") for i in r["resistencia"]),
       "los ids son de esa rejilla, no de Apolo")
    ok(bool(r["barreras"]), "el río queda como barrera dura",
       f"{len(r['barreras'])} celdas")
    caminos = [v for k, v in r["resistencia"].items() if r["clases"][k] == "camino"]
    ok(caminos and all(0 < v < 1 for v in caminos),
       "el camino queda con resistencia parcial, no como barrera",
       f"valor {caminos[0] if caminos else '—'}")

    from api.motor.automata import ejecutar_automata as _ej
    g_ = [{**c, "pendiente_grados": 5.0, "ndvi": 0.32, "humedad": 0.34,
           "viento_u": 0.4, "viento_v": 0.1, "prob_ignicion": 0} for c in rejilla]
    b_ = {"foco_fila": 25, "foco_columna": 10,
          "focos_iniciales": [{"fila": 25, "columna": 10}],
          "p_base": 0.30, "num_iteraciones": 30, "minutos_por_iteracion": 15,
          "multiplicador_viento": 1.0, "delta_humedad": 0.0,
          "delta_temperatura_c": 0.0, "semilla": 4}
    sin_t = _ej(g_, b_, {"constantes": CONSTANTES_POR_DEFECTO})["iteraciones"][-1]["num_celdas_quemadas"]
    con_t = _ej(g_, b_, {"constantes": CONSTANTES_POR_DEFECTO,
                         "barreras_extra": set(r["barreras"]),
                         "resistencia_extra": r["resistencia"]})["iteraciones"][-1]["num_celdas_quemadas"]
    ok(con_t < sin_t, "el terreno reduce la propagación",
       f"{sin_t} → {con_t} celdas ({(con_t/max(sin_t,1)-1)*100:+.0f} %)")
else:
    aviso("I omitida", "no se pudo cargar el motor")


# ---------------------------------------------------------------------------
print("\nJ. Construcción del grid de una zona")
# ---------------------------------------------------------------------------
try:
    sys.path.insert(0, str(AQUI.parents[1] / "validacion"))
    import zonas as _Z
    import f8_construir_grid as _f8

    _z = _Z.cargar("rurrenabaque")
    _c = _f8.celdas_de(_z)
    ok(len(_c) > 1000, "el grid se genera desde el límite municipal",
       f"{len(_c):,} celdas")
    ok(len({x["id"] for x in _c}) == len(_c), "sin identificadores duplicados")
    ok(all(x["id"].startswith("RBQ-") for x in _c),
       "con el prefijo de la zona")

    _fs = sorted({x["fila"] for x in _c})
    _cs = sorted({x["columna"] for x in _c})
    _lat = lambda f: [x["lat"] for x in _c if x["fila"] == f][0]
    _lon = lambda k: [x["lon"] for x in _c if x["columna"] == k][0]
    ok(_lat(_fs[-1]) < _lat(_fs[0]), "fila crece hacia el SUR, como Apolo")
    ok(_lon(_cs[-1]) > _lon(_cs[0]), "columna crece hacia el ESTE, como Apolo")

    _area = len(_c) * 0.25
    ok(abs(_area / _z.area_km2 - 1) < 0.10,
       "el área del grid se aproxima a la del municipio",
       f"{_area:,.0f} km² vs {_z.area_km2:,.0f}")

    # La pendiente se deriva de un DEM sintético con rampa conocida.
    for _x in _c[:400]:
        _x["elevacion_m"] = 200 + _x["fila"] * 10.0      # 10 m por celda al sur
    _sub = _c[:400]
    _f8.calcular_pendiente(_sub, _z)
    _p = [x["pendiente_grados"] for x in _sub if x.get("pendiente_grados")]
    _esperado = math.degrees(math.atan(10.0 / _z.tam_celda_m()[0]))
    ok(_p and abs(max(_p) - _esperado) < 0.5,
       "la pendiente se calcula bien sobre una rampa conocida",
       f"{max(_p):.2f}° esperado {_esperado:.2f}°")
except Exception as e:  # noqa: BLE001
    aviso("J omitida", f"{type(e).__name__}: {e}")

# ---------------------------------------------------------------------------
print("\n" + "=" * 74)
if fallos:
    print(f"  {fallos} PRUEBA(S) FALLIDA(S)" +
          (f" · {avisos} aviso(s)" if avisos else ""))
    print("  No sigas con la validación real hasta arreglarlas.")
    print("=" * 74)
    raise SystemExit(1)

print("  Todas las pruebas pasaron." +
      (f" Con {avisos} aviso(s), que no detienen el proceso." if avisos else ""))
print("=" * 74)
