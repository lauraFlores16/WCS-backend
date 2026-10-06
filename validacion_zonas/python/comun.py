"""
Piezas comunes de la validación multizona.

    validacion_zonas/
      datos/municipios_bolivia.geojson   catálogo de los 339 municipios (GeoBolivia)
      datos/mapbiomas/                   rásteres anuales de MapBiomas Fuego (no van al repo)
      zonas/<municipio>/<año>/           focos FIRMS verificados y eventos del año
      zonas/<municipio>/<año>/<evento>/  paquete listo para el autómata
      zonas/indice.json                  qué se procesó, para el backend y la pantalla
      config/particion.json              qué municipios calibran y cuáles solo validan

Rejilla: la MISMA retícula global de 0,0045° que Apolo y Rurrenabaque (origen
alineado a múltiplos del paso), así que las celdas de zonas vecinas encajan.
"""
from __future__ import annotations

import json
import math
import re
import sys
import unicodedata
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ_ZONAS = AQUI.parent                       # validacion_zonas/
RAIZ_BACKEND = RAIZ_ZONAS.parent               # WCS-backend/
RAIZ_PROYECTO = RAIZ_BACKEND.parent            # prototipo-borrador-1/
RBQ_PYTHON = RAIZ_BACKEND / "validacion_rurrenabaque" / "python"

CATALOGO = RAIZ_ZONAS / "datos" / "municipios_bolivia.geojson"
CARPETA_MAPBIOMAS = RAIZ_ZONAS / "datos" / "mapbiomas"
ZONAS = RAIZ_ZONAS / "zonas"
INDICE = ZONAS / "indice.json"
PARTICION = RAIZ_ZONAS / "config" / "particion.json"
CONFIG_CONGELADA = RAIZ_BACKEND / "validacion_rurrenabaque" / "config" / "parametros_ca_congelados.json"

PASO = 0.0045
AREA_CELDA_KM2 = 0.25
M_LAT = 110_574.0

# Carpetas donde ya hay rásteres de MapBiomas Fuego (cubren TODA Bolivia).
CARPETAS_MAPBIOMAS = [
    CARPETA_MAPBIOMAS,
    RAIZ_BACKEND / "validacion_rurrenabaque" / "datos" / "cicatrices",
    RAIZ_PROYECTO / "validacion" / "apolo" / "cicatrices",
]


