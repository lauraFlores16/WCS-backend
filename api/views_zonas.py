"""
VISTAS DE LA VALIDACIÓN MULTIZONA — todo lo que antes era p1/p2/p3 en la
terminal, lanzado desde el dashboard y guardado en Supabase.

    GET  /api/validacion/indice                    municipios procesados, partición, MapBiomas
    GET  /api/validacion/mapbiomas                 años de cicatriz disponibles y su cobertura
    POST /api/validacion/mapbiomas                 subir el ráster de un año (multipart)
    POST /api/validacion/zonas                     {municipio_id, anio} → descargar y procesar FIRMS
    GET  /api/validacion/zonas/<mid>/<anio>        focos y eventos de un municipio-año
    POST /api/validacion/zonas/<mid>/<anio>/paquete  {evento_id, rol} → paquete + validación oficial
    POST /api/validacion/paquetes/<pid>/oficial    repetir la validación oficial (30 rep.)
    GET  /api/validacion/trabajos[/<id>]           estado de los trabajos en segundo plano
"""
from __future__ import annotations

import base64
import re
import tempfile
from pathlib import Path

from django.http import HttpRequest
from django.views.decorators.http import require_http_methods

from . import auth
from .almacen import db
from .utils import bien, cuerpo_json, error_simple, mal

ID_OK = re.compile(r"^[a-z0-9-]{1,120}$")


def _usuario(request) -> str | None:
    u = auth.verificar_peticion(request) or {}
    return u.get("nombre") or u.get("email")


def _bitacora(request, accion, detalle):
    try:
        db.registrar_bitacora({"usuario": _usuario(request), "accion": accion, "detalle": detalle, "tipo": "data"})
    except Exception as e:  # noqa: BLE001
        print(f"[validacion] bitácora: {e}")


# ---------------------------------------------------------------------------
@require_http_methods(["GET"])
def indice(request: HttpRequest):
    denegado = auth.exigir_permiso(request, "ver_simulaciones")
    if denegado:
        return denegado
    from .zonas import mapbiomas
    try:
        part = db.vz_particion()
        rol = {m: r for r, lista in part.items() for m in lista}
        municipios: dict = {}
        for z in db.vz_listar_zonas():
            r = z.get("resumen") or {}
            m = municipios.setdefault(z["municipio_id"], {**(z.get("municipio") or {}),
                                                          "rol": rol.get(z["municipio_id"]), "anios": {}})
            m["anios"][str(z["anio"])] = {
                "focos": (r.get("focos") or {}).get("total"), "focos_verificados": (r.get("focos") or {}).get("verificados"),
                "eventos": (r.get("eventos") or {}).get("total"), "fecha_proceso": r.get("fecha_proceso"),
                "creado_por": z.get("creado_por"),
            }
        anios_mb = {str(k): v for k, v in mapbiomas.disponibles().items()}
        return bien({"municipios": municipios, "particion": part, "mapbiomas": anios_mb,
                     "trabajos": db.vz_listar_trabajos(10)})
    except Exception as e:  # noqa: BLE001
        return mal(e)


# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def mapbiomas_vista(request: HttpRequest):
    from .zonas import mapbiomas, trabajos
    if request.method == "GET":
        denegado = auth.exigir_permiso(request, "ver_simulaciones")
        if denegado:
            return denegado
        return bien({str(k): v for k, v in mapbiomas.disponibles().items()})

    denegado = auth.exigir_permiso(request, "ejecutar_simulacion")
    if denegado:
        return denegado
    try:
        anio = int(request.POST.get("anio", ""))
    except ValueError:
        return error_simple("Indica el año del ráster.", 400)
    archivo = request.FILES.get("archivo")
    if not archivo:
        return error_simple("Falta el archivo del ráster (GeoTIFF o IMG).", 400)
    if archivo.size > 400 * 1024 * 1024:
        return error_simple("El ráster pasa de 400 MB. Recórtalo a la región que vas a validar.", 413)
    tmp = Path(tempfile.mkdtemp()) / Path(archivo.name).name
    with tmp.open("wb") as fh:
        for trozo in archivo.chunks():
            fh.write(trozo)
    usuario = _usuario(request)

    def trabajo(avance):
        avance(0.1, f"Agregando {archivo.name} a la rejilla de 500 m…")
        datos, cob = mapbiomas.agregar_raster(tmp, archivo.name)
        if not cob.get("celdas_con_dato"):
            raise ValueError("El ráster no tiene ningún píxel con dato (0 o 1). ¿Es MapBiomas Fuego anual?")
        avance(0.8, f"Guardando ({len(datos) // 1024} KB)…")
        db.vz_guardar_mapbiomas({"anio": anio, "npz_b64": base64.b64encode(datos).decode("ascii"),
                                 "fuente": archivo.name, "cobertura": cob, "subido_por": usuario})
        mapbiomas.olvidar(anio)
        try:
            tmp.unlink()
        except OSError:
            pass
        return {"mensaje": f"Cicatriz {anio}: {cob['km2_quemados']} km² quemados en "
                           f"{cob['km2_con_dato']} km² con dato", "cobertura": cob}

    _bitacora(request, "Subió cicatriz MapBiomas", f"{anio} · {archivo.name}")
    return bien(trabajos.lanzar("mapbiomas", f"mapbiomas-{anio}", trabajo, usuario))


