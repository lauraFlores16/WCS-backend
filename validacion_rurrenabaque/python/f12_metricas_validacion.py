"""
============================================================================
FASES 12, 13 y 14 — COMPARACIÓN, MÉTRICAS Y FIGURAS
============================================================================
Ruta: validacion_rurrenabaque/python/f12_metricas_validacion.py

QUÉ HACE
    Cruza celda a celda la máscara OBSERVADA (dNBR llevado al grid de 500 m,
    bloque 07 de GEE) con la SIMULADA (probabilidad de quema de las N
    repeticiones, f11), calcula la matriz de confusión espacial y todas las
    métricas, y genera las tablas y figuras de la memoria.

QUÉ CAMBIÓ RESPECTO DE LA VERSIÓN ANTERIOR (ver f12_metricas_validacion.py.bak)

  1. CRUCE POR `id`, NO POR RÁSTER. Antes leía dos GeoTIFF y los alineaba por
     posición de píxel. Ahora lee dos CSV y los une por `id` (o por fila+columna
     si falta). Es más robusto: elimina de raíz cualquier desplazamiento de
     medio píxel entre observado y simulado, y hace imposible comparar celdas
     que no se correspondan. Si un `id` está en un archivo y no en el otro, se
     reporta en vez de emparejarse en silencio.

  2. NoData EXCLUIDO DE VERDAD. Antes el ráster observado traía `unmask(0)` y
     las nubes se contaban como «no quemado». Ahora el bloque 07 exporta una
     banda `valido` y aquí SOLO se evalúan las celdas con valido = 1. Las de
     valido = 0 no son TP, ni FP, ni FN, ni TN: se excluyen. Sin esto, una
     celda tapada por una nube sobre la que el modelo propagaba se contaba
     como falso positivo, y el modelo pagaba por una nube.

  3. MÉTRICAS DIRECCIONALES. Se añaden el error angular de propagación y la
     distancia entre centroides. Responden a algo que IoU y F1 no distinguen:
     si el modelo falla porque simula DE MÁS o porque simula HACIA OTRO LADO.
     Un IoU bajo con error angular pequeño significa que la dirección está
     bien y sobra extensión; un IoU bajo con error angular grande significa
     que el frente se fue por donde no era. Son dos diagnósticos distintos y
     llevan a correcciones distintas.

  4. FIGURAS. Se generan las de las fases 14-15 en vez de una sola.

QUÉ NO HACE
    No ajusta nada. Si las métricas salen malas, salen malas. El único umbral
    que expone es el de la simulación, y viene fijado desde f11.

ENTRADAS
    ../datos/observado_grid.csv         del bloque 07 de GEE
    ../resultados/ca_resultado_final.csv de f11

SALIDAS
    ../resultados/comparacion_espacial.csv
    ../resultados/metricas_validacion.json
    ../resultados/tabla_validacion.csv
    ../resultados/fig_05_probabilidad_quema.png
    ../resultados/fig_06_area_simulada.png
    ../resultados/fig_07_superposicion.png
    ../resultados/fig_08_evolucion.png
    ../resultados/resumen_validacion.md

CÓMO SE EJECUTA
    pip install pandas numpy matplotlib
    python f12_metricas_validacion.py
============================================================================
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch

AREA_CELDA_KM2 = 0.25


# ---------------------------------------------------------------------------
# Carga y armonización
# ---------------------------------------------------------------------------
def cruzar(obs: pd.DataFrame, sim: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Une observado y simulado. Falla ruidosamente si no se corresponden."""
    clave = "id" if ("id" in obs.columns and "id" in sim.columns) else None
    if clave is None:
        if not {"fila", "columna"}.issubset(obs.columns & sim.columns):
            raise SystemExit(
                "ERROR: los dos archivos necesitan `id`, o bien `fila` y "
                "`columna`. Sin una clave común no se pueden cruzar, y "
                "emparejarlos por orden de fila sería inventarse la "
                "correspondencia."
            )
        clave = ["fila", "columna"]

    o = obs.copy()
    s = sim.copy()
    juntos = o.merge(s, on=clave, how="outer", suffixes=("_obs", "_sim"),
                     indicator=True)

    diag = {
        "clave": clave if isinstance(clave, str) else "+".join(clave),
        "celdas_observado": int(len(o)),
        "celdas_simulado": int(len(s)),
        "solo_en_observado": int((juntos["_merge"] == "left_only").sum()),
        "solo_en_simulado": int((juntos["_merge"] == "right_only").sum()),
        "en_ambos": int((juntos["_merge"] == "both").sum()),
    }

    if diag["solo_en_observado"] or diag["solo_en_simulado"]:
        print("  AVISO: los dos grids no coinciden del todo.")
        print(f"    solo en observado: {diag['solo_en_observado']:,}")
        print(f"    solo en simulado : {diag['solo_en_simulado']:,}")
        print("    Solo se evalúan las celdas presentes en LOS DOS. Si la cifra")
        print("    es grande, revisa que ambos salieran del mismo LAT_ORIGEN /")
        print("    LON_ORIGEN (bloques 07 y 08 de GEE).")

    juntos = juntos[juntos["_merge"] == "both"].drop(columns="_merge")

    # Coordenadas: pueden venir de cualquiera de los dos lados.
    for c in ("lat", "lon", "fila", "columna"):
        if c not in juntos.columns:
            for suf in ("_obs", "_sim"):
                if c + suf in juntos.columns:
                    juntos[c] = juntos[c + suf]
                    break
    return juntos, diag


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------
def matriz_y_metricas(d: pd.DataFrame) -> dict:
    o = d["observado"].astype(bool).to_numpy()
    s = d["simulado"].astype(bool).to_numpy()

    TP = int(np.sum(o & s))
    FP = int(np.sum(~o & s))
    FN = int(np.sum(o & ~s))
    TN = int(np.sum(~o & ~s))
    n = TP + FP + FN + TN

    def div(a, b):
        return float(a) / float(b) if b else None

    precision = div(TP, TP + FP)
    recall = div(TP, TP + FN)
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall and (precision + recall) else None)
    iou = div(TP, TP + FP + FN)
    accuracy = div(TP + TN, n)

    area_obs = (TP + FN) * AREA_CELDA_KM2
    area_sim = (TP + FP) * AREA_CELDA_KM2
    dif = area_sim - area_obs
    err = abs(dif) / area_obs * 100 if area_obs else None

    comportamiento = ("SOBREESTIMACIÓN" if dif > 0 else
                      "SUBESTIMACIÓN" if dif < 0 else "COINCIDENCIA EXACTA")

    return {
        "tp": TP, "fp": FP, "fn": FN, "tn": TN,
        "celdas_evaluadas": n,
        "iou": round(iou, 4) if iou is not None else None,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "accuracy": round(accuracy, 4) if accuracy is not None else None,
        "area_observada_km2": round(area_obs, 3),
        "area_simulada_km2": round(area_sim, 3),
        "diferencia_area_km2": round(dif, 3),
        "error_area_pct": round(err, 2) if err is not None else None,
        "comportamiento": comportamiento,
    }


