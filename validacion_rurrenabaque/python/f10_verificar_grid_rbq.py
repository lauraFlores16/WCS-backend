"""
============================================================================
FASE 10 — VERIFICACIÓN DEL GRID DE RURRENABAQUE
============================================================================
Ruta: validacion_rurrenabaque/python/f10_verificar_grid_rbq.py

QUÉ HACE
    Comprueba que rbq_grid_500m.csv es compatible con el motor de SIPRO FIRE
    ANTES de gastar 30 ejecuciones del autómata. Si algo falla, DETIENE el
    proceso con código de salida distinto de cero.

POR QUÉ ESTE PASO NO ES OPCIONAL
    El autómata no valida su entrada: recibe una lista de celdas y confía en
    ella. Los tres fallos más probables son silenciosos, o sea que no dan
    error y producen resultados con aspecto normal:

      · FILA INVERTIDA. Si `fila` creciera hacia el norte en vez de hacia el
        sur, el motor calcularía la influencia del viento y de la pendiente en
        la dirección contraria. El incendio se propagaría hacia el lado
        equivocado y las métricas medirían un modelo que no es el calibrado en
        Apolo. Es la comprobación número uno.

      · CELDAS DUPLICADAS. Dos filas del CSV con el mismo (fila, columna) hacen
        que una sobrescriba a la otra al construir el índice. Se pierden datos
        sin que nadie se entere.

      · COORDENADAS QUE NO CASAN CON LOS ÍNDICES. Si lat/lon y fila/columna se
        calcularon con orígenes distintos, el cruce con la máscara observada
        empareja celdas equivocadas y las métricas salen sin sentido.

CÓMO SE EJECUTA
    pip install pandas numpy
    python f10_verificar_grid_rbq.py
    python f10_verificar_grid_rbq.py --grid ../datos/rurrenabaque_grid_500m.csv

    Código de salida 0 = todo correcto · 1 = hay que detenerse.

SOBRE LA COMPROBACIÓN DE MOORE
    La prueba 5 verifica que una celda interior tenga sus 8 vecinos de Moore
    PRESENTES EN EL GRID. Eso es una propiedad de la rejilla y hay que
    exigirla: sin los ocho, el motor no podría usarlos aunque quisiera.

    El motor usa ahora esos mismos 8: la vecindad se corrigió a Moore
    (distancia de Chebyshov). Antes el filtro era euclídeo y dejaba solo 4.
    Este script comprueba las dos cosas —que el grid tenga los 8 vecinos y que
    el motor los use— y avisa si dejaran de coincidir.
============================================================================
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

# Convención del grid de Apolo, MEDIDA sobre backend_django/api/datos/grid.csv:
#     fila 11 → lat −14,00037   ·   fila 233 → lat −14,99937   → crece al SUR
#     col  26 → lon −68,99822   ·   col  247 → lon −68,00372   → crece al ESTE
PASO_ESPERADO = 0.0045
TOLERANCIA_GRADOS = 0.0008          # ~90 m: holgura para el redondeo de GEE
TAM_NOMINAL_M = 500.0
TOLERANCIA_TAM_M = 60.0

COLUMNAS_OBLIGATORIAS = [
    "id", "fila", "columna", "lat", "lon", "pendiente_grados",
    "ndvi", "humedad", "viento_u", "viento_v", "prob_ignicion",
]

# Rangos del grid de Apolo, para avisar si Rurrenabaque sale muy fuera.
RANGOS_APOLO = {
    "ndvi": (0.100, 0.444, "NDVI"),
    "humedad": (0.220, 0.400, "humedad del suelo m³/m³"),
    "pendiente_grados": (0.0, 71.3, "pendiente en grados"),
}

fallos: list[str] = []
avisos: list[str] = []


def ok(cond: bool, titulo: str, detalle: str = "", critico: bool = True) -> bool:
    if cond:
        print(f"  OK: {titulo}" + (f" — {detalle}" if detalle else ""))
        return True
    marca = "FALLO" if critico else "AVISO"
    print(f"  {marca}: {titulo}" + (f" — {detalle}" if detalle else ""))
    (fallos if critico else avisos).append(f"{titulo}: {detalle}")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--grid", default="../datos/rurrenabaque_grid_500m.csv")
    ap.add_argument("--informe", default="../resultados/f10_verificacion_grid.json")
    args = ap.parse_args()

    ruta = Path(args.grid)
    if not ruta.exists():
        print(f"ERROR: no existe {ruta}")
        print("Ejecuta antes el bloque 08 de GEE y descarga rbq_grid_500m.csv")
        print("a validacion_rurrenabaque/datos/.")
        return 1

    df = pd.read_csv(ruta)
    print("=" * 74)
    print("FASE 10 — VERIFICACIÓN DEL GRID DE RURRENABAQUE")
    print("=" * 74)
    print(f"Archivo: {ruta}")
    print(f"Celdas : {len(df):,}")

    # ---------------------------------------------------------------- 1
    print("\n1. Columnas obligatorias")
    faltan = [c for c in COLUMNAS_OBLIGATORIAS if c not in df.columns]
    ok(not faltan, "las once columnas del motor están presentes",
       "faltan: " + ", ".join(faltan) if faltan else ", ".join(COLUMNAS_OBLIGATORIAS))
    if faltan:
        print("\n  Sin estas columnas el motor no puede leer el grid. Revisa los")
        print("  `selectors` del Export.table.toDrive del bloque 08.")
        return 1

    extras = [c for c in df.columns if c not in COLUMNAS_OBLIGATORIAS]
    if extras:
        print(f"  (columnas extra, permitidas: {', '.join(extras)})")

    # ---------------------------------------------------------------- 2
    print("\n2. Duplicados y valores ausentes")
    dup = df.duplicated(subset=["fila", "columna"]).sum()
    ok(dup == 0, "no hay pares (fila, columna) duplicados",
       f"{dup} duplicados" if dup else f"{len(df):,} pares únicos")

    dup_id = df["id"].duplicated().sum()
    ok(dup_id == 0, "no hay `id` duplicados",
       f"{dup_id} duplicados" if dup_id else f"{df['id'].nunique():,} ids únicos")

    for c in ("fila", "columna", "lat", "lon"):
        n = int(df[c].isna().sum())
        ok(n == 0, f"`{c}` sin valores ausentes", f"{n} ausentes" if n else "")

    # Las variables físicas SÍ pueden tener huecos (Apolo tiene 465 celdas sin
    # pendiente, un 1,3 %). Se avisa, no se detiene.
    for c in ("pendiente_grados", "ndvi", "humedad", "viento_u", "viento_v"):
        n = int(df[c].isna().sum())
        pct = n / len(df) * 100
        ok(pct < 5, f"`{c}` con pocos huecos", f"{n} ausentes ({pct:.1f} %)",
           critico=False)

    # ---------------------------------------------------------------- 3
    print("\n3. Orientación — la comprobación que más importa")
    lat_por_fila = df.groupby("fila")["lat"].mean()
    lon_por_col = df.groupby("columna")["lon"].mean()

    f_min, f_max = lat_por_fila.index.min(), lat_por_fila.index.max()
    c_min, c_max = lon_por_col.index.min(), lon_por_col.index.max()

    print(f"    fila {f_min} → lat {lat_por_fila[f_min]:.5f}"
          f"   ·   fila {f_max} → lat {lat_por_fila[f_max]:.5f}")
    print(f"    col  {c_min} → lon {lon_por_col[c_min]:.5f}"
          f"   ·   col  {c_max} → lon {lon_por_col[c_max]:.5f}")

    fila_al_sur = lat_por_fila[f_max] < lat_por_fila[f_min]
    col_al_este = lon_por_col[c_max] > lon_por_col[c_min]

    ok(fila_al_sur, "`fila` crece hacia el SUR (igual que Apolo)",
       "la latitud BAJA cuando la fila sube" if fila_al_sur
       else "INVERTIDA: la latitud SUBE. El motor propagaría el viento y la "
            "pendiente al revés")
    ok(col_al_este, "`columna` crece hacia el ESTE (igual que Apolo)",
       "la longitud SUBE cuando la columna sube" if col_al_este
       else "INVERTIDA: la longitud BAJA")

    if not fila_al_sur:
        print("\n    Cómo se corrige: en el bloque 08 de GEE la fórmula debe ser")
        print("        fila = round((LAT_ORIGEN - lat) / PASO)")
        print("    con LAT_ORIGEN en el borde NORTE. Si está como")
        print("        fila = round((lat - LAT_ORIGEN) / PASO)")
        print("    la orientación queda invertida.")

    # ---------------------------------------------------------------- 4
    print("\n4. Las coordenadas corresponden a los índices")
    # Se reconstruyen lat/lon desde fila/columna con un ajuste por mínimos
    # cuadrados y se mide el residuo. Si el CSV es coherente, debe ser ~0.
    import numpy as np

    A = np.vstack([df["fila"].to_numpy(float), np.ones(len(df))]).T
    coef_lat, *_ = np.linalg.lstsq(A, df["lat"].to_numpy(float), rcond=None)
    B = np.vstack([df["columna"].to_numpy(float), np.ones(len(df))]).T
    coef_lon, *_ = np.linalg.lstsq(B, df["lon"].to_numpy(float), rcond=None)

    paso_lat, lat0 = -coef_lat[0], coef_lat[1]
    paso_lon, lon0 = coef_lon[0], coef_lon[1]

    res_lat = float(np.abs(df["lat"] - (coef_lat[0] * df["fila"] + lat0)).max())
    res_lon = float(np.abs(df["lon"] - (coef_lon[0] * df["columna"] + lon0)).max())

    print(f"    lat = {lat0:.6f} − fila × {paso_lat:.6f}   (residuo máx {res_lat:.6f}°)")
    print(f"    lon = {lon0:.6f} + col  × {paso_lon:.6f}   (residuo máx {res_lon:.6f}°)")

    ok(res_lat < TOLERANCIA_GRADOS, "lat es función lineal de `fila`",
       f"residuo máximo {res_lat * 111320:.0f} m")
    ok(res_lon < TOLERANCIA_GRADOS, "lon es función lineal de `columna`",
       f"residuo máximo {res_lon * 111320:.0f} m")

    # ---------------------------------------------------------------- 5
    print("\n5. Paso y tamaño nominal de celda")
    ok(abs(paso_lat - PASO_ESPERADO) < 1e-4,
       f"paso en latitud = {PASO_ESPERADO}° (el mismo que Apolo)",
       f"medido {paso_lat:.6f}°")
    ok(abs(paso_lon - PASO_ESPERADO) < 1e-4,
       f"paso en longitud = {PASO_ESPERADO}° (el mismo que Apolo)",
       f"medido {paso_lon:.6f}°")

    lat_media = float(df["lat"].mean())
    tam_ns = paso_lat * 110574.0
    tam_eo = paso_lon * 111320.0 * math.cos(math.radians(lat_media))
    print(f"    tamaño real a lat {lat_media:.3f}: {tam_ns:.0f} m N-S × {tam_eo:.0f} m E-O")
    ok(abs(tam_ns - TAM_NOMINAL_M) < TOLERANCIA_TAM_M,
       "tamaño N-S ≈ 500 m", f"{tam_ns:.0f} m")
    ok(abs(tam_eo - TAM_NOMINAL_M) < TOLERANCIA_TAM_M,
       "tamaño E-O ≈ 500 m", f"{tam_eo:.0f} m")
    print(f"    (el motor usa TAM_CELDA_NS_M = 500,0 y TAM_CELDA_EO_M = 484,0)")

    # ---------------------------------------------------------------- 6
    print("\n6. Vecindad de Moore en una celda interior")
    presentes = set(zip(df["fila"].astype(int), df["columna"].astype(int)))

    # Se busca una celda que tenga los 8 vecinos, empezando por el centro de
    # masa del grid, que es donde es más probable encontrarla.
    fc = int(round(df["fila"].mean()))
    cc = int(round(df["columna"].mean()))
    candidata = None
    for radio in range(0, 60):
        for ddf in range(-radio, radio + 1):
            for ddc in range(-radio, radio + 1):
                f, c = fc + ddf, cc + ddc
                if (f, c) not in presentes:
                    continue
                vecinos = [(f + a, c + b)
                           for a in (-1, 0, 1) for b in (-1, 0, 1)
                           if not (a == 0 and b == 0)]
                if all(v in presentes for v in vecinos):
                    candidata = (f, c)
                    break
            if candidata:
                break
        if candidata:
            break

    if candidata:
        f, c = candidata
        vecinos = [(f + a, c + b) for a in (-1, 0, 1) for b in (-1, 0, 1)
                   if not (a == 0 and b == 0)]
        n = sum(1 for v in vecinos if v in presentes)
        ok(n == 8, "Moore radio 1 = 8 vecinos",
           f"celda interior (f{f}, c{c}) tiene los 8 presentes")

        # Diagonales bien identificadas: distancia ~697 m frente a ~484-500 m.
        diag = [(f - 1, c - 1), (f - 1, c + 1), (f + 1, c - 1), (f + 1, c + 1)]
        orto = [(f - 1, c), (f + 1, c), (f, c - 1), (f, c + 1)]
        d_diag = math.hypot(tam_ns, tam_eo)
        print(f"    ortogonales: 4 vecinos a {tam_ns:.0f} m (N-S) y {tam_eo:.0f} m (E-O)")
        print(f"    diagonales : 4 vecinos a {d_diag:.0f} m")
        ok(all(v in presentes for v in diag) and all(v in presentes for v in orto),
           "las 4 diagonales y las 4 ortogonales están identificadas",
           f"diagonal = {d_diag:.0f} m = √(NS² + EO²)")
    else:
        ok(False, "Moore radio 1 = 8 vecinos",
           "no se encontró ninguna celda interior con los 8 vecinos presentes")

    # --- Qué usa realmente el motor (aviso, no fallo) ----------------------
    try:
        raiz = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(raiz))
        import os
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        os.environ.setdefault("SIPRO_OMITIR_ARRANQUE", "1")
        from api.motor.automata import CONSTANTES_POR_DEFECTO, _construir_vecindad
        vec = _construir_vecindad(CONSTANTES_POR_DEFECTO["RADIO_VECINDAD"],
                                  CONSTANTES_POR_DEFECTO["EXP_DISTANCIA"])
        ok(len(vec) == 8,
           "el MOTOR usa los 8 vecinos de Moore",
           f"usa {len(vec)}, no 8. Revisa la constante VECINDAD de "
           f"CONSTANTES_POR_DEFECTO: debe valer 'moore'",
           critico=False)
    except Exception as e:  # noqa: BLE001
        print(f"  (no se pudo inspeccionar el motor: {type(e).__name__})")

    # ---------------------------------------------------------------- 7
    print("\n7. Rangos de las variables, contra Apolo")
    for col, (lo, hi, nombre) in RANGOS_APOLO.items():
        if col not in df.columns:
            continue
        s = df[col].dropna()
        if s.empty:
            continue
        dentro = ((s >= lo * 0.5) & (s <= hi * 1.5)).mean()
        ok(dentro > 0.90, f"`{col}` en un rango comparable al de Apolo",
           f"Apolo {lo}–{hi} · aquí {s.min():.3f}–{s.max():.3f} "
           f"(mediana {s.median():.3f})", critico=False)

    # prob_ignicion debe ir a 0: XGBoost no se aplica en Rurrenabaque
    if "prob_ignicion" in df.columns:
        todos_cero = bool((df["prob_ignicion"].fillna(0) == 0).all())
        ok(todos_cero, "`prob_ignicion` = 0 en todas las celdas",
           "valor neutro y documentado: XGBoost no se valida aquí"
           if todos_cero else
           "hay valores distintos de 0. XGBoost se entrenó con Apolo y "
           "aplicarlo aquí sería extrapolarlo fuera de su dominio")

    # Viento: avisar si supera el umbral de spotting
    if {"viento_u", "viento_v"}.issubset(df.columns):
        vel = (df["viento_u"] ** 2 + df["viento_v"] ** 2) ** 0.5
        vmax = float(vel.max())
        print(f"\n    viento: mediana {vel.median():.3f} · máximo {vmax:.3f} m/s")
        print("    (Apolo: mediana 0,390 · máximo 0,523 m/s)")
        if vmax >= 1.0:
            avisos.append(
                f"El viento llega a {vmax:.2f} m/s y supera SPOTTING_VIENTO_MIN "
                f"= 1,0. El spotting SE VA A DISPARAR, y el motor tiene el signo "
                f"norte-sur del salto invertido.")
            print("    AVISO: supera SPOTTING_VIENTO_MIN = 1,0 m/s.")
            print("    El spotting se disparará. El motor invierte el signo N-S")
            print("    del salto (ver parametros_ca_congelados.json). f11 lo")
            print("    cuenta y avisa; tenlo delante al interpretar.")

    # ---------------------------------------------------------------- fin
    print("\n" + "=" * 74)
    informe = {
        "archivo": str(ruta),
        "celdas": int(len(df)),
        "paso_lat": round(paso_lat, 6),
        "paso_lon": round(paso_lon, 6),
        "lat_origen": round(lat0, 6),
        "lon_origen": round(lon0, 6),
        "fila_crece_al_sur": bool(fila_al_sur),
        "columna_crece_al_este": bool(col_al_este),
        "tam_celda_ns_m": round(tam_ns, 1),
        "tam_celda_eo_m": round(tam_eo, 1),
        "fallos": fallos,
        "avisos": avisos,
    }
    salida = Path(args.informe)
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(informe, indent=2, ensure_ascii=False),
                      encoding="utf-8")

    if fallos:
        print(f"  PROCESO DETENIDO — {len(fallos)} fallo(s):")
        for f_ in fallos:
            print(f"    · {f_}")
        print("\n  Corrige el grid antes de ejecutar el autómata. Gastar 30")
        print("  repeticiones sobre un grid mal orientado no produce una")
        print("  validación, produce ruido con aspecto de resultado.")
        print("=" * 74)
        return 1

    print("  OK: estructura del grid válida")
    print("  OK: orientación compatible con Apolo")
    print("  OK: Moore radio 1 = 8 vecinos")
    if avisos:
        print(f"\n  Con {len(avisos)} aviso(s) que NO detienen el proceso:")
        for a in avisos:
            print(f"    · {a}")
    print(f"\n  Informe: {salida}")
    print("  Siguiente: python f11_ejecutar_ca_rbq.py")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