def slug(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def m_lon(lat: float) -> float:
    return 111_320.0 * math.cos(math.radians(lat))


def importar_rbq():
    """Da acceso a los scripts ya probados de validacion_rurrenabaque/python."""
    if str(RBQ_PYTHON) not in sys.path:
        sys.path.insert(0, str(RBQ_PYTHON))


# ---------------------------------------------------------------------------
# Catálogo de municipios
# ---------------------------------------------------------------------------
def catalogo() -> list[dict]:
    if not CATALOGO.exists():
        raise SystemExit(f"Falta {CATALOGO}. Ejecuta: python p0_catalogo.py")
    return json.loads(CATALOGO.read_text(encoding="utf-8"))["features"]


def buscar_municipio(nombre: str, departamento: str | None = None) -> dict:
    """Busca por nombre (sin tildes ni mayúsculas) o por id (slug)."""
    s, dep = slug(nombre), slug(departamento) if departamento else None
    feats = catalogo()
    cands = [f for f in feats if f["properties"]["id"] == s]
    if not cands:
        cands = [f for f in feats if slug(f["properties"]["nombre"]) == s
                 and (dep is None or slug(f["properties"]["departamento"]) == dep)]
    if not cands:
        cands = [f for f in feats if s in slug(f["properties"]["nombre"])
                 and (dep is None or slug(f["properties"]["departamento"]) == dep)]
    if not cands:
        raise SystemExit(f"No encuentro el municipio «{nombre}»"
                         + (f" en {departamento}" if departamento else "") + ".")
    if len(cands) > 1:
        lista = "\n".join(f"  {f['properties']['id']:<40} {f['properties']['nombre']} "
                          f"({f['properties']['departamento']})" for f in cands)
        raise SystemExit(f"«{nombre}» es ambiguo. Usa el id:\n{lista}")
    return cands[0]


# ---------------------------------------------------------------------------
# Zona rectangular compatible con f8 / f9 (misma interfaz que validacion/zonas.py)
# ---------------------------------------------------------------------------
class Ventana:
    """Rectángulo alineado a la rejilla global, con la interfaz que esperan
    las funciones de f8_construir_grid (celdas_de, traer_meteo, traer_ndvi…).

    El grid del evento NO se recorta al municipio: el fuego no respeta
    límites administrativos y la cicatriz puede cruzarlos.
    """

    def __init__(self, oeste, sur, este, norte, prefijo: str, etiqueta: str = ""):
        self.lat_origen = math.ceil(norte / PASO) * PASO
        self.lon_origen = math.floor(oeste / PASO) * PASO
        self.filas = int(math.ceil((self.lat_origen - sur) / PASO))
        self.columnas = int(math.ceil((este - self.lon_origen) / PASO))
        self.bbox = (self.lon_origen, self.lat_origen - self.filas * PASO,
                     self.lon_origen + self.columnas * PASO, self.lat_origen)
        self.prefijo = prefijo
        self.etiqueta = etiqueta or prefijo
        self.centroide = ((self.bbox[1] + self.bbox[3]) / 2, (self.bbox[0] + self.bbox[2]) / 2)
        ns, eo = self.tam_celda_m()
        self.area_km2 = (self.filas + 1) * (self.columnas + 1) * ns * eo / 1e6

    @classmethod
    def alrededor(cls, lats, lons, margen_km: float, prefijo: str, etiqueta=""):
        lat_c = sum(lats) / len(lats)
        dlat = margen_km * 1000 / M_LAT
        dlon = margen_km * 1000 / m_lon(lat_c)
        return cls(min(lons) - dlon, min(lats) - dlat, max(lons) + dlon, max(lats) + dlat,
                   prefijo, etiqueta)

    def coordenada(self, fila, columna):
        return (round(self.lat_origen - fila * PASO, 6), round(self.lon_origen + columna * PASO, 6))

    def celda(self, lat, lon):
        return (round((self.lat_origen - lat) / PASO), round((lon - self.lon_origen) / PASO))

    def contiene(self, lat, lon):
        return self.bbox[1] <= lat <= self.bbox[3] and self.bbox[0] <= lon <= self.bbox[2]

    def id_celda(self, fila, columna):
        return f"{self.prefijo}-{fila:03d}-{columna:03d}"

    def tam_celda_m(self):
        return PASO * M_LAT, PASO * m_lon(self.centroide[0])

    def geojson(self) -> dict:
        o, s, e, n = self.bbox
        return {"type": "FeatureCollection", "features": [{
            "type": "Feature", "properties": {"nombre": self.etiqueta},
            "geometry": {"type": "Polygon",
                         "coordinates": [[[o, s], [e, s], [e, n], [o, n], [o, s]]]}}]}


# ---------------------------------------------------------------------------
# MapBiomas Fuego
# ---------------------------------------------------------------------------
def raster_mapbiomas(anio: int | str) -> Path | None:
    for carpeta in CARPETAS_MAPBIOMAS:
        if not carpeta.exists():
            continue
        for p in sorted(carpeta.glob("*.img")) + sorted(carpeta.glob("*.tif")):
            if re.search(rf"(?<!\d){anio}(?!\d)", p.stem):
                return p
    return None


def anios_mapbiomas() -> list[int]:
    anios = set()
    for carpeta in CARPETAS_MAPBIOMAS:
        if carpeta.exists():
            for p in list(carpeta.glob("*.img")) + list(carpeta.glob("*.tif")):
                m = re.search(r"(?<!\d)(19[89]\d|20\d\d)(?!\d)", p.stem)
                if m:
                    anios.add(int(m.group(1)))
    return sorted(anios)


# ---------------------------------------------------------------------------
# Índice y partición calibración / validación
# ---------------------------------------------------------------------------
def leer_json(ruta: Path, defecto=None):
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else defecto


def escribir_json(ruta: Path, datos, compacto: bool = False) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    txt = (json.dumps(datos, ensure_ascii=False, separators=(",", ":"), default=str) if compacto
           else json.dumps(datos, indent=2, ensure_ascii=False, default=str))
    ruta.write_text(txt, encoding="utf-8")


def rol_de(municipio_id: str) -> str | None:
    """'calibracion', 'validacion' o None si aún no se asignó."""
    p = leer_json(PARTICION, {})
    for rol in ("calibracion", "validacion"):
        if municipio_id in (p.get(rol) or []):
            return rol
    return None


def asignar_rol(municipio_id: str, rol: str) -> None:
    """La asignación es definitiva: mover un municipio de validación a
    calibración después de haber visto sus métricas invalidaría la
    validación externa. Por eso se rechaza el cambio."""
    if rol not in ("calibracion", "validacion"):
        raise SystemExit("El rol tiene que ser 'calibracion' o 'validacion'.")
    p = leer_json(PARTICION, None) or {
        "_regla": ("Cada municipio pertenece a UN solo conjunto y no se mueve. Los de "
                   "'validacion' nunca entran a la calibración: es lo que mantiene la "
                   "validación externa independiente."),
        "calibracion": ["apolo-la-paz"], "validacion": ["puerto-menor-de-rurrenabaque-beni"],
    }
    actual = next((r for r in ("calibracion", "validacion") if municipio_id in (p.get(r) or [])), None)
    if actual and actual != rol:
        raise SystemExit(f"{municipio_id} ya está en '{actual}'. No se puede pasar a '{rol}': "
                         "cambiarlo después de verlo invalidaría la validación.")
    if actual is None:
        p.setdefault(rol, []).append(municipio_id)
    escribir_json(PARTICION, p)


def actualizar_indice() -> dict:
    """Recorre zonas/ y escribe zonas/indice.json."""
    municipios = {}
    for res in sorted(ZONAS.glob("*/*/resumen_anio.json")):
        r = leer_json(res)
        mid = r["municipio"]["id"]
        m = municipios.setdefault(mid, {**r["municipio"], "rol": rol_de(mid), "anios": {}})
        m["anios"][str(r["anio"])] = {
            "focos": r["focos"]["total"], "focos_verificados": r["focos"]["verificados"],
            "eventos": r["eventos"]["total"], "fecha_proceso": r.get("fecha_proceso"),
            "paquetes": [],
        }
    for paq in sorted(ZONAS.glob("*/*/*/paquete.json")):
        p = leer_json(paq)
        mid, anio = p["municipio"]["id"], str(p["anio"])
        if mid in municipios and anio in municipios[mid]["anios"]:
            met = leer_json(paq.parent / "resultados" / "metricas_validacion.json", {})
            municipios[mid]["anios"][anio]["paquetes"].append({
                "id": p["id"], "evento_id": p["evento"]["evento_id"],
                "inicio_utc": p["evento"].get("inicio_utc"), "focos": p["evento"].get("numero_focos"),
                "verificacion": p.get("verificacion", {}).get("nivel_evento"),
                "iou": met.get("iou"), "f1": met.get("f1"),
            })
    indice = {"_generado_por": "validacion_zonas/python/comun.py",
              "particion": leer_json(PARTICION, {}), "municipios": municipios}
    escribir_json(INDICE, indice)
    return indice
