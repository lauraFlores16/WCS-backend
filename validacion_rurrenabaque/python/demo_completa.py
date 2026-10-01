"""
============================================================================
DEMOSTRACIÓN COMPLETA CON DATOS DE MENTIRA
============================================================================
Ruta: validacion_rurrenabaque/python/demo_completa.py

    cd validacion_rurrenabaque/python
    python demo_completa.py

QUÉ ES ESTO
    Un ensayo general. Fabrica datos falsos —focos, grid, cicatriz observada—
    y hace correr la cadena entera de la validación de principio a fin, en un
    solo comando y sin necesitar nada de fuera:

        sin clave de NASA FIRMS
        sin Google Earth Engine
        sin conexión a internet
        sin decidir todavía p_base

    Sirve para VER funcionar el sistema y entender qué produce cada paso antes
    de tener los datos de verdad. En unos 30 segundos deja en la carpeta
    `demo_salida/` exactamente los mismos archivos y figuras que producirá la
    validación real.

LO QUE NO ES
    Esto NO es la validación. Los números que salen no significan nada: el
    terreno es plano y uniforme, la cicatriz es una elipse dibujada a mano y
    los focos están inventados. No copies ni una cifra de aquí a la tesis.

    Lo único real es la MAQUINARIA: el autómata que se ejecuta es el de
    SIPRO FIRE, y las métricas se calculan con el mismo código que usará la
    validación de verdad.

QUÉ VERÁS
    Los cinco pasos, uno detrás de otro, con su salida:

      1. Fabricar los datos de mentira
      2. Verificar el grid            (f10)
      3. Ejecutar el autómata         (f11)
      4. Calcular las métricas        (f12)
      5. Dónde ha quedado cada cosa

CÓMO BORRARLO
    Se puede borrar la carpeta `demo_salida/` entera cuando quieras. No toca
    nada del proyecto ni de `resultados/`.
============================================================================
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

AQUI = Path(__file__).resolve().parent
SALIDA = AQUI.parent / "demo_salida"

# Convención del grid, la misma que Apolo: fila al SUR, columna al ESTE.
PASO = 0.0045
LAT_ORIGEN = -14.3420
LON_ORIGEN = -67.5630
N_FILAS, N_COLS = 70, 55


def titulo(n, t):
    print()
    print("=" * 74)
    print(f"  PASO {n} — {t}")
    print("=" * 74)


def correr(script: str, *args) -> int:
    """Lanza uno de los scripts reales y deja que escriba en pantalla."""
    cmd = [sys.executable, str(AQUI / script), *args]
    print(f"\n  $ python {script} " + " ".join(
        a if not a.startswith("/") and not a.startswith("\\") else Path(a).name
        for a in args))
    print()
    r = subprocess.run(cmd)
    return r.returncode


# ===========================================================================
print(__doc__.split("QUÉ ES ESTO")[0])
print("  Preparando la demostración…")

if SALIDA.exists():
    shutil.rmtree(SALIDA)
for sub in ("datos", "config", "resultados"):
    (SALIDA / sub).mkdir(parents=True)

# ===========================================================================
titulo(1, "FABRICAR LOS DATOS DE MENTIRA")
# ===========================================================================
print("""
  En la validación real estos tres archivos vienen de fuera:

    rbq_grid_500m.csv    del bloque 08 de Google Earth Engine
    observado_grid.csv   del bloque 07 de Google Earth Engine
    focos_iniciales.csv  de f3_f4_analisis_y_evento.py, que lee NASA FIRMS

  Aquí se fabrican para poder seguir sin ellos.
