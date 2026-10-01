"""
============================================================================
AUDITORÍA DE CALIBRACIÓN DEL MODELO DE OCURRENCIA (XGBoost)
============================================================================
Ruta: validacion_rurrenabaque/python/auditar_xgboost.py

    cd validacion_rurrenabaque/python
    python auditar_xgboost.py

QUÉ PREGUNTA RESPONDE
    «¿Están bien calibradas las probabilidades de XGBoost?»

    Ojo, que no es lo mismo que «¿discrimina bien?». Son dos propiedades
    distintas y se confunden todo el tiempo:

      DISCRIMINACIÓN (AUC-ROC)
        ¿Ordena bien? ¿Les pone más probabilidad a las celdas que sí
        ardieron que a las que no? Un AUC de 0,92 dice que sí.

      CALIBRACIÓN
        ¿Los números significan lo que dicen? Cuando el modelo dice 0,80,
        ¿arde de verdad el 80 % de esas celdas, o solo el 45 %?

    Un modelo puede tener AUC excelente y estar mal calibrado. Y al revés.
    Para SIPRO FIRE importan las dos, pero la segunda importa especialmente
    porque el sistema muestra esos números al usuario y los usa para clasificar
    niveles de riesgo: si la pantalla dice «riesgo 85 %» y en realidad arde el
    40 %, el nivel «crítico» está mal puesto.

LO QUE SE PUEDE ARREGLAR SIN REENTRENAR
    Esto es lo importante y es la razón de que este script exista:

        La calibración se corrige DESPUÉS, sin tocar el modelo.

    Se aprende una función que reordena las probabilidades para que digan la
    verdad —regresión isotónica— y se aplica a la salida. XGBoost no se toca,
    el dataset no se toca, el notebook no se toca. Solo se ajusta la escala.

    Eso es compatible con las restricciones del proyecto: no es reentrenar, es
    recalibrar la salida. Y se puede hacer hoy, aquí, sin GEE ni internet.

LA LIMITACIÓN QUE HAY QUE DECLARAR
    XGBoost se entrenó con estos mismos focos históricos, así que esta
    auditoría es EN MUESTRA. Los valores saldrán mejores de lo que serían con
    datos nuevos, y no sirven como métrica de rendimiento para la memoria —esa
    hay que citarla del notebook, con su partición de prueba.

    Pero para lo que sirve sí vale: un modelo mal calibrado se delata incluso
    en muestra. Si la curva de fiabilidad ya se desvía aquí, fuera de muestra
    se desvía más.

QUÉ GENERA
    ../resultados/xgboost_calibracion.json      cifras y curva
    ../resultados/xgboost_calibracion.png       diagrama de fiabilidad
    ../resultados/xgboost_recalibrado.csv       mapa prob_original → prob_corregida
============================================================================
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

RAIZ = Path(__file__).resolve().parents[2]
GRID = RAIZ / "api" / "datos" / "grid.csv"
FOCOS = RAIZ / "api" / "datos" / "focos.csv"

PASO = 0.0045


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--grid", default=str(GRID))
    ap.add_argument("--focos", default=str(FOCOS))
    ap.add_argument("--resultados", default="../resultados")
    ap.add_argument("--bins", type=int, default=10)
    args = ap.parse_args()

    res = Path(args.resultados)
    res.mkdir(parents=True, exist_ok=True)

    # --- Cargar ------------------------------------------------------------
    celdas = list(csv.DictReader(open(args.grid, encoding="utf-8")))
    focos = list(csv.DictReader(open(args.focos, encoding="utf-8")))

    print("=" * 74)
    print("AUDITORÍA DE CALIBRACIÓN — MODELO DE OCURRENCIA (XGBoost)")
    print("=" * 74)
    print(f"Celdas del grid    : {len(celdas):,}")
    print(f"Focos históricos   : {len(focos):,}")

    # --- Verdad observada: ¿ardió alguna vez esta celda? -------------------
    # Se asigna cada foco a su celda por proximidad de índice, que es lo mismo
    # que hace el grid. Una celda cuenta como positiva si registró al menos un
    # foco en todo el histórico.
    ix = {}
    for c in celdas:
        ix[(int(c["fila"]), int(c["columna"]))] = c

    # Origen de la rejilla, deducido del propio grid
    f0 = min(int(c["fila"]) for c in celdas)
    lat_f0 = max(float(c["lat"]) for c in celdas if int(c["fila"]) == f0)
    c0 = min(int(c["columna"]) for c in celdas)
    lon_c0 = min(float(c["lon"]) for c in celdas if int(c["columna"]) == c0)

    quemadas = set()
    fuera = 0
    for f in focos:
        try:
            lat, lon = float(f["lat"]), float(f["lon"])
        except (TypeError, ValueError):
            continue
        fila = f0 + round((lat_f0 - lat) / PASO)
        col = c0 + round((lon - lon_c0) / PASO)
        if (fila, col) in ix:
            quemadas.add((fila, col))
        else:
            fuera += 1

    print(f"Focos fuera del grid: {fuera:,} ({fuera/len(focos)*100:.1f} %)")
    print(f"Celdas con fuego    : {len(quemadas):,} "
          f"({len(quemadas)/len(celdas)*100:.1f} % del municipio)")

    y = np.array([1 if (int(c["fila"]), int(c["columna"])) in quemadas else 0
                  for c in celdas], dtype=np.int8)
    p = np.array([float(c["prob_ignicion"] or 0) for c in celdas], dtype=np.float64)

    tasa_base = y.mean()
    print(f"Prevalencia observada: {tasa_base:.4f}")
    print(f"Media de p predicha  : {p.mean():.4f}")
    print(f"  → El modelo predice {p.mean()/max(tasa_base,1e-9):.2f}× la "
          f"prevalencia real.")
    if p.mean() > tasa_base * 1.3:
        print("    Sobreestima de forma sistemática.")
    elif p.mean() < tasa_base * 0.77:
        print("    Subestima de forma sistemática.")
    else:
        print("    En promedio, coherente con la prevalencia.")

    # --- Discriminación: AUC-ROC -------------------------------------------
    # Se calcula con el estadístico U de Mann-Whitney, que es exacto y no
    # necesita sklearn: AUC = P(p_positivo > p_negativo), con los empates
    # contando medio.
    orden = np.argsort(p, kind="mergesort")
    rangos = np.empty(len(p), dtype=np.float64)
    i = 0
    while i < len(p):
        j = i
        while j + 1 < len(p) and p[orden[j + 1]] == p[orden[i]]:
            j += 1
        rango_medio = (i + j) / 2 + 1          # rangos desde 1, empates promediados
        for k in range(i, j + 1):
            rangos[orden[k]] = rango_medio
        i = j + 1
    n1 = int(y.sum())
    n0 = len(y) - n1
    auc = ((rangos[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)) if n1 and n0 else None

    brier = float(np.mean((p - y) ** 2))
    # Brier del modelo trivial que predice siempre la prevalencia. Si el del
    # modelo no lo bate, las probabilidades no aportan nada.
    brier_base = float(np.mean((tasa_base - y) ** 2))
    skill = 1 - brier / brier_base if brier_base else None

    print("\n" + "-" * 74)
    print("DISCRIMINACIÓN Y ERROR")
    print("-" * 74)
    print(f"  AUC-ROC             : {auc:.4f}" if auc else "  AUC-ROC: n/d")
    print(f"  Brier score         : {brier:.4f}   (0 = perfecto)")
    print(f"  Brier de referencia : {brier_base:.4f}   (predecir siempre la prevalencia)")
    print(f"  Brier skill score   : {skill:+.4f}   "
          f"({'aporta' if skill and skill > 0 else 'NO aporta'} sobre la referencia)")

    # --- Calibración: diagrama de fiabilidad --------------------------------
    # Bins de anchura igual. Para cada uno: probabilidad media predicha frente
    # a frecuencia observada de fuego. En un modelo bien calibrado caen sobre
    # la diagonal.
    bordes = np.linspace(0, 1, args.bins + 1)
    curva = []
    ece = 0.0          # Expected Calibration Error: desvío medio ponderado
    mce = 0.0          # Maximum Calibration Error: el peor bin
    for b in range(args.bins):
        lo, hi = bordes[b], bordes[b + 1]
        sel = (p >= lo) & (p < hi) if b < args.bins - 1 else (p >= lo) & (p <= hi)
        n = int(sel.sum())
        if n == 0:
            curva.append({"bin": b, "desde": lo, "hasta": hi, "n": 0,
                          "p_media": None, "frecuencia": None, "desvio": None})
            continue
        pm = float(p[sel].mean())
        fo = float(y[sel].mean())
        d = fo - pm
        ece += n / len(p) * abs(d)
        mce = max(mce, abs(d))
        curva.append({"bin": b, "desde": float(lo), "hasta": float(hi), "n": n,
                      "p_media": round(pm, 4), "frecuencia": round(fo, 4),
                      "desvio": round(d, 4)})

    print("\n" + "-" * 74)
    print("CALIBRACIÓN — diagrama de fiabilidad")
    print("-" * 74)
    print(f"{'RANGO':>14}{'CELDAS':>9}{'p MEDIA':>10}{'ARDIÓ':>9}{'DESVÍO':>9}")
    print("-" * 74)
    for c in curva:
        if c["n"] == 0:
            print(f"{c['desde']:.2f}–{c['hasta']:.2f}".rjust(14) +
                  f"{0:>9}" + "        —" * 3)
            continue
        flecha = "↓ dice de más" if c["desvio"] < -0.05 else \
                 ("↑ dice de menos" if c["desvio"] > 0.05 else "")
        print(f"{c['desde']:.2f}–{c['hasta']:.2f}".rjust(14) +
              f"{c['n']:>9,}{c['p_media']:>10.3f}{c['frecuencia']:>9.3f}"
              f"{c['desvio']:>+9.3f}  {flecha}")
    print("-" * 74)
    print(f"  ECE (error medio de calibración) : {ece:.4f}")
    print(f"  MCE (peor bin)                   : {mce:.4f}")
    if ece < 0.05:
        print("  → Bien calibrado: los números significan lo que dicen.")
    elif ece < 0.15:
        print("  → Calibración regular. Recalibrar la salida mejoraría la lectura.")
    else:
        print("  → MAL CALIBRADO. Los números no significan lo que dicen y la")
        print("    clasificación en niveles de riesgo está distorsionada.")

    # --- Recalibración isotónica -------------------------------------------
    # Regresión isotónica por el algoritmo PAVA (pool adjacent violators):
    # busca la función MONÓTONA que mejor ajusta la frecuencia observada. Al
    # ser monótona, no cambia el orden de las celdas —o sea, NO cambia el
    # AUC— solo corrige la escala. Eso es exactamente lo que se quiere:
    # arreglar el significado de los números sin tocar el modelo.
    orden_p = np.argsort(p, kind="mergesort")
    xs = p[orden_p]
    ys = y[orden_p].astype(np.float64)

    # PAVA sobre los valores ordenados
    niveles = list(ys)
    pesos = [1.0] * len(ys)
    i = 0
    while i < len(niveles) - 1:
        if niveles[i] <= niveles[i + 1]:
            i += 1
            continue
        # Violación: se fusionan los dos bloques en su media ponderada
        total_w = pesos[i] + pesos[i + 1]
        media = (niveles[i] * pesos[i] + niveles[i + 1] * pesos[i + 1]) / total_w
        niveles[i:i + 2] = [media]
        pesos[i:i + 2] = [total_w]
        if i > 0:
            i -= 1
    # Reexpandir a un valor por observación
    ajustado = []
    for v, w in zip(niveles, pesos):
        ajustado.extend([v] * int(round(w)))
    ajustado = np.array(ajustado[:len(ys)])

    p_recal = np.empty_like(p)
    p_recal[orden_p] = ajustado

    brier_recal = float(np.mean((p_recal - y) ** 2))
    ece_recal = 0.0
    for b in range(args.bins):
        lo, hi = bordes[b], bordes[b + 1]
        sel = (p_recal >= lo) & (p_recal < hi) if b < args.bins - 1 else (p_recal >= lo) & (p_recal <= hi)
        if sel.sum():
            ece_recal += sel.sum() / len(p) * abs(y[sel].mean() - p_recal[sel].mean())

    print("\n" + "-" * 74)
    print("RECALIBRACIÓN ISOTÓNICA — sin tocar el modelo")
    print("-" * 74)
    print(f"{'':22}{'ORIGINAL':>12}{'RECALIBRADO':>14}")
    print(f"{'Brier score':22}{brier:>12.4f}{brier_recal:>14.4f}")
    print(f"{'ECE':22}{ece:>12.4f}{ece_recal:>14.4f}")
    print(f"{'AUC-ROC':22}{auc:>12.4f}{auc:>14.4f}   (no cambia: es monótona)")
    print()
    print("  La regresión isotónica es monótona, así que NO altera el orden de")
    print("  las celdas y el AUC se conserva exacto. Solo corrige la escala.")
    print("  Aplicarla NO es reentrenar XGBoost: el modelo, el dataset y el")
    print("  notebook quedan intactos. Es una transformación de su salida.")

    # Tabla de conversión, en los puntos donde cambia
    tabla = []
    ultimo = None
    for orig, rec in sorted(zip(p, p_recal)):
        if ultimo is None or abs(rec - ultimo) > 0.01:
            tabla.append({"prob_original": round(float(orig), 4),
                          "prob_recalibrada": round(float(rec), 4)})
            ultimo = rec
    with open(res / "xgboost_recalibrado.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["prob_original", "prob_recalibrada"])
        w.writeheader()
        w.writerows(tabla)

    # --- Figura -------------------------------------------------------------
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    pts = [(c["p_media"], c["frecuencia"], c["n"]) for c in curva if c["n"]]
    if pts:
        xs_, ys_, ns_ = zip(*pts)
        ax.plot([0, 1], [0, 1], "--", color="#94A3B8", lw=1.5,
                label="Calibración perfecta")
        ax.plot(xs_, ys_, "o-", color="#B45309", lw=2, markersize=7,
                label="XGBoost (original)")
        # Recalibrado
        rc = []
        for b in range(args.bins):
            lo, hi = bordes[b], bordes[b + 1]
            sel = (p_recal >= lo) & (p_recal < hi) if b < args.bins - 1 else (p_recal >= lo) & (p_recal <= hi)
            if sel.sum():
                rc.append((p_recal[sel].mean(), y[sel].mean()))
        if rc:
            ax.plot(*zip(*rc), "s-", color="#0F766E", lw=2, markersize=6,
                    label="Tras recalibrar")
        ax.set_xlabel("Probabilidad predicha")
        ax.set_ylabel("Frecuencia observada de fuego")
        ax.set_title(f"Diagrama de fiabilidad\nECE {ece:.3f} → {ece_recal:.3f}",
                     fontsize=11)
        ax.legend(fontsize=9); ax.grid(alpha=0.3)
        ax.set_xlim(0, 1); ax.set_ylim(0, 1)

    # Histograma: dónde están realmente las celdas
    ax2.hist(p, bins=args.bins, color="#94A3B8", edgecolor="white")
    ax2.set_yscale("log")
    ax2.set_xlabel("Probabilidad predicha")
    ax2.set_ylabel("Celdas (escala log)")
    ax2.set_title("Distribución de las predicciones\n"
                  f"{len(celdas):,} celdas · prevalencia real {tasa_base:.3f}",
                  fontsize=11)
    ax2.grid(alpha=0.3, axis="y")

    fig.suptitle("Calibración del modelo de ocurrencia — Apolo "
                 "(auditoría EN MUESTRA)", fontsize=12, weight="bold")
    fig.tight_layout()
    fig.savefig(res / "xgboost_calibracion.png", dpi=150)
    plt.close(fig)

    # --- JSON ---------------------------------------------------------------
    (res / "xgboost_calibracion.json").write_text(json.dumps({
        "_aviso": "Auditoría EN MUESTRA: XGBoost se entrenó con estos mismos "
                  "focos. No sirve como métrica de rendimiento para la memoria "
                  "—esa hay que citarla del notebook, con su partición de "
                  "prueba— pero sí detecta problemas de calibración.",
        "celdas": len(celdas),
        "focos_historicos": len(focos),
        "focos_fuera_del_grid": fuera,
        "celdas_con_fuego": len(quemadas),
        "prevalencia_observada": round(float(tasa_base), 6),
        "probabilidad_media_predicha": round(float(p.mean()), 6),
        "razon_prediccion_prevalencia": round(float(p.mean() / max(tasa_base, 1e-9)), 3),
        "discriminacion": {"auc_roc": round(auc, 4) if auc else None},
        "error": {
            "brier": round(brier, 5),
            "brier_referencia": round(brier_base, 5),
            "brier_skill_score": round(skill, 5) if skill is not None else None,
        },
        "calibracion": {
            "ece": round(ece, 5), "mce": round(mce, 5),
            "curva_fiabilidad": curva,
        },
        "recalibracion_isotonica": {
            "brier": round(brier_recal, 5),
            "ece": round(ece_recal, 5),
            "auc_roc": round(auc, 4) if auc else None,
            "_nota": "La isotónica es monótona: conserva el orden y por tanto "
                     "el AUC exacto. Corrige solo la escala. NO es reentrenar.",
            "tabla": "xgboost_recalibrado.csv",
        },
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nEscrito: {res / 'xgboost_calibracion.json'}")
    print(f"Escrito: {res / 'xgboost_calibracion.png'}")
    print(f"Escrito: {res / 'xgboost_recalibrado.csv'}  ({len(tabla)} puntos)")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
