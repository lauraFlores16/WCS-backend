"""Referencia OBSERVADA del evento, en el formato que lee f12.

    cd validacion_rurrenabaque/python
    python f9d_observado_evento.py --evento E122

Toma la cicatriz anual de MapBiomas Fire (30 m), la agrega al grid de 500 m
y la recorta al evento con las detecciones FIRMS etiquetadas por f3_f4.

Reglas por celda (todas se deciden aquí, ANTES de simular):

    observado = 1   fracción quemada ≥ 0,50 (mayoría simple, igual que f9c)
                    y dentro de la zona del evento (huella FIRMS ± --margen celdas)
    valido    = 0   · sin dato en el ráster
                    · quemada (≥ 0,25) FUERA de la zona del evento: la quemó
                      otro incendio de 2023 y el producto anual no dice cuándo;
                      no es ni acierto ni fallo del autómata
                    · dentro de la zona pero tocada por detecciones de eventos
                      de OTRAS fechas (> 3 días): atribución dudosa
    valido    = 1   el resto del municipio. Un autómata que se desborde sobre
                    terreno no quemado paga falsos positivos en todo el
                    municipio, no solo cerca del evento.

Salida: ../datos/observado_grid.csv  (id, fila, columna, lat, lon, observado,
        fraccion_quemada, valido, motivo) + ../resultados/f9d_observado_meta.json
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import pandas as pd

from cicatriz_grid import buscar_raster, fraccion_por_celda, vecinos
from rejilla import Rejilla, GRID_POR_ZONA, AREA_CELDA_KM2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--evento", default=None,
                    help="p. ej. E122. Por defecto, el de evento_validacion.json")
    ap.add_argument("--zona", default="rurrenabaque")
    ap.add_argument("--anio", default="2023")
    ap.add_argument("--grid", default=None)
    ap.add_argument("--raster", default=None)
    ap.add_argument("--etiquetados", default="../resultados/firms_eventos_etiquetados.csv")
    ap.add_argument("--umbral", type=float, default=0.50)
    ap.add_argument("--margen", type=int, default=2,
                    help="Celdas alrededor de la huella FIRMS que cuentan como zona del evento")
    ap.add_argument("--salida", default="../datos/observado_grid.csv")
    ap.add_argument("--resultados", default="../resultados")
    args = ap.parse_args()

    eid = args.evento or json.loads(
        Path(args.resultados, "evento_validacion.json").read_text(encoding="utf-8"))["evento_id"]
    rej = Rejilla.desde_csv(args.grid or GRID_POR_ZONA[args.zona])
    raster = Path(args.raster) if args.raster else buscar_raster(args.anio)
    frac, total_km2, _ = fraccion_por_celda(raster, rej)

    et = pd.read_csv(args.etiquetados, parse_dates=["fecha_hora_utc"])
    et = et[et["evento"].notna() & (et["evento"] != "")]
    g = et[et["evento"] == eid]
    if g.empty:
        print(f"No hay detecciones del evento {eid} en {args.etiquetados}")
        return 1
    ini, fin = g["fecha_hora_utc"].min(), g["fecha_hora_utc"].max()
    margen_t = pd.Timedelta(days=3)
    otras = et[(et["fecha_hora_utc"] > fin + margen_t) | (et["fecha_hora_utc"] < ini - margen_t)]

    huella = {rej.indice(a, b) for a, b in zip(g["latitude"], g["longitude"])}
    zona_ev = vecinos(huella, args.margen)
    ajenas = vecinos({rej.indice(a, b) for a, b in zip(otras["latitude"], otras["longitude"])}, 1)

    cont = {"observado": 0, "no_quemado": 0, "sin_dato": 0,
            "otro_incendio": 0, "atribucion_dudosa": 0}
    filas = []
    for (f, c), row in sorted(rej.celdas.items()):
        fr, npx = frac.get((f, c), (None, 0))
        obs, val, motivo = 0, 1, "no_quemado"
        if fr is None:
            val, motivo = 0, "sin_dato"
        elif (f, c) in zona_ev:
            if (f, c) in ajenas and fr >= 0.25:
                val, motivo = 0, "atribucion_dudosa"
            elif fr >= args.umbral:
                obs, motivo = 1, "observado"
        elif fr >= 0.25:
            val, motivo = 0, "otro_incendio"
        cont[motivo] += 1
        filas.append([row["id"], f, c, row["lat"], row["lon"], obs,
                      "" if fr is None else round(fr, 4), val, motivo, npx])

    salida = Path(args.salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    with open(salida, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "fila", "columna", "lat", "lon", "observado",
                    "fraccion_quemada", "valido", "motivo", "pixeles_30m"])
        w.writerows(filas)

    n_obs = cont["observado"]
    km_raster = sum((frac[k][0] or 0) * AREA_CELDA_KM2 for k in zona_ev if k in frac
                    and not (k in ajenas and (frac[k][0] or 0) >= 0.25))
    meta = {
        "evento_id": eid, "inicio_utc": ini.isoformat(), "fin_utc": fin.isoformat(),
        "focos": int(len(g)), "celdas_huella_firms": len(huella),
        "fuente": f"MapBiomas Fire Bolivia col.1, anual {args.anio} ({raster.name})",
        "regla": f"observado = fracción quemada ≥ {args.umbral} dentro de la huella "
                 f"FIRMS ± {args.margen} celdas",
        "celdas_observadas": n_obs,
        "area_observada_grid_km2": n_obs * AREA_CELDA_KM2,
        "area_observada_raster_30m_km2": round(km_raster, 2),
        "celdas_por_motivo": cont,
        "celdas_validas": sum(1 for x in filas if x[7] == 1),
        "celdas_excluidas": sum(1 for x in filas if x[7] == 0),
        "_uso": "REFERENCIA. No entra al autómata.",
        "_limitacion": "Producto anual: la fecha de cada píxel no se conoce. La "
                       "atribución al evento se hace por coincidencia con su huella FIRMS.",
    }
    Path(args.resultados, "f9d_observado_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 72)
    print(f"REFERENCIA OBSERVADA · {eid} · {ini:%Y-%m-%d %H:%M} → {fin:%Y-%m-%d %H:%M}")
    print("=" * 72)
    print(f"Huella FIRMS          : {len(huella)} celdas ({len(g)} focos)")
    print(f"Zona del evento       : huella ± {args.margen} celdas = {len(zona_ev)} celdas")
    print(f"Observado (≥ {args.umbral:.0%})      : {n_obs} celdas = {n_obs * AREA_CELDA_KM2:.2f} km²")
    print(f"Quemado en 30 m (zona): {km_raster:.2f} km²")
    print("\nCeldas por motivo:")
    for k, v in cont.items():
        print(f"  {k:<20}{v:>7,}")
    print(f"\nEvaluables (valido=1) : {meta['celdas_validas']:,}")
    print(f"Excluidas (valido=0)  : {meta['celdas_excluidas']:,}")
    print(f"\nEscrito: {salida}")
    print(f"Escrito: {Path(args.resultados, 'f9d_observado_meta.json')}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
