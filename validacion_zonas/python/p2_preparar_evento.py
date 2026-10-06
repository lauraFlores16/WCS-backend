"""
P2 — Paquete de validación de UN evento: grid, cicatriz observada, focos
iniciales e insumos externos, en el formato que corre el autómata.

    python p2_preparar_evento.py --municipio ixiamas-la-paz --anio 2022 \
        --evento E004 --rol validacion

    # Reutilizando un grid o unos insumos ya construidos (sin red):
    python p2_preparar_evento.py --municipio rurrenabaque --anio 2023 --evento E117 \
        --rol validacion --grid ../../validacion_rurrenabaque/datos/rurrenabaque_grid_500m.csv \
        --insumos ../../validacion_rurrenabaque/datos/insumos_validacion_rbq.json

QUÉ HACE (las mismas reglas que se usaron en Rurrenabaque)
  1. Ventana del evento: recuadro de sus detecciones + --margen-km, alineado
     a la rejilla global de 0,0045°. No se recorta al municipio: el fuego no
     respeta límites y la cicatriz puede cruzarlos.
  2. Grid de 500 m con elevación y pendiente (SRTM), NDVI (Sentinel-2 previo al
     evento), humedad y viento (ERA5-Land del evento). Es f8_construir_grid.
  3. Observado: cicatriz anual de MapBiomas Fuego llevada al grid (mayoría de
     píxeles quemados), dentro de la huella FIRMS del evento ± 2 celdas. Las
     celdas quemadas por OTROS incendios del año se excluyen (valido = 0).
     Es la regla de f9d.
  4. Focos iniciales: detecciones de las primeras 6 h del evento.
  5. Insumos externos grabados (clima ERA5 horario, caminos y ríos de
     OpenStreetMap) para que la corrida sea reproducible sin red.

LA PARTICIÓN (--rol) es obligatoria la primera vez y no se puede cambiar
después: un municipio de validación no entra NUNCA en la calibración.

SALIDA: zonas/<municipio>/<año>/<evento>/
    paquete.json  grid.csv  observado.csv  focos_iniciales.csv
    evento.json   insumos.json  ventana.geojson
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from comun import (PASO, AREA_CELDA_KM2, RAIZ_BACKEND, ZONAS, Ventana, actualizar_indice,
                   asignar_rol, buscar_municipio, escribir_json, importar_rbq,
                   leer_json, raster_mapbiomas, rol_de, slug)

MINUTOS_POR_ITERACION = 15
COLS_GRID = ["id", "fila", "columna", "lat", "lon", "pendiente_grados", "ndvi", "humedad",
             "viento_u", "viento_v", "prob_ignicion", "elevacion_m"]


def construir_grid(ventana: Ventana, desde: str, hasta: str, args) -> tuple[list[dict], dict]:
    importar_rbq()
    import f8_construir_grid as F8
    celdas = F8.celdas_de(ventana, poligono=False)
    print(f"  {len(celdas):,} celdas en la ventana")
    fuentes, faltan = {}, []
    print("  · topografía (SRTM 30 m)…")
    try:
        F8.traer_elevacion(celdas, "opentopodata")
        F8.calcular_pendiente(celdas, ventana)
        fuentes["elevacion"] = "SRTM 30 m vía OpenTopoData; pendiente por diferencias finitas"
    except Exception as e:  # noqa: BLE001
        print(f"    FALLÓ: {e}")
        faltan.append("elevacion/pendiente")
    print("  · meteorología (ERA5-Land)…")
    try:
        F8.traer_meteo(celdas, ventana, desde, hasta, args.submuestreo)
        fuentes["humedad_viento"] = f"ERA5-Land vía Open-Meteo, media {desde} a {hasta}"
    except Exception as e:  # noqa: BLE001
        print(f"    FALLÓ: {e}")
        faltan.append("humedad/viento")
    print("  · vegetación (Sentinel-2)…")
    try:
        info = F8.traer_ndvi(celdas, ventana, desde, args.margen_ndvi, args.nubes, Z_PASO=PASO)
        if info:
            fuentes["ndvi"] = f"Sentinel-2 L2A {info['fecha']} · {info['nubes_pct']:.1f} % nubes"
        else:
            faltan.append("ndvi")
    except Exception as e:  # noqa: BLE001
        print(f"    FALLÓ: {e}")
        faltan.append("ndvi")
    for c in celdas:
        c["prob_ignicion"] = 0
    return celdas, {"fuentes": fuentes, "faltan": faltan}


def leer_grid(ruta: Path) -> list[dict]:
    with open(ruta, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def escribir_csv(ruta: Path, filas: list[dict], cols: list[str]) -> None:
    with open(ruta, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(filas)


def observado(grid_csv: Path, ventana_geojson: Path, anio: int, ev_focos: pd.DataFrame,
              otros: pd.DataFrame, margen: int, umbral: float) -> tuple[list[dict], dict]:
    """La regla de f9d_observado_evento.py, para cualquier zona."""
    importar_rbq()
    from cicatriz_grid import fraccion_por_celda, vecinos
    from rejilla import Rejilla
    raster = raster_mapbiomas(anio)
    if raster is None:
        raise SystemExit(f"No hay ráster de MapBiomas Fuego para {anio}. Ponlo en "
                         "validacion_zonas/datos/mapbiomas/ (nombre con el año).")
    rej = Rejilla.desde_csv(grid_csv)
    frac, _, _ = fraccion_por_celda(raster, rej, ventana_geojson)
    huella = {rej.indice(a, b) for a, b in zip(ev_focos["latitude"], ev_focos["longitude"])}
    zona_ev = vecinos(huella, margen)
    ajenas = vecinos({rej.indice(a, b) for a, b in zip(otros["latitude"], otros["longitude"])}, 1)
    cont = {"observado": 0, "no_quemado": 0, "sin_dato": 0, "otro_incendio": 0, "atribucion_dudosa": 0}
    filas = []
    for (f, c), row in sorted(rej.celdas.items()):
        fr, npx = frac.get((f, c), (None, 0))
        obs, val, motivo = 0, 1, "no_quemado"
        if fr is None:
            val, motivo = 0, "sin_dato"
        elif (f, c) in zona_ev:
            if (f, c) in ajenas and fr >= 0.25:
                val, motivo = 0, "atribucion_dudosa"
            elif fr >= umbral:
                obs, motivo = 1, "observado"
        elif fr >= 0.25:
            val, motivo = 0, "otro_incendio"
        cont[motivo] += 1
        filas.append({"id": row["id"], "fila": f, "columna": c, "lat": row["lat"], "lon": row["lon"],
                      "observado": obs, "fraccion_quemada": "" if fr is None else round(fr, 4),
                      "valido": val, "motivo": motivo, "pixeles_30m": npx})
    meta = {"fuente": f"MapBiomas Fuego Bolivia, anual {anio} ({raster.name})",
            "regla": f"observado = fracción quemada ≥ {umbral} dentro de la huella FIRMS ± {margen} celdas",
            "celdas_huella_firms": len(huella), "celdas_observadas": cont["observado"],
            "area_observada_km2": cont["observado"] * AREA_CELDA_KM2, "celdas_por_motivo": cont,
            "celdas_validas": sum(1 for x in filas if x["valido"] == 1),
            "_uso": "REFERENCIA. No entra al autómata."}
    return filas, meta


def grabar_insumos(grid: list[dict], iniciales: list[dict], evento: dict, n_pasos: int) -> dict:
    """Clima horario y terreno OSM del evento, tomados con los servicios del backend."""
    sys.path.insert(0, str(RAIZ_BACKEND))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("SIPRO_OMITIR_ARRANQUE", "1")
    os.environ.setdefault("PRECALENTAR", "false")
    os.environ.setdefault("SUPABASE_URL", "http://127.0.0.1:1")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "no-se-usa")
    import django
    django.setup()
    from api.servicios import meteo, terreno

    por = {(int(c["fila"]), int(c["columna"])): c for c in grid}
    c0 = por[(iniciales[0]["fila"], iniciales[0]["columna"])]
    lat0, lon0 = float(c0["lat"]), float(c0["lon"])
    serie_amb = serie_vto = None
    barreras, resistencia = [], {}
    try:
        serie_amb = meteo.obtener_serie_ambiental_historica(lat0, lon0, evento["fecha_inicio"], evento["fecha_fin"])
        print(f"  clima ERA5 horario: {len(serie_amb)} h")
    except Exception as e:  # noqa: BLE001
        print(f"  sin clima horario ({type(e).__name__}: {e}). Sin ciclo diurno, el modelo sobreestima.")
    try:
        serie_vto = meteo.obtener_serie_viento_historica(lat0, lon0, evento["inicio_utc"], evento["fin_utc"])
        print(f"  viento ERA5 horario: {len(serie_vto)} h")
    except Exception as e:  # noqa: BLE001
        print(f"  sin viento horario ({type(e).__name__}): se usa el del grid")
    try:
        celdas = [{**c, "lat": float(c["lat"]), "lon": float(c["lon"]),
                   "fila": int(c["fila"]), "columna": int(c["columna"])} for c in grid]
        bbox = {"sur": min(c["lat"] for c in celdas), "norte": max(c["lat"] for c in celdas),
                "oeste": min(c["lon"] for c in celdas), "este": max(c["lon"] for c in celdas)}
        osm = terreno.obtener_terreno_osm(bbox, celdas)
        barreras, resistencia = sorted(osm["barreras"]), osm["resistencia"]
        print(f"  OSM: {len(barreras)} barreras · {len(resistencia)} celdas con resistencia")
    except Exception as e:  # noqa: BLE001
        print(f"  sin OSM ({type(e).__name__}: {e}): sin ríos ni caminos que frenen el frente")
    return {
        "_documento": "Insumos exactos del evento para repetir la validación sin red.",
        "_generado_por": "validacion_zonas/python/p2_preparar_evento.py",
        "parametros_base": {
            "focos_iniciales": [{"fila": f["fila"], "columna": f["columna"]} for f in iniciales],
            "foco_fila": iniciales[0]["fila"], "foco_columna": iniciales[0]["columna"],
            "num_iteraciones": n_pasos, "minutos_por_iteracion": MINUTOS_POR_ITERACION,
            "multiplicador_viento": 1.0, "delta_humedad": 0.0, "delta_temperatura_c": 0.0,
            "inicio_utc": evento["inicio_utc"],
        },
        "serie_ambiental": serie_amb, "serie_viento": serie_vto,
        "barreras_extra": barreras, "resistencia_extra": resistencia,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--municipio", required=True)
    ap.add_argument("--departamento", default=None)
    ap.add_argument("--anio", type=int, required=True)
    ap.add_argument("--evento", required=True, help="Id de eventos.json, p. ej. E004")
    ap.add_argument("--rol", choices=["calibracion", "validacion"], default=None)
    ap.add_argument("--margen-km", type=float, default=8.0)
    ap.add_argument("--margen-celdas", type=int, default=2, help="Huella FIRMS ± celdas (f9d)")
    ap.add_argument("--umbral", type=float, default=0.50, help="Fracción quemada para 'observado'")
    ap.add_argument("--horas-iniciales", type=float, default=6.0)
    ap.add_argument("--grid", default=None, help="Reutilizar un grid ya construido")
    ap.add_argument("--insumos", default=None, help="Reutilizar insumos ya grabados (JSON)")
    ap.add_argument("--margen-ndvi", type=int, default=45)
    ap.add_argument("--nubes", type=float, default=40.0)
    ap.add_argument("--submuestreo", type=float, default=0.08)
    args = ap.parse_args()

    mun = buscar_municipio(args.municipio, args.departamento)
    P = mun["properties"]
    base = ZONAS / P["id"] / str(args.anio)
    eventos = leer_json(base / "eventos.json")
    if not eventos:
        print(f"No hay {base / 'eventos.json'}. Ejecuta antes p1_focos_anio.py.")
        return 1
    ev = next((e for e in eventos["eventos"] if e["evento_id"] == args.evento.upper()), None)
    if not ev:
        print(f"No existe {args.evento} en {P['nombre']} {args.anio}.")
        return 1

    rol = rol_de(P["id"])
    if rol is None:
        if not args.rol:
            print("Este municipio aún no tiene rol. Indica --rol calibracion o --rol validacion.")
            print("Es definitivo: un municipio de validación no se usa nunca para calibrar.")
            return 2
        asignar_rol(P["id"], args.rol)
        rol = args.rol
    elif args.rol and args.rol != rol:
        print(f"{P['nombre']} ya es de '{rol}'. No se cambia.")
        return 2

    salida = base / ev["evento_id"]
    salida.mkdir(parents=True, exist_ok=True)
    focos = pd.read_csv(base / "focos.csv", parse_dates=["fecha_hora_utc"])
    focos["evento"] = focos["evento"].fillna("")
    fe = focos[focos["evento"] == ev["evento_id"]].sort_values("fecha_hora_utc")
    ini, fin = fe["fecha_hora_utc"].min(), fe["fecha_hora_utc"].max()
    margen_t = pd.Timedelta(days=3)
    otros = focos[(focos["evento"] != "") &
                  ((focos["fecha_hora_utc"] > fin + margen_t) | (focos["fecha_hora_utc"] < ini - margen_t))]

    print("=" * 74)
    print(f"PAQUETE · {P['nombre']} {args.anio} · {ev['evento_id']} · rol {rol.upper()}")
    print("=" * 74)
    print(f"{len(fe)} detecciones · {ini:%Y-%m-%d %H:%M} → {fin:%Y-%m-%d %H:%M} · {ev['verificacion']}")

    # --- 1-2. Ventana y grid -------------------------------------------------
    prefijo = slug(P["nombre"]).replace("-", "")[:3].upper()
    desde, hasta = ini.strftime("%Y-%m-%d"), fin.strftime("%Y-%m-%d")
    info_grid = {}
    if args.grid:
        shutil.copyfile(args.grid, salida / "grid.csv")
        grid = leer_grid(salida / "grid.csv")
        lats, lons = [float(c["lat"]) for c in grid], [float(c["lon"]) for c in grid]
        ventana = Ventana(min(lons), min(lats), max(lons), max(lats), prefijo, P["nombre"])
        info_grid = {"fuentes": {"grid": f"reutilizado: {Path(args.grid).name}"}, "faltan": []}
        print(f"\nGrid reutilizado: {len(grid):,} celdas")
    else:
        ventana = Ventana.alrededor(fe["latitude"].tolist(), fe["longitude"].tolist(),
                                    args.margen_km, prefijo, P["nombre"])
        print(f"\nGrid de la ventana (± {args.margen_km} km):")
        celdas, info_grid = construir_grid(ventana, desde, hasta, args)
        escribir_csv(salida / "grid.csv", celdas, COLS_GRID)
        grid = leer_grid(salida / "grid.csv")
        if info_grid["faltan"]:
            print(f"  ATENCIÓN: faltan {', '.join(info_grid['faltan'])}. El paquete queda incompleto.")
    escribir_json(salida / "ventana.geojson", ventana.geojson(), compacto=True)

    # --- 3. Observado --------------------------------------------------------
    print("\nCicatriz observada (MapBiomas Fuego):")
    filas_obs, meta_obs = observado(salida / "grid.csv", salida / "ventana.geojson", args.anio,
                                    fe, otros, args.margen_celdas, args.umbral)
    escribir_csv(salida / "observado.csv", filas_obs,
                 ["id", "fila", "columna", "lat", "lon", "observado", "fraccion_quemada",
                  "valido", "motivo", "pixeles_30m"])
    print(f"  {meta_obs['celdas_observadas']} celdas quemadas = {meta_obs['area_observada_km2']:.2f} km² · "
          f"{meta_obs['celdas_validas']:,} evaluables")
    if meta_obs["celdas_observadas"] < 10:
        print("  ATENCIÓN: menos de 10 celdas observadas. Las métricas saldrán muy inestables.")

    # --- 4. Focos iniciales --------------------------------------------------
    from rejilla import Rejilla
    rej = Rejilla.desde_csv(salida / "grid.csv")
    prim = fe[fe["fecha_hora_utc"] <= ini + pd.Timedelta(hours=args.horas_iniciales)]
    iniciales, filas_ini = [], []
    for _, r in prim.iterrows():
        k = rej.celda(r["latitude"], r["longitude"])
        if k is None:
            continue
        filas_ini.append({"fecha": r["fecha_hora_utc"].strftime("%Y-%m-%d"),
                          "hora": r["fecha_hora_utc"].strftime("%H:%M"),
                          "lat": r["latitude"], "lon": r["longitude"], "frp": r.get("frp"),
                          "sensor": r["fuente_firms"], "nivel": r["nivel"],
                          "grid_id": rej.id_de(*k), "fila": k[0], "columna": k[1]})
        if {"fila": k[0], "columna": k[1]} not in iniciales:
            iniciales.append({"fila": k[0], "columna": k[1]})
    if not iniciales:
        print("Ningún foco inicial cae dentro del grid.")
        return 1
    escribir_csv(salida / "focos_iniciales.csv", filas_ini,
                 ["fecha", "hora", "lat", "lon", "frp", "sensor", "nivel", "grid_id", "fila", "columna"])
    print(f"\nFocos iniciales (primeras {args.horas_iniciales:.0f} h): {len(filas_ini)} detecciones → "
          f"{len(iniciales)} celdas")

    # --- 5. Evento e insumos -------------------------------------------------
    dur_h = (fin - ini).total_seconds() / 3600
    evento = {
        "municipio": P["nombre"], "departamento": P["departamento"], "anio": args.anio,
        "evento_id": ev["evento_id"], "fecha_inicio": desde, "fecha_fin": hasta,
        "inicio_utc": ini.isoformat(), "fin_utc": fin.isoformat(), "duracion_h": round(dur_h, 1),
        "numero_focos": int(len(fe)), "sensor_principal": fe["fuente_firms"].mode().iloc[0],
        "frp_total_mw": ev.get("frp_total_mw"), "frp_max_mw": ev.get("frp_max_mw"),
        "extension_km": ev.get("extension_km"), "centro": ev["centro"],
        "criterio_seleccion": "Elegido en p2 entre los eventos de eventos.json (orden propuesto: "
                              "detecciones y compacidad).",
        "parametros_agrupamiento": eventos["agrupamiento"],
    }
    escribir_json(salida / "evento.json", evento)
    n_pasos = max(int(round(max(dur_h, 6.0) * 60 / MINUTOS_POR_ITERACION)), 1)
    if args.insumos:
        ins = json.loads(Path(args.insumos).read_text(encoding="utf-8"))
        ins["parametros_base"].update({"focos_iniciales": iniciales, "foco_fila": iniciales[0]["fila"],
                                       "foco_columna": iniciales[0]["columna"], "num_iteraciones": n_pasos,
                                       "inicio_utc": evento["inicio_utc"]})
        print(f"\nInsumos reutilizados: {Path(args.insumos).name}")
    else:
        print("\nInsumos externos del evento:")
        ins = grabar_insumos(grid, iniciales, evento, n_pasos)
    escribir_json(salida / "insumos.json", ins, compacto=True)

    niveles = fe["nivel"].value_counts().to_dict()
    paquete = {
        "id": f"{P['id']}-{args.anio}-{ev['evento_id']}",
        "municipio": {k: P[k] for k in ("id", "nombre", "departamento", "provincia", "area_km2")},
        "anio": args.anio, "rol": rol, "evento": evento,
        "verificacion": {"nivel_evento": ev["verificacion"], "focos_por_nivel": niveles,
                         "fraccion_confirmada": ev.get("fraccion_confirmada")},
        "ventana": {"bbox": [round(v, 5) for v in ventana.bbox], "celdas": len(grid),
                    "margen_km": None if args.grid else args.margen_km},
        "grid": info_grid, "observado": meta_obs,
        "horizonte_h": round(n_pasos * MINUTOS_POR_ITERACION / 60, 2),
        "insumos": {"horas_clima": len(ins.get("serie_ambiental") or []),
                    "horas_viento": len(ins.get("serie_viento") or []),
                    "barreras": len(ins.get("barreras_extra") or []),
                    "celdas_resistencia": len(ins.get("resistencia_extra") or {})},
        "fecha_preparacion": datetime.now(timezone.utc).isoformat(),
    }
    escribir_json(salida / "paquete.json", paquete)
    actualizar_indice()
    print(f"\nPaquete listo: {salida}")
    print(f"Siguiente: python p3_validar_evento.py --paquete {paquete['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
