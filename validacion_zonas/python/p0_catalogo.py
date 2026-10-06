"""
P0 — Catálogo de los municipios de Bolivia.

    python p0_catalogo.py

Descarga de geoBoundaries (fuente: GeoBolivia, dominio público) los límites
municipales (ADM3), departamentales (ADM1) y provinciales (ADM2) en su versión
simplificada (error < 100 m, de sobra para celdas de 500 m), les asigna
departamento y provincia por el punto interior y escribe:

    ../datos/municipios_bolivia.geojson                 con geometría (backend)
    ../../../WCS-frontend/public/datos/municipios_bolivia.json   lista ligera (pantalla)

Solo hace falta volver a ejecutarlo si geoBoundaries publica una versión nueva.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from comun import CATALOGO, RAIZ_PROYECTO, escribir_json, slug

URL = ("https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/main/"
       "releaseData/gbOpen/BOL/{n}/geoBoundaries-BOL-{n}_simplified.geojson")


def bajar(nivel: str, cache: Path) -> dict:
    ruta = cache / f"BOL_{nivel}_simplified.geojson"
    if not ruta.exists():
        import requests
        print(f"  descargando {nivel}…")
        r = requests.get(URL.format(n=nivel), timeout=300)
        r.raise_for_status()
        ruta.write_bytes(r.content)
    return json.loads(ruta.read_text(encoding="utf-8"))


def redondear(geom: dict, nd: int = 5) -> dict:
    """5 decimales ≈ 1 m: sobra precisión y el archivo pesa la mitad."""
    def r(c):
        return [r(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], nd), round(c[1], nd)]
    return {"type": geom["type"], "coordinates": r(geom["coordinates"])}


def main() -> int:
    from pyproj import Geod
    from shapely.geometry import mapping, shape

    cache = CATALOGO.parent / "_geoboundaries"
    cache.mkdir(parents=True, exist_ok=True)
    adm3, adm2, adm1 = (bajar(n, cache) for n in ("ADM3", "ADM2", "ADM1"))
    deps = [(f["properties"]["shapeName"], shape(f["geometry"])) for f in adm1["features"]]
    provs = [(f["properties"]["shapeName"], shape(f["geometry"])) for f in adm2["features"]]
    geod = Geod(ellps="WGS84")

    def dentro(punto, capas):
        for nombre, g in capas:
            if g.contains(punto):
                return nombre
        return min(capas, key=lambda x: x[1].distance(punto))[0]

    feats, ids = [], set()
    for f in adm3["features"]:
        g = shape(f["geometry"])
        p = g.representative_point()
        nombre = f["properties"]["shapeName"].strip()
        dep, prov = dentro(p, deps), dentro(p, provs)
        mid = slug(f"{nombre}-{dep}")
        if mid in ids:
            mid = slug(f"{nombre}-{prov}-{dep}")
        ids.add(mid)
        area = abs(geod.geometry_area_perimeter(g)[0]) / 1e6
        o, s, e, n = g.bounds
        feats.append({"type": "Feature", "properties": {
            "id": mid, "nombre": nombre, "departamento": dep, "provincia": prov,
            "area_km2": round(area, 1), "bbox": [round(v, 5) for v in (o, s, e, n)],
            "centro": [round(p.y, 5), round(p.x, 5)], "geoboundaries_id": f["properties"]["shapeID"],
        }, "geometry": redondear(mapping(g))})

    feats.sort(key=lambda x: (x["properties"]["departamento"], x["properties"]["nombre"]))
    escribir_json(CATALOGO, {
        "type": "FeatureCollection",
        "_fuente": "geoBoundaries gbOpen BOL ADM3 (simplificado) · origen GeoBolivia · dominio público",
        "features": feats,
    }, compacto=True)
    ligero = [f["properties"] for f in feats]
    destino = RAIZ_PROYECTO / "WCS-frontend" / "public" / "datos" / "municipios_bolivia.json"
    if destino.parent.exists():
        destino.write_text(json.dumps(ligero, ensure_ascii=False), encoding="utf-8")
        print(f"Escrito: {destino}")
    print(f"Escrito: {CATALOGO}  ({len(feats)} municipios)")
    por_dep = {}
    for p in ligero:
        por_dep[p["departamento"]] = por_dep.get(p["departamento"], 0) + 1
    for d, n in sorted(por_dep.items()):
        print(f"  {d:<12}{n:>4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