""")

rng = np.random.default_rng(42)

# --- El grid -----------------------------------------------------------
# Terreno con una loma en el centro y algo de variación, para que el autómata
# tenga algo que hacer y no se propague de forma perfectamente circular.
filas = []
for f in range(N_FILAS):
    for c in range(N_COLS):
        d = np.hypot(f - 35, c - 27)
        filas.append({
            "id": f"RBQ-{f:03d}-{c:03d}",
            "fila": f,
            "columna": c,
            "lat": round(LAT_ORIGEN - f * PASO, 6),   # fila crece al SUR
            "lon": round(LON_ORIGEN + c * PASO, 6),   # columna crece al ESTE
            "pendiente_grados": round(float(max(0, 18 - d * 0.4)), 2),
            "ndvi": round(float(np.clip(0.34 + rng.normal(0, 0.04), 0.12, 0.48)), 4),
            "humedad": round(float(np.clip(0.35 + rng.normal(0, 0.02), 0.22, 0.40)), 4),
            "viento_u": 0.42,      # viento hacia el ESTE
            "viento_v": 0.18,      # y algo hacia el NORTE
            "prob_ignicion": 0,    # XGBoost no se usa en Rurrenabaque
        })
grid = pd.DataFrame(filas)
grid.to_csv(SALIDA / "datos" / "rbq_grid_500m.csv", index=False)
print(f"  grid de mentira        : {len(grid):,} celdas de 500 m")

# --- La cicatriz "observada" -------------------------------------------
# Una elipse desplazada al noreste del foco, más una banda de "nubes" arriba
# para que se vea cómo funciona la exclusión de celdas sin dato.
obs = []
for _, r in grid.iterrows():
    f, c = int(r.fila), int(r.columna)
    dentro = ((f - 31) / 11) ** 2 + ((c - 33) / 9) ** 2 <= 1.0
    hay_nube = f < 6                      # 6 filas tapadas por nubes
    obs.append({
        "id": r.id, "fila": f, "columna": c, "lat": r.lat, "lon": r.lon,
        "observado": int(dentro),
        "fraccion_quemada": float(dentro),
        "valido": 0 if hay_nube else 1,
        "cobertura": 0.0 if hay_nube else 1.0,
    })
obs_df = pd.DataFrame(obs)
obs_df.to_csv(SALIDA / "datos" / "observado_grid.csv", index=False)
print(f"  cicatriz de mentira    : {int(obs_df.observado.sum()):,} celdas quemadas "
      f"({obs_df.observado.sum() * 0.25:.1f} km²)")
print(f"  celdas tapadas por nube: {int((obs_df.valido == 0).sum()):,} "
      f"(se excluirán de las métricas)")

# --- Los focos iniciales ------------------------------------------------
focos = []
for i, (df_, dc_) in enumerate([(0, 0), (0, 1), (1, 0), (1, 1)]):
    f, c = 36 + df_, 26 + dc_
    focos.append({
        "fecha": "2023-09-12", "hora": f"0{2 + i}:30",
        "lat": round(LAT_ORIGEN - f * PASO, 6),
        "lon": round(LON_ORIGEN + c * PASO, 6),
        "frp": round(20 + i * 9.5, 1),
        "sensor": "VIIRS_SNPP_SP",
        "grid_id": f"RBQ-{f:03d}-{c:03d}", "fila": f, "columna": c,
    })
pd.DataFrame(focos).to_csv(SALIDA / "resultados" / "focos_iniciales.csv", index=False)
print(f"  focos iniciales        : {len(focos)} celdas de arranque")

# --- El evento -----------------------------------------------------------
json.dump({
    "municipio": "Rurrenabaque", "anio": 2023,
    "evento_id": "DEMO (datos de mentira)",
    "fecha_inicio": "2023-09-12", "fecha_fin": "2023-09-14",
    "duracion_h": 36.0, "numero_focos": len(focos),
    "sensor_principal": "VIIRS_SNPP_SP",
    "criterio_seleccion": "ninguno: esto es una demostración",
}, open(SALIDA / "resultados" / "evento_validacion.json", "w", encoding="utf-8"),
    indent=2, ensure_ascii=False)

# --- Los parámetros, con p_base puesto SOLO para la demostración ---------
cfg = json.loads((AQUI.parent / "config" / "parametros_ca_congelados.json")
                 .read_text(encoding="utf-8"))
cfg["p_base"] = 0.30
cfg["fecha_congelacion"] = "DEMO"
cfg["ejecucion"]["n_repeticiones"] = 10       # 10 en vez de 30, para que sea rápido
json.dump(cfg, open(SALIDA / "config" / "parametros_ca_congelados.json", "w",
                    encoding="utf-8"), indent=2, ensure_ascii=False)
print(f"  p_base                 : 0.30  ← SOLO para la demostración.")
print(f"                           En la validación real hay que elegirlo tú.")
print(f"  repeticiones           : 10 (en la real son 30)")

D = lambda *p: str(SALIDA.joinpath(*p))

# ===========================================================================
titulo(2, "VERIFICAR EL GRID  ·  f10_verificar_grid_rbq.py")
# ===========================================================================
print("""
  Comprueba que el grid es compatible con el motor ANTES de gastar
  repeticiones. Lo más importante que mira: que la fila crezca hacia el SUR,
  como en Apolo. Si creciera al norte, el motor empujaría el fuego con el
  viento en la dirección contraria y no habría forma de darse cuenta mirando
  el resultado.

  Si algo falla aquí, se detiene y no se sigue.
