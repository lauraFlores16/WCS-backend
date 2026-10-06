"""
P1 — Focos NASA FIRMS verificados de un municipio y un año, y sus eventos.

    cd WCS-backend/validacion_zonas/python
    python p1_focos_anio.py --municipio "Ixiamas" --anio 2022 --clave TU_MAP_KEY
    python p1_focos_anio.py --municipio "San Pedro" --departamento "Santa Cruz" --anio 2023

    # Sin descargar (CSV de FIRMS ya bajado, por ejemplo el de Rurrenabaque):
    python p1_focos_anio.py --municipio rurrenabaque --anio 2023 \
        --csv ../../validacion_rurrenabaque/datos/firms_rbq_2023_bruto.csv

QUÉ HACE
  1. Descarga el año completo del ARCHIVO ESTÁNDAR de FIRMS (colecciones _SP,
     reprocesadas por NASA; no las de tiempo casi real) dentro del bbox del
     municipio, en tramos de 5 días, y recorta al límite municipal.
  2. Clasifica cada detección por su NIVEL DE VERIFICACIÓN (ver abajo).
  3. Agrupa las detecciones verificadas en EVENTOS con el mismo DBSCAN
     espaciotemporal de Rurrenabaque (1.500 m · 48 h · 5 detecciones).
  4. Contrasta cada evento con la cicatriz anual de MapBiomas Fuego.

NIVELES DE VERIFICACIÓN (FIRMS no «verifica» en terreno: detecta anomalías
térmicas. Estos son los criterios defendibles que sí se pueden aplicar):

    descartado    type ≠ 0: volcán, fuente fija (industria, gas) u offshore
    baja          confianza baja (VIIRS 'l', MODIS < 30): posible falso positivo
    verificado    archivo estándar _SP + type 0 + confianza nominal/alta
    confirmado    verificado Y hay píxel quemado de MapBiomas del mismo año
                  a menos de una huella del sensor (375 m VIIRS, 1 km MODIS):
                  confirmación independiente, desde otro satélite y otro método

SALIDA (zonas/<municipio>/<año>/)
    firms_bruto.csv      todo lo que devolvió FIRMS en el bbox
    focos.csv            detecciones del municipio con nivel y evento
    eventos.json         eventos con sus indicadores, ordenados por tamaño
    resumen_anio.json    cifras del año (alimenta la pantalla de Validación)
    limite.geojson       límite municipal usado
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from comun import (ZONAS, actualizar_indice, buscar_municipio, escribir_json,
                   importar_rbq, m_lon, raster_mapbiomas, rol_de, M_LAT)

FUENTES_DEFECTO = ["VIIRS_SNPP_SP", "VIIRS_NOAA20_SP"]
HUELLA_M = {"VIIRS": 375.0, "MODIS": 1000.0}


# ---------------------------------------------------------------------------
def descargar(clave: str, anio: int, bbox: str, fuentes: list[str]) -> tuple[pd.DataFrame, list]:
    importar_rbq()
    import f2_descargar_firms_2023 as F2
    F2.FUENTES = fuentes
    hoy = date.today()
    fin = min(date(anio, 12, 31), hoy)
    est = F2.estado_clave(clave)
    if est:
        print(f"Clave FIRMS: {est.get('current_transactions')} de "
              f"{est.get('transaction_limit')} transacciones usadas en la ventana actual")
    return F2.descargar_rango(clave, date(anio, 1, 1), fin, bbox)


def normalizar(df: pd.DataFrame) -> pd.DataFrame:
    importar_rbq()
    import f2_descargar_firms_2023 as F2
    d = F2.normalizar(df)
    if "fuente_firms" not in d.columns:
        d["fuente_firms"] = "desconocida"
    if "type" not in d.columns:
        d["type"] = np.nan
    return d


def recortar(df: pd.DataFrame, geometria) -> pd.DataFrame:
    from shapely.geometry import Point
    from shapely.prepared import prep
    pg = prep(geometria)
    m = [pg.contains(Point(lo, la)) for la, lo in zip(df["latitude"], df["longitude"])]
    return df[m].copy()


# ---------------------------------------------------------------------------
def nivel_firms(r) -> str:
    t = r.get("type")
    if pd.notna(t) and int(t) != 0:
        return "descartado"
    c = r.get("confidence")
    if isinstance(c, str):
        if c.strip().lower() in ("l", "low"):
            return "baja"
    elif pd.notna(c) and float(c) < 30:
        return "baja"
    if not str(r.get("fuente_firms", "")).upper().endswith("_SP"):
        return "baja"          # tiempo casi real: no es el archivo reprocesado
    return "verificado"


def confirmar_mapbiomas(df: pd.DataFrame, anio: int, bbox) -> dict:
    """Marca 'confirmado' a los verificados con quema de MapBiomas cerca."""
    raster = raster_mapbiomas(anio)
    if raster is None:
        print(f"  Sin ráster de MapBiomas para {anio}: no se puede confirmar.")
        return {"raster": None}
    import rasterio
    from rasterio.windows import from_bounds
    o, s, e, n = bbox
    margen = 0.02
    with rasterio.open(raster) as src:
        w = from_bounds(o - margen, s - margen, e + margen, n + margen,
                        transform=src.transform).round_offsets().round_lengths()
        a = src.read(1, window=w)
        tr = src.window_transform(w)
    quem = a == 1
    lat_c = (s + n) / 2
    px_lat = abs(tr.e) * M_LAT
    px_lon = abs(tr.a) * m_lon(lat_c)
    confirmados = 0
    for i, r in df[df["nivel"] == "verificado"].iterrows():
        huella = HUELLA_M["MODIS" if "MODIS" in str(r["fuente_firms"]).upper() else "VIIRS"]
        ry, rx = int(math.ceil(huella / px_lat)), int(math.ceil(huella / px_lon))
        fila = int((r["latitude"] - tr.f) / tr.e)
        col = int((r["longitude"] - tr.c) / tr.a)
        ventana = quem[max(fila - ry, 0):fila + ry + 1, max(col - rx, 0):col + rx + 1]
        if ventana.size and ventana.any():
            df.at[i, "nivel"] = "confirmado"
            confirmados += 1
    km2 = float(quem.sum()) * px_lat * px_lon / 1e6
    print(f"  MapBiomas {anio}: {confirmados} detecciones confirmadas · "
          f"{km2:,.0f} km² quemados en el recuadro")
    return {"raster": raster.name, "km2_quemados_bbox": round(km2, 1), "confirmados": confirmados,
            "_quem": quem, "_tr": tr, "_px": (px_lat, px_lon)}


# ---------------------------------------------------------------------------
def agrupar(d: pd.DataFrame, eps_m: float, eps_h: float, min_muestras: int) -> pd.DataFrame:
    from sklearn.cluster import DBSCAN
    d = d.dropna(subset=["fecha_hora_utc"]).copy()
    if d.empty:
        d["dbscan"] = []
        return d
    lat_ref = float(d["latitude"].mean())
    horas = (d["fecha_hora_utc"] - d["fecha_hora_utc"].min()).dt.total_seconds() / 3600
    X = np.column_stack([d["longitude"].astype(float) * m_lon(lat_ref),
                         d["latitude"].astype(float) * M_LAT,
                         horas * (eps_m / eps_h)])
    d["dbscan"] = DBSCAN(eps=eps_m, min_samples=min_muestras).fit_predict(X)
    return d


def resumir_eventos(d: pd.DataFrame, mb: dict) -> list[dict]:
    eventos = []
    grupos = [(k, g) for k, g in d[d["dbscan"] >= 0].groupby("dbscan")]
    grupos.sort(key=lambda kg: kg[1]["fecha_hora_utc"].min())
    for n, (k, g) in enumerate(grupos, start=1):
        lat, lon = g["latitude"].astype(float), g["longitude"].astype(float)
        lat_c = float(lat.mean())
        ancho = (lon.max() - lon.min()) * m_lon(lat_c) / 1000
        alto = (lat.max() - lat.min()) * M_LAT / 1000
        dur_h = (g["fecha_hora_utc"].max() - g["fecha_hora_utc"].min()).total_seconds() / 3600
        dias_span = max((g["fecha_hora_utc"].max().date() - g["fecha_hora_utc"].min().date()).days + 1, 1)
        celdas = {(round(a / 0.0045), round(b / 0.0045)) for a, b in zip(lat, lon)}
        n_conf = int((g["nivel"] == "confirmado").sum())
        frac_conf = n_conf / len(g) if mb.get("raster") else None

        quem_km2 = None
        if mb.get("raster"):
            tr, (pla, plo) = mb["_tr"], mb["_px"]
            margen = 1.0 / 111.0
            f0, f1 = sorted((int((lat.max() + margen - tr.f) / tr.e), int((lat.min() - margen - tr.f) / tr.e)))
            c0, c1 = sorted((int((lon.min() - margen - tr.c) / tr.a), int((lon.max() + margen - tr.c) / tr.a)))
            sub = mb["_quem"][max(f0, 0):f1 + 1, max(c0, 0):c1 + 1]
            quem_km2 = round(float(sub.sum()) * pla * plo / 1e6, 2)

        if frac_conf is None:
            nivel = "verificado"
        elif frac_conf >= 0.5:
            nivel = "confirmado"
        elif frac_conf > 0:
            nivel = "parcial"
        else:
            nivel = "sin_cicatriz"
        frp = g["frp"].astype(float) if "frp" in g else pd.Series(dtype=float)
        eventos.append({
            "evento_id": f"E{n:03d}", "dbscan": int(k), "focos": int(len(g)),
            "inicio_utc": g["fecha_hora_utc"].min().isoformat(),
            "fin_utc": g["fecha_hora_utc"].max().isoformat(),
            "duracion_h": round(dur_h, 1),
            "centro": {"lat": round(lat_c, 6), "lon": round(float(lon.mean()), 6)},
            "bbox": [round(float(lon.min()), 5), round(float(lat.min()), 5),
                     round(float(lon.max()), 5), round(float(lat.max()), 5)],
            "extension_km": round(math.hypot(ancho, alto), 2),
            "celdas_500m_tocadas": len(celdas),
            "continuidad_temporal": round(g["fecha_hora_utc"].dt.date.nunique() / dias_span, 3),
            "continuidad_espacial": round(min(len(celdas) * 0.25 / max(ancho * alto, 0.25), 1.0), 3),
            "frp_total_mw": round(float(frp.sum()), 1) if len(frp) else None,
            "frp_max_mw": round(float(frp.max()), 1) if len(frp) else None,
            "sensores": sorted(g["fuente_firms"].astype(str).unique().tolist()),
            "focos_confirmados": n_conf,
            "fraccion_confirmada": None if frac_conf is None else round(frac_conf, 3),
            "mapbiomas_km2_cerca": quem_km2,
            "verificacion": nivel,
        })
    # Orden propuesto: tamaño y compacidad. NO decide qué evento se usa.
    eventos.sort(key=lambda e: (e["focos"], e["continuidad_espacial"]), reverse=True)
    return eventos


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--municipio", required=True, help="Nombre o id del catálogo")
    ap.add_argument("--departamento", default=None)
    ap.add_argument("--anio", type=int, required=True)
    ap.add_argument("--clave", default=os.environ.get("FIRMS_MAP_KEY"))
    ap.add_argument("--fuentes", default=",".join(FUENTES_DEFECTO),
                    help="Colecciones _SP separadas por coma (añade MODIS_SP si quieres)")
    ap.add_argument("--csv", default=None, help="CSV de FIRMS ya descargado (no descarga)")
    ap.add_argument("--incluir-baja", action="store_true",
                    help="Agrupar también las detecciones de confianza baja")
    ap.add_argument("--eps-m", type=float, default=1500)
    ap.add_argument("--eps-h", type=float, default=48)
    ap.add_argument("--min-muestras", type=int, default=5)
    args = ap.parse_args()

    from shapely.geometry import shape
    mun = buscar_municipio(args.municipio, args.departamento)
    P = mun["properties"]
    geom = shape(mun["geometry"])
    salida = ZONAS / P["id"] / str(args.anio)
    salida.mkdir(parents=True, exist_ok=True)
    escribir_json(salida.parent / "limite.geojson",
                  {"type": "FeatureCollection", "features": [mun]}, compacto=True)

    print("=" * 74)
    print(f"FOCOS FIRMS · {P['nombre']} ({P['departamento']}) · {args.anio}")
    print("=" * 74)
    print(f"Superficie {P['area_km2']:,.0f} km² · bbox {P['bbox']}")
    rol = rol_de(P["id"])
    print(f"Rol en la partición: {rol or 'SIN ASIGNAR (se decide en p2)'}")

    # --- 1. Descarga --------------------------------------------------------
    bruto = salida / "firms_bruto.csv"
    bitacora = []
    if args.csv:
        df = pd.read_csv(args.csv)
        print(f"\nCSV dado: {len(df):,} filas ({args.csv})")
    elif bruto.exists():
        df = pd.read_csv(bruto)
        print(f"\nYa descargado: {len(df):,} filas ({bruto.name}). Bórralo para volver a bajar.")
    else:
        if not args.clave:
            print("\nFalta la clave MAP_KEY de FIRMS (--clave o variable FIRMS_MAP_KEY).")
            print("Se pide gratis en https://firms.modaps.eosdis.nasa.gov/api/map_key/")
            return 2
        bbox = ",".join(f"{v:.5f}" for v in P["bbox"])
        df, bitacora = descargar(args.clave, args.anio, bbox, args.fuentes.split(","))
        if df.empty and not any(b.get("ok") for b in bitacora):
            print("\nFIRMS no devolvió nada válido. No se escribe ningún resultado.")
            return 1
        df.to_csv(bruto, index=False)
    if df.empty:
        print("Sin detecciones en el año.")
    d = normalizar(df) if not df.empty else df
    d = d[d["acq_date"].dt.year == args.anio] if not d.empty else d
    d = recortar(d, geom) if not d.empty else d
    print(f"Dentro del municipio: {len(d):,} detecciones")

    # --- 2. Verificación ----------------------------------------------------
    d["nivel"] = [nivel_firms(r) for r in d.to_dict("records")] if len(d) else []
    mb = confirmar_mapbiomas(d, args.anio, P["bbox"]) if len(d) else {"raster": None}
    cuenta = d["nivel"].value_counts().to_dict() if len(d) else {}
    print("\nNiveles de verificación:")
    for k in ("confirmado", "verificado", "baja", "descartado"):
        print(f"  {k:<12}{cuenta.get(k, 0):>7,}")

    # --- 3. Eventos ---------------------------------------------------------
    aceptados = ("confirmado", "verificado", "baja") if args.incluir_baja else ("confirmado", "verificado")
    base = d[d["nivel"].isin(aceptados)] if len(d) else d
    g = agrupar(base, args.eps_m, args.eps_h, args.min_muestras) if len(base) else base
    eventos = resumir_eventos(g, mb) if len(g) else []
    mapa = {e["dbscan"]: e["evento_id"] for e in eventos}
    d["evento"] = ""
    if len(g):
        d.loc[g.index, "evento"] = g["dbscan"].map(mapa).fillna("")
    cols = [c for c in ("latitude", "longitude", "acq_date", "acq_time", "fecha_hora_utc",
                        "fuente_firms", "satellite", "instrument", "confidence", "type",
                        "version", "frp", "brillo_k", "daynight", "nivel", "evento") if c in d.columns]
    d[cols].to_csv(salida / "focos.csv", index=False)
    escribir_json(salida / "eventos.json", {
        "municipio": P, "anio": args.anio,
        "agrupamiento": {"algoritmo": "DBSCAN espaciotemporal", "eps_espacial_m": args.eps_m,
                         "eps_temporal_h": args.eps_h, "min_muestras": args.min_muestras,
                         "niveles_incluidos": list(aceptados)},
        "eventos": eventos})

    print(f"\nEventos: {len(eventos)}  (de {len(base):,} detecciones agrupables)")
    print(f"{'ID':>6}{'FOCOS':>7}{'INICIO':>18}{'DUR h':>7}{'EXT km':>8}{'CONF':>7}{'MB km²':>8}  VERIFICACIÓN")
    for e in eventos[:15]:
        fc = "—" if e["fraccion_confirmada"] is None else f"{e['fraccion_confirmada']:.0%}"
        mk = "—" if e["mapbiomas_km2_cerca"] is None else f"{e['mapbiomas_km2_cerca']:.1f}"
        print(f"{e['evento_id']:>6}{e['focos']:>7}{e['inicio_utc'][:16]:>18}{e['duracion_h']:>7.0f}"
              f"{e['extension_km']:>8.1f}{fc:>7}{mk:>8}  {e['verificacion']}")

    escribir_json(salida / "resumen_anio.json", {
        "municipio": {k: P[k] for k in ("id", "nombre", "departamento", "provincia", "area_km2", "bbox", "centro")},
        "anio": args.anio,
        "fecha_proceso": datetime.now(timezone.utc).isoformat(),
        "fuente_firms": "NASA FIRMS, archivo estándar (_SP)" if not args.csv else f"CSV: {Path(args.csv).name}",
        "colecciones": sorted(d["fuente_firms"].astype(str).unique().tolist()) if len(d) else [],
        "focos": {"bbox": int(len(df)), "total": int(len(d)),
                  "verificados": int(d["nivel"].isin(["verificado", "confirmado"]).sum()) if len(d) else 0,
                  "por_nivel": {k: int(v) for k, v in cuenta.items()},
                  "por_mes": {int(k): int(v) for k, v in d.groupby(d["acq_date"].dt.month).size().items()} if len(d) else {}},
        "mapbiomas": {k: v for k, v in mb.items() if not k.startswith("_")},
        "eventos": {"total": len(eventos),
                    "confirmados": sum(e["verificacion"] == "confirmado" for e in eventos)},
        "descarga": bitacora,
    })
    actualizar_indice()
    print(f"\nEscrito en {salida}")
    if eventos:
        e = eventos[0]
        print(f"\nSiguiente: python p2_preparar_evento.py --municipio {P['id']} "
              f"--anio {args.anio} --evento {e['evento_id']} --rol validacion")
    return 0


if __name__ == "__main__":
    sys.exit(main())
