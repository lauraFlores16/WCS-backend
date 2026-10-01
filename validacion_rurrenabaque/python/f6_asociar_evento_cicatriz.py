"""Cruza cada evento FIRMS con la cicatriz observada SOBRE SU PROPIA HUELLA.

    cd validacion_rurrenabaque/python
    python f3_f4_analisis_y_evento.py            # genera firms_eventos_etiquetados.csv
    python f6_asociar_evento_cicatriz.py --anio 2023

Qué cambió respecto de la versión anterior (f6_asociar_evento_cicatriz.py.bak)
------------------------------------------------------------------------------
La versión anterior sumaba las manchas cuyo centroide caía a menos de R km
del CENTRO del evento. Eso mide cuánta cicatriz hay en el barrio, no si el
evento la produjo: eventos distintos salían asociados a la misma mancha #579
y el evento propuesto por f3_f4 (E41) ni siquiera aparecía.

Ahora se mide sobre la huella real del evento: las celdas de 500 m donde
FIRMS lo detectó, dilatadas una celda (≈ el píxel VIIRS de 375 m más su error
de geolocalización). Para cada evento:

    ACIERTO   fracción de sus celdas FIRMS con cicatriz (≥ 25 %) en su 3×3.
              Alto = las detecciones térmicas SÍ dejaron área quemada.
    OBS.CELDAS celdas de la huella con fracción quemada ≥ 0,50 (regla de f9c).
              Es el tamaño de la referencia contra la que se medirá el CA.
    CICATRIZ  km² de 30 m quemados dentro de la huella.
    SOLAPE    eventos de OTRAS fechas (> 3 días) cuya huella toca la misma zona. MapBiomas
              es ANUAL: si hay solape, parte de la cicatriz puede ser de otro
              evento y la referencia queda contaminada.
    CONTAM.   fracción de las OBS.CELDAS que tocan detecciones de esos eventos
              de otras fechas: la parte de la referencia que podría no ser suya.

Validable = OBS.CELDAS ≥ --min-celdas, ACIERTO ≥ --min-acierto y
            CONTAM. ≤ --max-contaminacion.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from cicatriz_grid import buscar_raster, fraccion_por_celda, vecinos
from rejilla import Rejilla, GRID_POR_ZONA, AREA_CELDA_KM2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zona", default="rurrenabaque")
    ap.add_argument("--anio", default="2023")
    ap.add_argument("--grid", default=None)
    ap.add_argument("--raster", default=None)
    ap.add_argument("--etiquetados", default="../resultados/firms_eventos_etiquetados.csv")
    ap.add_argument("--candidatos", default="../resultados/eventos_candidatos.csv")
    ap.add_argument("--min-focos", type=int, default=20)
    ap.add_argument("--min-celdas", type=int, default=10)
    ap.add_argument("--min-acierto", type=float, default=0.80)
    ap.add_argument("--umbral-celda", type=float, default=0.50)
    ap.add_argument("--dilatacion", type=int, default=1)
    ap.add_argument("--max-contaminacion", type=float, default=0.25)
    ap.add_argument("--resultados", default="../resultados")
    args = ap.parse_args()

    rej = Rejilla.desde_csv(args.grid or GRID_POR_ZONA[args.zona])
    raster = Path(args.raster) if args.raster else buscar_raster(args.anio)
    et = pd.read_csv(args.etiquetados, parse_dates=["fecha_hora_utc"])
    et = et[et["evento"].notna() & (et["evento"] != "")]
    cand = pd.read_csv(args.candidatos).set_index("evento_id")

    print("=" * 96)
    print(f"ASOCIACIÓN EVENTO ↔ CICATRIZ (huella FIRMS) · {args.zona} {args.anio}")
    print("=" * 96)
    print(f"Cicatriz : {raster.name}")
    frac, total_km2, _ = fraccion_por_celda(raster, rej)
    n05 = sum(1 for v, _ in frac.values() if v is not None and v >= args.umbral_celda)
    print(f"Quemado en el municipio: {total_km2:.1f} km² · "
          f"{n05} celdas de 500 m con ≥ {args.umbral_celda:.0%} quemado")
    print(f"Eventos DBSCAN: {et['evento'].nunique()} · se evalúan los de ≥ {args.min_focos} focos\n")

    huellas = {}
    for eid, g in et.groupby("evento"):
        cel = {rej.indice(la, lo) for la, lo in zip(g["latitude"], g["longitude"])}
        huellas[eid] = (cel, vecinos(cel, args.dilatacion))

    ventanas = {e: (g["fecha_hora_utc"].min(), g["fecha_hora_utc"].max())
                for e, g in et.groupby("evento")}
    filas = []
    for eid, g in et.groupby("evento"):
        if len(g) < args.min_focos:
            continue
        cel, dil = huellas[eid]
        dil_in = [k for k in dil if k in frac]
        fr = {k: (frac[k][0] or 0.0) for k in dil_in}
        cic_km2 = sum(fr[k] * AREA_CELDA_KM2 for k in dil_in)
        obs = sum(1 for k in dil_in if fr[k] >= args.umbral_celda)
        acierto = sum(1 for k in cel
                      if any((frac.get(v, (0, 0))[0] or 0) >= 0.25 for v in vecinos({k}))
                      ) / max(len(cel), 1)
        # Solape con eventos de OTRAS fechas (> 3 días de separación). Los
        # simultáneos suelen ser trozos del mismo complejo que DBSCAN partió;
        # los de otras fechas son los que contaminan una referencia anual.
        ini, fin = g["fecha_hora_utc"].min(), g["fecha_hora_utc"].max()
        margen = pd.Timedelta(days=3)
        solape = [o for o, (c2, _) in huellas.items()
                  if o != eid and c2 & dil
                  and (ventanas[o][0] > fin + margen or ventanas[o][1] < ini - margen)]
        simult = [o for o, (c2, _) in huellas.items()
                  if o != eid and c2 & dil and o not in solape]
        ajenas = set().union(*[huellas[o][0] for o in solape]) if solape else set()
        obs_set = {k for k in dil_in if fr[k] >= args.umbral_celda}
        contaminadas = len(obs_set & vecinos(ajenas)) / max(len(obs_set), 1)
        c = cand.loc[eid]
        filas.append({
            "evento_id": eid, "focos": len(g), "celdas_firms": len(cel),
            "inicio_utc": str(c["inicio_utc"])[:16], "fin_utc": str(c["fin_utc"])[:16],
            "duracion_h": float(c["duracion_h"]),
            "c_temp": float(c["continuidad_temporal"]),
            "c_esp": float(c["continuidad_espacial"]),
            "lat": float(c["lat_centro"]), "lon": float(c["lon_centro"]),
            "cicatriz_km2": round(cic_km2, 2), "obs_celdas": obs,
            "acierto": round(acierto, 2),
            "solape_eventos": len(solape), "solape_ids": ",".join(sorted(solape)),
            "simultaneos_ids": ",".join(sorted(simult)),
            "contaminacion": round(contaminadas, 2),
        })

    r = pd.DataFrame(filas)
    r["validable"] = ((r["obs_celdas"] >= args.min_celdas)
                      & (r["acierto"] >= args.min_acierto)
                      & (r["contaminacion"] <= args.max_contaminacion))
    r = r.sort_values(["validable", "obs_celdas", "acierto", "c_esp"],
                      ascending=False).reset_index(drop=True)

    print(f"{'ID':>5}{'FOCOS':>6}{'CELD.FIRMS':>11}{'INICIO':>18}{'DUR.h':>7}"
          f"{'C.ESP':>7}{'ACIERTO':>9}{'OBS.CELDAS':>11}{'CICATRIZ':>10}{'CONTAM.':>8}{'VALID.':>8}")
    print("-" * 100)
    for _, x in r.head(20).iterrows():
        print(f"{x.evento_id:>5}{x.focos:>6}{x.celdas_firms:>11}{x.inicio_utc:>18}"
              f"{x.duracion_h:>7.0f}{x.c_esp:>7.2f}{x.acierto:>9.2f}{x.obs_celdas:>11}"
              f"{x.cicatriz_km2:>10.2f}{x.contaminacion:>8.2f}{('SÍ' if x.validable else 'no'):>8}")
    print("-" * 100)

    sel = json.loads(Path(args.resultados, "evento_validacion.json").read_text(encoding="utf-8"))
    prop = sel.get("evento_id")
    if prop in set(r["evento_id"]):
        x = r[r["evento_id"] == prop].iloc[0]
        print(f"\nEvento propuesto por f3_f4 ({prop}, el de más focos): "
              f"acierto {x.acierto:.2f} · {x.obs_celdas} celdas observadas · "
              f"{'VALIDABLE' if x.validable else 'NO validable'}")

    val = r[r["validable"]]
    if val.empty:
        print("\nNingún evento cumple los tres criterios. Relaja --min-celdas o "
              "usa dNBR (f9b) como referencia.")
    else:
        m = val.iloc[0]
        print(f"\nRECOMENDADO: {m.evento_id} · {m.inicio_utc} → {m.fin_utc} · "
              f"{m.focos} focos · acierto {m.acierto:.2f} · {m.obs_celdas} celdas observadas")
        print(f"  Fijarlo:  python f3_f4_analisis_y_evento.py --evento {m.evento_id}")

    out = Path(args.resultados)
    r.to_csv(out / "asociacion_evento_cicatriz.csv", index=False)
    (out / "asociacion_evento_cicatriz.json").write_text(json.dumps({
        "zona": args.zona, "anio": args.anio, "raster": raster.name,
        "metodo": "huella FIRMS dilatada ±%d celda(s)" % args.dilatacion,
        "criterios": {"min_focos": args.min_focos, "min_celdas": args.min_celdas,
                      "min_acierto": args.min_acierto, "max_contaminacion": args.max_contaminacion,
                      "umbral_celda": args.umbral_celda},
        "cicatriz_total_km2": round(total_km2, 2),
        "celdas_quemadas_municipio": n05,
        "validables": list(val["evento_id"]),
        "recomendado": None if val.empty else val.iloc[0]["evento_id"],
        "candidatos": r.to_dict(orient="records"),
    }, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\nEscrito: {out / 'asociacion_evento_cicatriz.csv'}")
    print(f"Escrito: {out / 'asociacion_evento_cicatriz.json'}")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
