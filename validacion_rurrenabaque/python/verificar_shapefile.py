"""
============================================================================
FASE 1 — VERIFICACIÓN DEL LÍMITE MUNICIPAL DE RURRENABAQUE
============================================================================
Ruta: validacion_rurrenabaque/python/verificar_shapefile.py

QUÉ HACE
    Lee Mun_RBQ.shp tal como lo entregó la institución y comprueba, sin
    modificar el polígono, todo lo que hay que saber antes de subirlo a GEE:
    CRS declarado, validez topológica, atributos, área, perímetro, extensión
    y encuadre en coordenadas geográficas. Escribe además la versión WGS84
    en GeoJSON, que es lo que consume el resto del flujo.

POR QUÉ HACE FALTA
    Tres cosas se rompen callando si no se verifican antes:

      1. El CRS. El shapefile viene en UTM 19S (metros). Si se sube a GEE
         o se compara contra FIRMS sin reproyectar, el polígono aparece en
         mitad del Atlántico y todo lo demás sale vacío sin dar error.

      2. El área. El campo `sup_ha` del .dbf trae 250.000 ha, que es un
         valor nominal redondeado. El área real de la geometría es otra.
         Si se cita la del .dbf en la tesis, el error relativo de área de
         la validación arrastra ese sesgo desde el principio.

      3. La validez topológica. Un polígono con auto-intersecciones da
         áreas negativas o dobles al intersectarlo con el grid, y el fallo
         no se ve: solo salen métricas raras al final.

    El área se calcula por DOS vías independientes —planimétrica sobre el
    UTM declarado y geodésica sobre el elipsoide WGS84—. Si coinciden, la
    proyección declarada en el .prj es la correcta. Si no coincidieran,
    el .prj estaría mintiendo y habría que averiguar el CRS real antes de
    seguir.

CÓMO SE EJECUTA
    pip install pyshp pyproj shapely
    python verificar_shapefile.py --shp ../datos/Mun_RBQ.shp

    (Coloca los 8 archivos del shapefile —shp, shx, dbf, prj, cpg, sbn,
     sbx, xml— juntos en validacion_rurrenabaque/datos/.)

NO MODIFICA el polígono original. Solo lee y deriva.
============================================================================
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import shapefile                      # pyshp
from pyproj import CRS, Geod, Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform as sh_transform
from shapely.prepared import prep

# Convención del grid del proyecto. NO se toca: es la misma que usa el grid
# de Apolo (frontend/public/datos/grid.csv). Si aquí se pusiera otro paso, las
# métricas de Rurrenabaque dejarían de ser comparables con las de Apolo.
PASO_GRID_GRADOS = 0.0045


def verificar(ruta_shp: Path, salida_geojson: Path | None) -> dict:
    sf = shapefile.Reader(str(ruta_shp))

    # --- 1. Atributos -------------------------------------------------------
    campos = [f[0] for f in sf.fields[1:]]
    registros = [dict(zip(campos, r)) for r in sf.records()]

    print("=" * 74)
    print("FASE 1 — VERIFICACIÓN DE Mun_RBQ.shp")
    print("=" * 74)
    print(f"Tipo de geometría : {sf.shapeTypeName}")
    print(f"Registros         : {len(sf)}")
    if len(sf) != 1:
        print("  !! AVISO: se esperaba UN único polígono municipal.")
    print("\nAtributos del .dbf:")
    for k, v in registros[0].items():
        print(f"  {k:<12} = {v}")

    # --- 2. CRS -------------------------------------------------------------
    crs_utm = CRS.from_wkt(ruta_shp.with_suffix(".prj").read_text())
    print(f"\nCRS declarado     : {crs_utm.name}")
    epsg = crs_utm.to_epsg()
    print(f"EPSG              : {epsg}")
    print(f"Unidad            : {crs_utm.axis_info[0].unit_name}")

    # --- 3. Geometría -------------------------------------------------------
    forma = sf.shape(0)
    geom_utm = shape(forma.__geo_interface__)
    print(f"\nVálida (topología): {geom_utm.is_valid}")
    print(f"Anillos           : {len(forma.parts)}")
    print(f"Vértices          : {len(forma.points)}")
    if geom_utm.geom_type == "Polygon":
        print(f"Huecos interiores : {len(geom_utm.interiors)}")
    if not geom_utm.is_valid:
        print("  !! El polígono NO es topológicamente válido. Corregirlo antes")
        print("     de intersectarlo con el grid, o las áreas saldrán mal.")

    # --- 4. Área por dos vías independientes --------------------------------
    area_planimetrica_km2 = geom_utm.area / 1e6

    tr = Transformer.from_crs(crs_utm, CRS.from_epsg(4326), always_xy=True)
    geom_wgs = sh_transform(tr.transform, geom_utm)

    geod = Geod(ellps="WGS84")
    area_geodesica_m2, _ = geod.geometry_area_perimeter(geom_wgs)
    area_geodesica_km2 = abs(area_geodesica_m2) / 1e6

    discrepancia = abs(area_planimetrica_km2 - area_geodesica_km2)
    pct = discrepancia / area_geodesica_km2 * 100

    print(f"\nÁrea planimétrica (UTM {epsg}) : {area_planimetrica_km2:>12,.2f} km²")
    print(f"Área geodésica    (WGS84)      : {area_geodesica_km2:>12,.2f} km²")
    print(f"Discrepancia entre ambas       : {discrepancia:>12,.2f} km²  ({pct:.3f} %)")
    if pct < 0.5:
        print("  → Coinciden. La proyección declarada en el .prj es la correcta.")
    else:
        print("  !! NO coinciden. Revisar el .prj antes de seguir: el CRS declarado")
        print("     probablemente no es el real, y todo lo que venga después estará")
        print("     desplazado.")

    sup_ha_dbf = registros[0].get("sup_ha")
    if sup_ha_dbf:
        print(f"\nsup_ha declarado en el .dbf    : {sup_ha_dbf:>12,.1f} ha "
              f"({sup_ha_dbf/100:,.1f} km²)")
        print(f"ha reales de la geometría      : {area_planimetrica_km2*100:>12,.1f} ha")
        print("  → Para la tesis se usa la CALCULADA, citando la diferencia. El campo")
        print("    del .dbf es un valor nominal redondeado.")

    print(f"\nPerímetro                      : {geom_utm.length/1000:>12,.2f} km")

    # --- 5. Encuadre geográfico --------------------------------------------
    minx, miny, maxx, maxy = geom_wgs.bounds
    c = geom_wgs.centroid
    print("\nBBox WGS84 (EPSG:4326)")
    print(f"  lon: {minx:.6f}  →  {maxx:.6f}")
    print(f"  lat: {miny:.6f}  →  {maxy:.6f}")
    print(f"  cadena para NASA FIRMS: {minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f}")
    print(f"Centroide: lat {c.y:.6f}, lon {c.x:.6f}")

    bx = geom_utm.bounds
    print(f"\nExtensión E-O: {(bx[2]-bx[0])/1000:.1f} km")
    print(f"Extensión N-S: {(bx[3]-bx[1])/1000:.1f} km")

    # --- 6. Dimensionado del grid ------------------------------------------
    # Cuenta las celdas cuyo CENTRO cae dentro del municipio. Ese es el
    # criterio que usará el grid real: una celda pertenece al municipio o no,
    # sin celdas a medias, porque el autómata trabaja con celdas enteras.
    pg = prep(geom_wgs)
    lat0 = math.floor(miny / PASO_GRID_GRADOS) * PASO_GRID_GRADOS
    lon0 = math.floor(minx / PASO_GRID_GRADOS) * PASO_GRID_GRADOS
    nf = int(math.ceil((maxy - lat0) / PASO_GRID_GRADOS))
    nc = int(math.ceil((maxx - lon0) / PASO_GRID_GRADOS))
    dentro = sum(
        1
        for i in range(nf)
        for j in range(nc)
        if pg.contains(Point(lon0 + (j + 0.5) * PASO_GRID_GRADOS,
                             lat0 + (i + 0.5) * PASO_GRID_GRADOS))
    )

    print(f"\nGrid de {PASO_GRID_GRADOS}° (la misma convención que Apolo)")
    print(f"  Rejilla envolvente : {nf} filas × {nc} columnas = {nf*nc:,} celdas")
    print(f"  Dentro del municipio: {dentro:,} celdas ({dentro*25/100:,.0f} km² a 25 ha/celda)")
    print(f"  Origen: lat0={lat0:.6f}  lon0={lon0:.6f}")

    resumen = {
        "atributos": registros[0],
        "crs_epsg": epsg,
        "valida": geom_utm.is_valid,
        "vertices": len(forma.points),
        "area_km2_planimetrica": round(area_planimetrica_km2, 2),
        "area_km2_geodesica": round(area_geodesica_km2, 2),
        "perimetro_km": round(geom_utm.length / 1000, 2),
        "bbox_wgs84": [minx, miny, maxx, maxy],
        "centroide_wgs84": [c.y, c.x],
        "grid": {"paso": PASO_GRID_GRADOS, "lat0": lat0, "lon0": lon0,
                 "filas": nf, "columnas": nc, "celdas_dentro": dentro},
    }

    # --- 7. GeoJSON WGS84 ---------------------------------------------------
    if salida_geojson:
        fc = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": registros[0],
                "geometry": json.loads(json.dumps(geom_wgs.__geo_interface__)),
            }],
        }
        salida_geojson.write_text(json.dumps(fc), encoding="utf-8")
        print(f"\nGeoJSON WGS84 escrito en: {salida_geojson}")

    print("=" * 74)
    return resumen


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shp", default="../datos/Mun_RBQ.shp",
                    help="Ruta a Mun_RBQ.shp (con sus .dbf/.prj/.shx al lado)")
    ap.add_argument("--geojson", default="../datos/Mun_RBQ_wgs84.geojson",
                    help="Salida GeoJSON en WGS84")
    args = ap.parse_args()
    verificar(Path(args.shp), Path(args.geojson) if args.geojson else None)