def metricas_direccionales(d: pd.DataFrame, focos: pd.DataFrame | None) -> dict:
    """Error angular y distancia entre centroides.

    El ángulo se mide como acimut geográfico: 0° = norte, 90° = este. Se
    calcula en METROS, no en grados de latitud/longitud, porque a esta latitud
    un grado de longitud mide un 3 % menos que uno de latitud y el ángulo
    saldría sesgado.

    La circularidad se corrige llevando la diferencia al rango [-180, 180]: sin
    eso, 350° y 10° darían un error de 340° cuando en realidad son 20°.
    """
    obs = d[d["observado"] == 1]
    sim = d[d["simulado"] == 1]
    if obs.empty or sim.empty:
        return {"error_angular_grados": None, "distancia_centroides_km": None,
                "_nota": "no hay superficie observada o simulada suficiente"}

    lat_ref = float(d["lat"].mean())
    m_lat = 110574.0
    m_lon = 111320.0 * math.cos(math.radians(lat_ref))

    cen_obs = (float(obs["lat"].mean()), float(obs["lon"].mean()))
    cen_sim = (float(sim["lat"].mean()), float(sim["lon"].mean()))

    dy = (cen_sim[0] - cen_obs[0]) * m_lat
    dx = (cen_sim[1] - cen_obs[1]) * m_lon
    dist_km = math.hypot(dx, dy) / 1000

    salida = {"distancia_centroides_km": round(dist_km, 3),
              "centroide_observado": {"lat": round(cen_obs[0], 6),
                                      "lon": round(cen_obs[1], 6)},
              "centroide_simulado": {"lat": round(cen_sim[0], 6),
                                     "lon": round(cen_sim[1], 6)}}

    if focos is None or focos.empty:
        salida["error_angular_grados"] = None
        salida["_nota_angular"] = ("sin focos iniciales no hay origen desde el "
                                   "que medir la dirección de propagación")
        return salida

    origen = (float(focos["lat"].mean()), float(focos["lon"].mean()))

    def acimut(desde, hasta):
        dy_ = (hasta[0] - desde[0]) * m_lat
        dx_ = (hasta[1] - desde[1]) * m_lon
        return (math.degrees(math.atan2(dx_, dy_)) + 360) % 360

    a_obs = acimut(origen, cen_obs)
    a_sim = acimut(origen, cen_sim)
    err = (a_sim - a_obs + 180) % 360 - 180      # corrección de circularidad

    salida.update({
        "origen_focos": {"lat": round(origen[0], 6), "lon": round(origen[1], 6)},
        "angulo_observado_grados": round(a_obs, 2),
        "angulo_simulado_grados": round(a_sim, 2),
        "error_angular_grados": round(abs(err), 2),
        "error_angular_con_signo": round(err, 2),
        "_lectura_signo": "positivo = el simulado se desvía en sentido horario",
    })
    return salida