# ---------------------------------------------------------------------------
@require_http_methods(["POST"])
def zona_procesar(request: HttpRequest):
    """Lo que hacía p1_focos_anio.py: FIRMS del año → verificación → eventos."""
    denegado = auth.exigir_permiso(request, "ejecutar_simulacion")
    if denegado:
        return denegado
    from .zonas import comun, firms_hist, mapbiomas, trabajos
    c = cuerpo_json(request)
    mid = str(c.get("municipio_id", ""))
    try:
        anio = int(c.get("anio"))
    except (TypeError, ValueError):
        return error_simple("Año no válido.", 400)
    if not ID_OK.match(mid):
        return error_simple("Municipio no válido.", 400)
    try:
        mun = comun.municipio(mid)
    except KeyError as e:
        return error_simple(str(e).strip("'\""), 404)
    from datetime import date
    if not 2012 <= anio < date.today().year + 1:
        return error_simple("FIRMS VIIRS tiene datos desde 2012.", 400)
    fuentes = [f for f in (c.get("fuentes") or firms_hist.FUENTES_DEFECTO)
               if f in ("VIIRS_SNPP_SP", "VIIRS_NOAA20_SP", "MODIS_SP")] or firms_hist.FUENTES_DEFECTO
    existe = db.vz_leer_zona(f"{mid}-{anio}", con_focos=False)
    if existe and any(p["zona_id"] == existe["id"] for p in db.vz_listar_paquetes()):
        return error_simple("Ese municipio y año ya tienen paquetes de validación: no se reprocesa "
                            "para no cambiar los eventos sobre los que se validó.", 409)
    usuario = _usuario(request)

    def trabajo(avance):
        P = mun["properties"]
        avance(0.01, f"Descargando FIRMS {anio} de {P['nombre']} ({', '.join(fuentes)})…")
        filas, bitacora = firms_hist.descargar(anio, P["bbox"], fuentes,
                                               lambda f, m: avance(0.02 + 0.85 * f, m))
        avance(0.9, f"{len(filas):,} detecciones en el recuadro. Verificando y agrupando…")
        r = firms_hist.procesar(mun, anio, filas, mapbiomas.cargar(anio))
        r["resumen"]["descarga"] = {"fuentes": fuentes, "tramos": len(bitacora),
                                    "fallidos": sum(1 for b in bitacora if b.get("error"))}
        db.vz_guardar_zona({
            "id": f"{mid}-{anio}", "municipio_id": mid, "anio": anio, "municipio": r["resumen"]["municipio"],
            "resumen": r["resumen"], "eventos": r["eventos"], "limite": {"type": "FeatureCollection", "features": [mun]},
            "focos_gz": comun.gz_texto(comun.csv_texto(r["focos"], firms_hist.COLUMNAS_FOCOS)),
            "creado_por": usuario,
        })
        res = r["resumen"]
        return {"mensaje": f"{res['focos']['total']:,} detecciones en el municipio, "
                           f"{res['focos']['verificados']:,} verificadas · {res['eventos']['total']} eventos",
                "zona_id": f"{mid}-{anio}"}

    _bitacora(request, "Procesó focos FIRMS", f"{mid} · {anio}")
    return bien(trabajos.lanzar("zona", f"zona-{mid}-{anio}", trabajo, usuario))