""")
if correr("f10_verificar_grid_rbq.py",
          "--grid", D("datos", "rbq_grid_500m.csv"),
          "--informe", D("resultados", "f10_verificacion_grid.json")) != 0:
    print("\n  La verificación falló. En la demostración esto no debería pasar.")
    raise SystemExit(1)

# ===========================================================================
titulo(3, "EJECUTAR EL AUTÓMATA  ·  f11_ejecutar_ca_rbq.py")
# ===========================================================================
print("""
  Aquí corre el autómata celular DE VERDAD: el mismo `ejecutar_automata` de
  backend_django/api/motor/automata.py que usa SIPRO FIRE en Apolo. No es una
  copia ni una reimplementación.

  Lo ejecuta 10 veces con semillas 1 a 10. Verás que cada corrida quema una
  cantidad distinta de celdas: el modelo es estocástico, y por eso no se
  valida con una sola. De las 10 sale una PROBABILIDAD DE QUEMA por celda.
""")
if correr("f11_ejecutar_ca_rbq.py",
          "--grid", D("datos", "rbq_grid_500m.csv"),
          "--focos", D("resultados", "focos_iniciales.csv"),
          "--config", D("config", "parametros_ca_congelados.json"),
          "--evento", D("resultados", "evento_validacion.json"),
          "--resultados", D("resultados")) != 0:
    raise SystemExit(1)

# ===========================================================================
titulo(4, "CALCULAR LAS MÉTRICAS  ·  f12_metricas_validacion.py")
# ===========================================================================
print("""
  Cruza celda a celda lo observado con lo simulado y calcula TP, FP, FN y TN,
  y de ahí IoU, Precision, Recall, F1 y el error de área.

  Fíjate en el paso 2 de su salida: las celdas tapadas por nubes se EXCLUYEN.
  No cuentan como acierto ni como fallo. Sin eso, el modelo pagaría por una
  nube.
""")
if correr("f12_metricas_validacion.py",
          "--observado", D("datos", "observado_grid.csv"),
          "--simulado", D("resultados", "ca_resultado_final.csv"),
          "--focos", D("resultados", "focos_iniciales.csv"),
          "--evento", D("resultados", "evento_validacion.json"),
          "--ejecucion", D("resultados", "f11_resumen_ejecucion.json"),
          "--evolucion", D("resultados", "ca_evolucion_temporal.csv"),
          "--resultados", D("resultados")) != 0:
    raise SystemExit(1)

# ===========================================================================
titulo(5, "DÓNDE HA QUEDADO CADA COSA")
# ===========================================================================
print(f"\n  Todo en:  {SALIDA}\n")
for p in sorted((SALIDA / "resultados").iterdir()):
    kb = p.stat().st_size / 1024
    print(f"    {p.name:<34} {kb:>8.1f} KB")

print("""
  Los que van a la memoria:

    tabla_validacion.csv      la tabla de indicadores
    metricas_validacion.json  todas las cifras, para no copiar a mano
    resumen_validacion.md     el texto ya redactado
    fig_07_superposicion.png  el mapa TP/FP/FN con las métricas al lado

  Abre esa figura: es la que mejor se entiende de un vistazo. Verde donde el
  modelo acertó, naranja donde simuló de más, azul donde no llegó.

  Esta demostración NO alimenta la pestaña Validación de la aplicación: esa
  pantalla lee únicamente resultados reales del módulo validacion/, para que
  no haya forma de confundir un ensayo con una validación.

  ─────────────────────────────────────────────────────────────────────────
  RECUERDA: los números de esta demostración NO significan nada. El terreno
  es plano y uniforme y la cicatriz está dibujada a mano. Lo que acabas de
  comprobar es que la maquinaria funciona de punta a punta.

  Para la validación de verdad hacen falta tres cosas que no puedo darte yo:
    1. decidir p_base en config/parametros_ca_congelados.json
    2. tu clave de NASA FIRMS
    3. acceso a Google Earth Engine
  ─────────────────────────────────────────────────────────────────────────
""")