# ---------------------------------------------------------------------------
# Figuras
# ---------------------------------------------------------------------------
def _rejilla(d: pd.DataFrame, campo: str):
    f0, f1 = int(d["fila"].min()), int(d["fila"].max())
    c0, c1 = int(d["columna"].min()), int(d["columna"].max())
    m = np.full((f1 - f0 + 1, c1 - c0 + 1), np.nan)
    m[d["fila"].astype(int) - f0, d["columna"].astype(int) - c0] = d[campo].to_numpy()
    return m


def figuras(d: pd.DataFrame, met: dict, dirn: dict, focos, res: Path,
            evento: str, evolucion: pd.DataFrame | None):
    # --- Fig 5: probabilidad de quema --------------------------------------
    if "prob_quemada" in d.columns:
        fig, ax = plt.subplots(figsize=(7, 7))
        im = ax.imshow(_rejilla(d, "prob_quemada"), cmap="inferno",
                       vmin=0, vmax=1, interpolation="nearest")
        plt.colorbar(im, ax=ax, label="P(quema) sobre las repeticiones")
        ax.set_title("Figura 5 · Probabilidad de quema del autómata\n"
                     f"Rurrenabaque · {evento}", fontsize=10)
        ax.set_xticks([]); ax.set_yticks([])
        fig.tight_layout(); fig.savefig(res / "fig_05_probabilidad_quema.png", dpi=150)
        plt.close(fig)

    # --- Fig 6: área simulada ----------------------------------------------
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.imshow(_rejilla(d, "simulado"), cmap="Oranges", vmin=0, vmax=1,
              interpolation="nearest")
    ax.set_title(f"Figura 6 · Área simulada\n"
                 f"{met['area_simulada_km2']:.2f} km²", fontsize=10)
    ax.set_xticks([]); ax.set_yticks([])
    fig.tight_layout(); fig.savefig(res / "fig_06_area_simulada.png", dpi=150)
    plt.close(fig)

    # --- Fig 7: superposición ----------------------------------------------
    clase = np.zeros(len(d))
    clase[(d["simulado"] == 1) & (d["observado"] == 0)] = 1   # FP
    clase[(d["simulado"] == 0) & (d["observado"] == 1)] = 2   # FN
    clase[(d["simulado"] == 1) & (d["observado"] == 1)] = 3   # TP
    d = d.assign(_clase=clase)

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(13, 6.5),
                                  gridspec_kw={"width_ratios": [1.25, 1]})
    cmap = ListedColormap(["#F1F5F9", "#F59E0B", "#2563EB", "#16A34A"])
    ax.imshow(_rejilla(d, "_clase"), cmap=cmap, vmin=0, vmax=3,
              interpolation="nearest")
    if focos is not None and not focos.empty:
        f0, c0 = int(d["fila"].min()), int(d["columna"].min())
        ax.scatter(focos["columna"].astype(int) - c0,
                   focos["fila"].astype(int) - f0,
                   s=90, facecolors="none", edgecolors="#0F766E", linewidths=2,
                   label="Focos iniciales")
    ax.legend(handles=[
        Patch(color="#16A34A", label=f"TP · coincide ({met['tp']:,})"),
        Patch(color="#F59E0B", label=f"FP · solo simulado ({met['fp']:,})"),
        Patch(color="#2563EB", label=f"FN · solo observado ({met['fn']:,})"),
        Patch(color="#F1F5F9", label=f"TN ({met['tn']:,})"),
    ], loc="lower left", fontsize=8, framealpha=0.92)
    ax.set_title("Figura 7 · Observado vs simulado", fontsize=11, weight="bold")
    ax.set_xticks([]); ax.set_yticks([])

    ax2.axis("off")
    fmt = lambda v: "—" if v is None else f"{v:.3f}"
    ea = dirn.get("error_angular_grados")
    dc = dirn.get("distancia_centroides_km")
    ax2.text(0.0, 0.98, "MÉTRICAS PRINCIPALES", fontsize=10, weight="bold",
             va="top", family="monospace")
    ax2.text(0.0, 0.90,
             f"IoU        {fmt(met['iou'])}\n"
             f"Precision  {fmt(met['precision'])}\n"
             f"Recall     {fmt(met['recall'])}\n"
             f"F1-score   {fmt(met['f1'])}\n\n"
             f"Área observada  {met['area_observada_km2']:>9.2f} km²\n"
             f"Área simulada   {met['area_simulada_km2']:>9.2f} km²\n"
             f"Diferencia      {met['diferencia_area_km2']:>+9.2f} km²\n"
             f"Error de área   {met['error_area_pct']:>9.2f} %\n"
             f"Comportamiento  {met['comportamiento']}\n\n"
             f"COMPLEMENTARIAS\n"
             f"Error angular      {'—' if ea is None else f'{ea:.1f}°'}\n"
             f"Dist. centroides   {'—' if dc is None else f'{dc:.2f} km'}\n"
             f"Accuracy           {fmt(met['accuracy'])}\n\n"
             f"Celdas evaluadas   {met['celdas_evaluadas']:,}\n"
             f"(de ellas TN       {met['tn']:,} = "
             f"{met['tn'] / max(met['celdas_evaluadas'], 1) * 100:.1f} %)",
             fontsize=9, va="top", family="monospace", linespacing=1.55)
    fig.suptitle(f"VALIDACIÓN EXTERNA DEL AUTÓMATA CELULAR — Rurrenabaque · {evento}",
                 fontsize=12, weight="bold")
    fig.tight_layout()
    fig.savefig(res / "fig_07_superposicion.png", dpi=160, bbox_inches="tight")
    plt.close(fig)

    # --- Fig 8: evolución ---------------------------------------------------
    if evolucion is not None and not evolucion.empty:
        fig, ax = plt.subplots(figsize=(9, 4))
        ax.plot(evolucion["horas"], evolucion["area_ca_km2"],
                color="#B45309", lw=2, label="Área acumulada del CA")
        ax.axhline(met["area_observada_km2"], color="#2563EB", ls="--", lw=1.5,
                   label=f"Área observada (dNBR) {met['area_observada_km2']:.1f} km²")
        ax.set_xlabel("Horas simuladas"); ax.set_ylabel("km²")
        ax.set_title("Figura 8 · Evolución del área simulada frente a la observada\n"
                     "La observada es un valor final único: el dNBR no da serie temporal",
                     fontsize=10)
        ax.legend(fontsize=8); ax.grid(alpha=0.3)
        fig.tight_layout(); fig.savefig(res / "fig_08_evolucion.png", dpi=150)
        plt.close(fig)


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--observado", default="../datos/observado_grid.csv")
    ap.add_argument("--simulado", default="../resultados/ca_resultado_final.csv")
    ap.add_argument("--focos", default="../resultados/focos_iniciales.csv")
    ap.add_argument("--evento", default="../resultados/evento_validacion.json")
    ap.add_argument("--ejecucion", default="../resultados/f11_resumen_ejecucion.json")
    ap.add_argument("--evolucion", default="../resultados/ca_evolucion_temporal.csv")
    ap.add_argument("--resultados", default="../resultados")
    args = ap.parse_args()

    res = Path(args.resultados)
    res.mkdir(parents=True, exist_ok=True)

    for p, quien in ((args.observado, "el bloque 07 de GEE"),
                     (args.simulado, "f11_ejecutar_ca_rbq.py")):
        if not Path(p).exists():
            print(f"ERROR: no existe {p}. Ejecuta antes {quien}.")
            return 1

    obs = pd.read_csv(args.observado)
    sim = pd.read_csv(args.simulado)

    print("=" * 74)
    print("FASES 12-14 — COMPARACIÓN, MÉTRICAS Y FIGURAS")
    print("=" * 74)

    # --- Armonización -------------------------------------------------------
    print("\n1. Armonización observado / simulado")
    d, diag = cruzar(obs, sim)
    print(f"  clave de cruce      : {diag['clave']}")
    print(f"  celdas en ambos     : {diag['en_ambos']:,}")

    if "valido" not in d.columns:
        print("\n  ERROR: el archivo observado no trae la columna `valido`.")
        print("  Estás usando una exportación antigua del bloque 07. Sin esa")
        print("  columna, las nubes se contarían como «no quemado» y el modelo")
        print("  pagaría por ellas. Vuelve a exportar con el bloque 07 nuevo.")
        return 1

    # --- Exclusión de NoData ------------------------------------------------
    print("\n2. Exclusión de celdas sin información")
    n_total = len(d)
    d = d[d["valido"] == 1].copy()
    n_validas = len(d)
    excluidas = n_total - n_validas
    print(f"  celdas cruzadas     : {n_total:,}")
    print(f"  excluidas (valido=0): {excluidas:,} ({excluidas / max(n_total,1) * 100:.1f} %)")
    print(f"  evaluadas           : {n_validas:,}")
    print("  Las excluidas no son TP, FP, FN ni TN. Simplemente no se sabe.")
    if excluidas / max(n_total, 1) > 0.40:
        print("\n  ⚠ Más del 40 % del municipio sin observación válida. La")
        print("  validación pierde representatividad y hay que declararlo en")
        print("  la memoria. Considera buscar otras escenas en el bloque 05.")
    if n_validas == 0:
        print("\n  ERROR: no queda ninguna celda válida. Revisa el bloque 07.")
        return 1

    # --- Métricas -----------------------------------------------------------
    print("\n3. Matriz de confusión espacial")
    met = matriz_y_metricas(d)
    print(f"    TP {met['tp']:>8,}   FP {met['fp']:>8,}")
    print(f"    FN {met['fn']:>8,}   TN {met['tn']:>8,}")

    focos = None
    if Path(args.focos).exists():
        focos = pd.read_csv(args.focos)
        # Si los focos aún no tienen celda (f11 no se ha ejecutado sobre este
        # archivo), se les asigna la del grid observado por posición.
        if not ({"fila", "columna"} <= set(focos.columns)
                and focos[["fila", "columna"]].notna().all().all()):
            lat0 = float((obs["lat"] + obs["fila"] * 0.0045).mean())
            lon0 = float((obs["lon"] - obs["columna"] * 0.0045).mean())
            focos["fila"] = ((lat0 - focos["lat"]) / 0.0045).round().astype(int)
            focos["columna"] = ((focos["lon"] - lon0) / 0.0045).round().astype(int)
    dirn = metricas_direccionales(d, focos)

    print("\n4. Métricas principales")
    for k in ("iou", "precision", "recall", "f1"):
        print(f"    {k.capitalize():<10}: {met[k]}")
    print(f"\n    Área observada : {met['area_observada_km2']:>9.2f} km²")
    print(f"    Área simulada  : {met['area_simulada_km2']:>9.2f} km²")
    print(f"    Diferencia     : {met['diferencia_area_km2']:>+9.2f} km²")
    print(f"    Error de área  : {met['error_area_pct']:>9.2f} %")
    print(f"    Comportamiento : {met['comportamiento']}")

    print("\n5. Complementarias")
    ea = dirn.get("error_angular_grados")
    dc = dirn.get("distancia_centroides_km")
    print(f"    Error angular      : {'—' if ea is None else f'{ea:.2f}°'}")
    print(f"    Dist. centroides   : {'—' if dc is None else f'{dc:.3f} km'}")
    print(f"    Accuracy           : {met['accuracy']}")
    pct_tn = met["tn"] / max(met["celdas_evaluadas"], 1) * 100
    print(f"      Calculada sobre {met['celdas_evaluadas']:,} celdas, de las que")
    print(f"      {met['tn']:,} son TN ({pct_tn:.1f} %). Un valor alto refleja")
    print(f"      sobre todo la extensión no quemada del municipio, no la")
    print(f"      calidad de la propagación. NO es métrica principal.")

    if ea is not None:
        print("\n    Cómo leer el error angular junto al IoU:")
        print("      IoU bajo + ángulo pequeño  → la dirección es correcta, sobra")
        print("        o falta extensión. Se corrige con p_base o la residencia.")
        print("      IoU bajo + ángulo grande   → el frente se fue por donde no")
        print("        era. Apunta al viento, a la pendiente o a la vecindad.")

    # --- Salidas ------------------------------------------------------------
    d["clase"] = np.select(
        [(d["observado"] == 1) & (d["simulado"] == 1),
         (d["observado"] == 0) & (d["simulado"] == 1),
         (d["observado"] == 1) & (d["simulado"] == 0)],
        ["TP", "FP", "FN"], default="TN")

    cols = [c for c in ("id", "fila", "columna", "lat", "lon", "observado",
                        "simulado", "prob_quemada", "fraccion_quemada",
                        "valido", "clase") if c in d.columns]
    d[cols].to_csv(res / "comparacion_espacial.csv", index=False)

    evento_id = "(sin identificar)"
    evento = {}
    if Path(args.evento).exists():
        evento = json.loads(Path(args.evento).read_text(encoding="utf-8"))
        evento_id = evento.get("evento_id", evento_id)

    ejec = {}
    if Path(args.ejecucion).exists():
        ejec = json.loads(Path(args.ejecucion).read_text(encoding="utf-8"))

    salida = {
        "municipio": "Rurrenabaque",
        "departamento": "Beni",
        "evento": evento_id,
        "periodo": f"{evento.get('fecha_inicio','?')} a {evento.get('fecha_fin','?')}",
        "fecha_calculo": datetime.now(timezone.utc).isoformat(),
        "armonizacion": diag,
        "celdas_totales_cruzadas": n_total,
        "celdas_excluidas_sin_dato": excluidas,
        "celdas_evaluadas": n_validas,
        **met,
        **dirn,
        "n_repeticiones": ejec.get("n_repeticiones"),
        "umbral_prob_quemada": ejec.get("umbral_prob_quemada"),
        "p_base": ejec.get("p_base"),
        "advertencias": ejec.get("advertencias", []),
        "_nota_accuracy": (
            f"COMPLEMENTARIA. {met['tn']:,} de {met['celdas_evaluadas']:,} "
            f"celdas ({pct_tn:.1f} %) son TN. No usar como métrica principal."),
    }
    (res / "metricas_validacion.json").write_text(
        json.dumps(salida, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- Tabla para la memoria ---------------------------------------------
    n_focos = len(focos) if focos is not None else None
    tabla = [
        ("Evento", evento_id),
        ("Periodo", salida["periodo"]),
        ("Focos iniciales", n_focos if n_focos is not None else "—"),
        ("Repeticiones", ejec.get("n_repeticiones", "—")),
        ("Celdas evaluadas", f"{n_validas:,}"),
        ("Celdas excluidas (sin dato)", f"{excluidas:,}"),
        ("Área observada (km²)", f"{met['area_observada_km2']:.2f}"),
        ("Área simulada (km²)", f"{met['area_simulada_km2']:.2f}"),
        ("Diferencia (km²)", f"{met['diferencia_area_km2']:+.2f}"),
        ("Error de área (%)", f"{met['error_area_pct']:.2f}"),
        ("IoU", met["iou"]),
        ("Precision", met["precision"]),
        ("Recall", met["recall"]),
        ("F1-score", met["f1"]),
        ("Accuracy (complementaria)", met["accuracy"]),
        ("Error angular (°)", "—" if ea is None else f"{ea:.2f}"),
        ("Distancia entre centroides (km)", "—" if dc is None else f"{dc:.3f}"),
        ("Comportamiento", met["comportamiento"]),
    ]
    pd.DataFrame(tabla, columns=["Indicador", "Resultado"]).to_csv(
        res / "tabla_validacion.csv", index=False)

    # --- Figuras ------------------------------------------------------------
    evol = None
    if Path(args.evolucion).exists():
        evol = pd.read_csv(args.evolucion)
    print("\n6. Figuras")
    figuras(d, met, dirn, focos, res, evento_id, evol)
    print("    fig_05_probabilidad_quema.png")
    print("    fig_06_area_simulada.png")
    print("    fig_07_superposicion.png")
    if evol is not None:
        print("    fig_08_evolucion.png")

    # --- Resumen en markdown ------------------------------------------------
    escribir_resumen(res, salida, met, dirn, ejec, evento, n_focos,
                     excluidas, n_total)

    print(f"\nEscrito: {res / 'comparacion_espacial.csv'}")
    print(f"Escrito: {res / 'metricas_validacion.json'}")
    print(f"Escrito: {res / 'tabla_validacion.csv'}")
    print(f"Escrito: {res / 'resumen_validacion.md'}")
    print("=" * 74)
    return 0


def escribir_resumen(res, salida, met, dirn, ejec, evento, n_focos,
                     excluidas, n_total):
    ea = dirn.get("error_angular_grados")
    dc = dirn.get("distancia_centroides_km")
    pct_tn = met["tn"] / max(met["celdas_evaluadas"], 1) * 100

    if met["comportamiento"] == "SOBREESTIMACIÓN":
        lectura = (
            f"El autómata sobreestimó la superficie afectada en "
            f"{abs(met['diferencia_area_km2']):.2f} km² ({met['error_area_pct']:.1f} %). "
            f"El Recall de {met['recall']} frente a una Precision de {met['precision']} "
            f"indica que la simulación alcanzó buena parte de la superficie "
            f"observada, pero incluyó además superficie que no resultó afectada.")
    else:
        lectura = (
            f"El autómata subestimó la superficie afectada en "
            f"{abs(met['diferencia_area_km2']):.2f} km² ({met['error_area_pct']:.1f} %). "
            f"La Precision de {met['precision']} frente a un Recall de {met['recall']} "
            f"indica que lo simulado se corresponde con lo observado, pero la "
            f"simulación no alcanzó toda la extensión del evento.")

    if ea is not None:
        if ea < 30:
            lect_dir = (f"El error angular de {ea:.1f}° es reducido: la dirección "
                        f"principal de propagación simulada se corresponde con la "
                        f"observada, y la discrepancia está en la extensión, no en "
                        f"el rumbo.")
        elif ea < 90:
            lect_dir = (f"El error angular de {ea:.1f}° es moderado: la dirección "
                        f"simulada se desvía de la observada de forma apreciable.")
        else:
            lect_dir = (f"El error angular de {ea:.1f}° es elevado: el frente "
                        f"simulado avanzó en una dirección sustancialmente distinta "
                        f"de la observada.")
    else:
        lect_dir = "No se pudo calcular el error angular."

    adv = ejec.get("advertencias", [])
    txt = f"""# Resumen de la validación externa — Rurrenabaque

**Generado automáticamente por `f12_metricas_validacion.py`. No editar a mano:
se regenera en cada ejecución.**

## Evento

| | |
|---|---|
| Municipio | Rurrenabaque, provincia General José Ballivián, Beni |
| Evento | {salida['evento']} |
| Periodo | {salida['periodo']} |
| Focos iniciales | {n_focos if n_focos is not None else '—'} detecciones FIRMS |
| Criterio de selección | {evento.get('criterio_seleccion', '—')} |
| Sensor principal | {evento.get('sensor_principal', '—')} |

## Fuentes

| Elemento | Fuente |
|---|---|
| Actividad térmica y focos iniciales | NASA FIRMS (VIIRS / MODIS, procesamiento estándar) |
| Superficie observada | dNBR sobre Sentinel-2 / Landsat, umbral USGS 0,27 |
| Variables del territorio | ERA5-Land, MODIS MOD13A1, SRTM |
| Modelo de propagación | Autómata celular de SIPRO FIRE, sin modificar |

FIRMS **no** representa directamente superficie quemada: detecta anomalías
térmicas. La superficie observada procede del dNBR, no del recuento de focos.

## Ejecución

| | |
|---|---|
| Repeticiones | {ejec.get('n_repeticiones', '—')} |
| Semillas | 1 a {ejec.get('n_repeticiones', '—')}, registradas |
| Umbral de quema | {ejec.get('umbral_prob_quemada', '—')} de las repeticiones |
| p_base congelado | {ejec.get('p_base', '—')} |
| Parámetros congelados el | {ejec.get('fecha_congelacion_parametros', '—')} |
| Horizonte | {ejec.get('horizonte_horas', '—')} h ({ejec.get('pasos', '—')} pasos de 15 min) |

Los parámetros se fijaron **antes** de ejecutar Rurrenabaque y no se
modificaron con sus resultados.

## Cobertura de la observación

| | |
|---|---|
| Celdas cruzadas | {n_total:,} |
| Excluidas por falta de dato | {excluidas:,} ({excluidas / max(n_total,1) * 100:.1f} %) |
| Evaluadas | {met['celdas_evaluadas']:,} |

Las celdas sin información satelital válida (nube, sombra, agua) se excluyeron
por completo del cálculo: no se contabilizaron como TP, FP, FN ni TN.

## Resultados

| Indicador | Resultado |
|---|---|
| Área observada | {met['area_observada_km2']:.2f} km² |
| Área simulada | {met['area_simulada_km2']:.2f} km² |
| Diferencia | {met['diferencia_area_km2']:+.2f} km² |
| Error de área | {met['error_area_pct']:.2f} % |
| **IoU** | **{met['iou']}** |
| **Precision** | **{met['precision']}** |
| **Recall** | **{met['recall']}** |
| **F1-score** | **{met['f1']}** |
| Accuracy (complementaria) | {met['accuracy']} |
| Error angular | {'—' if ea is None else f'{ea:.2f}°'} |
| Distancia entre centroides | {'—' if dc is None else f'{dc:.3f} km'} |
| **Comportamiento** | **{met['comportamiento']}** |

Matriz de confusión espacial: TP {met['tp']:,} · FP {met['fp']:,} ·
FN {met['fn']:,} · TN {met['tn']:,}.

## Interpretación

{lectura}

{lect_dir}

La Accuracy se presenta como métrica complementaria: {met['tn']:,} de las
{met['celdas_evaluadas']:,} celdas evaluadas ({pct_tn:.1f} %) son verdaderos
negativos, de modo que un valor alto reflejaría sobre todo la extensión no
afectada del municipio y no la calidad de la representación de la propagación.

## Limitaciones

- La prueba evalúa la capacidad del autómata para reproducir el patrón espacial
  de un evento, no su exactitud predictiva operativa.
- La referencia observada depende del umbral de dNBR adoptado (0,27, criterio
  USGS). El bloque 07 publica la sensibilidad del área a ese umbral.
- El autómata es estocástico; los resultados corresponden a la agregación de
  {ejec.get('n_repeticiones', '—')} repeticiones y no a una realización única.
- XGBoost no interviene en esta validación. La columna `prob_ignicion` del grid
  de Rurrenabaque se fijó en 0 y los focos iniciales proceden de observación
  satelital.
"""
    if adv:
        txt += "\n### Advertencias registradas en la ejecución\n\n"
        for a in adv:
            txt += f"- {a}\n"

    (res / "resumen_validacion.md").write_text(txt, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