@require_http_methods(["GET"])
def zona_detalle(request: HttpRequest, mid: str, anio: int):
    denegado = auth.exigir_permiso(request, "ver_simulaciones")
    if denegado:
        return denegado
    if not ID_OK.match(mid):
        return error_simple("Municipio no válido", 400)
    from .zonas import comun
    z = db.vz_leer_zona(f"{mid}-{anio}")
    if not z:
        return error_simple("Ese municipio y año no se han procesado todavía.", 404)
    focos = []
    for r in comun.leer_csv_texto(comun.texto_gz(z["focos_gz"])):
        focos.append({"lat": float(r["latitude"]), "lon": float(r["longitude"]), "fecha": r.get("fecha_hora_utc"),
                      "nivel": r.get("nivel"), "evento": r.get("evento") or None, "frp": r.get("frp") or None,
                      "fuente": r.get("fuente_firms")})
    return bien({"resumen": z["resumen"], "eventos": z["eventos"], "limite": z.get("limite"), "focos": focos})


# ---------------------------------------------------------------------------
def _oficial(pid: str, avance) -> dict:
    from .motor import validacion_externa as V
    V.olvidar(pid)
    res = V.ejecutar(30, True, lambda f, rep: avance(0.85 + 0.14 * f, f"Validación oficial: semilla {rep['semilla']}/30"),
                     pid=pid)
    V.guardar_oficial(pid, res)
    m = res["metricas"]
    return {"mensaje": f"IoU {m['iou']} · F1 {m['f1']} · {m['comportamiento'].lower()}", "paquete": pid,
            "iou": m["iou"], "f1": m["f1"]}


@require_http_methods(["POST"])
def paquete_preparar(request: HttpRequest, mid: str, anio: int):
    """Lo que hacían p2 + p3: paquete del evento y su validación oficial."""
    denegado = auth.exigir_permiso(request, "ejecutar_simulacion")
    if denegado:
        return denegado
    from .zonas import mapbiomas, paquete, trabajos
    c = cuerpo_json(request)
    eid = str(c.get("evento_id", "")).upper()
    rol = c.get("rol")
    if not ID_OK.match(mid) or not re.match(r"^E\d{3}$", eid):
        return error_simple("Municipio o evento no válido.", 400)
    z = db.vz_leer_zona(f"{mid}-{anio}")
    if not z:
        return error_simple("Procesa antes los focos de ese municipio y año.", 404)
    actual = db.vz_particion()
    rol_actual = next((r for r, lista in actual.items() if mid in lista), None)
    if rol_actual is None and rol not in ("calibracion", "validacion"):
        return error_simple("Elige si el municipio es de calibración o de validación (es definitivo).", 400)
    if rol_actual and rol and rol != rol_actual:
        return error_simple(f"El municipio ya es de '{rol_actual}' y no se puede cambiar.", 409)
    if mapbiomas.cargar(anio) is None:
        return error_simple(f"No hay cicatriz de MapBiomas para {anio}. Súbela primero.", 409)
    usuario = _usuario(request)

    def trabajo(avance):
        rol_final = db.vz_asignar_rol(mid, rol_actual or rol, usuario)
        avance(0.02, f"Preparando {eid} ({rol_final})…")
        fila = paquete.preparar(z, eid, rol_final, mapbiomas.cargar(anio), avance)
        fila["creado_por"] = usuario
        avance(0.82, "Guardando el paquete…")
        db.vz_guardar_paquete(fila)
        return _oficial(fila["id"], avance)

    _bitacora(request, "Preparó paquete de validación", f"{mid} · {anio} · {eid}")
    return bien(trabajos.lanzar("paquete", f"paquete-{mid}-{anio}-{eid}", trabajo, usuario))


@require_http_methods(["POST"])
def paquete_oficial(request: HttpRequest, pid: str):
    denegado = auth.exigir_permiso(request, "ejecutar_simulacion")
    if denegado:
        return denegado
    from .zonas import trabajos
    if not db.vz_leer_paquete(pid):
        return error_simple("Ese paquete no está en la base.", 404)
    return bien(trabajos.lanzar("oficial", f"oficial-{pid}", lambda av: _oficial(pid, av), _usuario(request)))


@require_http_methods(["GET"])
def trabajos_vista(request: HttpRequest, tid: str | None = None):
    denegado = auth.exigir_permiso(request, "ver_simulaciones")
    if denegado:
        return denegado
    from .zonas import trabajos
    if tid:
        t = trabajos.estado(tid)
        return bien(t) if t else error_simple("No existe ese trabajo.", 404)
    return bien(db.vz_listar_trabajos(20))
