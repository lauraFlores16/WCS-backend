"""
============================================================================
VISTAS DE LA API — puerto de backend/rutas/index.js
============================================================================
Un solo archivo porque el proyecto es pequeño y así se ve todo el contrato de
un vistazo (misma decisión que tomó el original en Express).
============================================================================
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from . import auth
from .almacen import db as almacen_db
from .errores import ErrorAPI
from .lib.cache import estado_cache
from .lib.cola import estado_colas
from .motor import sesion_simulacion as sesion_sim
from .motor.alertas_riesgo import calcular_alertas_riesgo
from .motor.calibracion import calibrar
from .motor.escenarios import REFERENCIAS as REFERENCIAS_ESCENARIOS
from .motor.escenarios import listar as catalogo_escenarios
from .motor.parametros import derivar_parametros
from .motor.simulacion import (
    AREA_POR_CELDA_HA,
    ejecutar_simulacion,
    guardar_sesion_como_escenario,
    obtener_simulacion,
    preparar_entorno,
    reconstruir_iteraciones,
)
from .servicios import firms, grid as grid_srv, meteo, terreno
from .utils import bien, cuerpo_json, error_simple, mal


def _num(valor, defecto=None):
    if valor is None:
        return defecto
    try:
        return float(valor)
    except (TypeError, ValueError):
        return defecto


# ===========================================================================
# ESTADO / SALUD
# ===========================================================================
@require_http_methods(["GET"])
def estado(request: HttpRequest):
    try:
        grid = grid_srv.obtener_grid()
        return bien({
            "servicio": "SIPRO FIRE backend (Django)",
            "version": "2.0.0-django",
            "hora": datetime.now(timezone.utc).isoformat(),
            "grid_cargado": bool(grid),
            "celdas": len(grid),
            "firms_configurada": firms.clave_configurada(),
            "almacen": almacen_db.MODO_ALMACEN,
            "colas": estado_colas(),
            "cache": estado_cache(),
        })
    except Exception as e:  # noqa: BLE001
        return mal(e)


# ===========================================================================
# CONFIGURACIÓN DEL SISTEMA
# ===========================================================================
# Lo que enseña la pantalla de Configuración. Antes esa pantalla llevaba los
# valores escritos a mano en el propio JSX —incluido un «Google Earth Engine ·
# Conectado» que no comprobaba nada y que además era falso: las variables de
# GEE están precalculadas dentro de grid.csv, no se consultan en vivo—. Aquí
# se mide todo lo medible y lo que es un dato del proyecto se marca como tal.
@require_http_methods(["GET"])
def configuracion_vista(request: HttpRequest):
    denegado = auth.exigir_permiso(request, "configuracion")
    if denegado:
        return denegado

    try:
        from urllib.parse import urlparse

        from .almacen.supabase import TABLAS_ESPERADAS
        from .motor.automata import CONSTANTES_POR_DEFECTO

        malla = grid_srv.obtener_grid()
        focos = grid_srv.obtener_focos()
        historicos = grid_srv.obtener_historicos()

        # --- Malla: se mide, no se declara -------------------------------
        filas = [c.get("fila") for c in malla if c.get("fila") is not None]
        columnas = [c.get("columna") for c in malla if c.get("columna") is not None]
        probabilidades = [c["prob_ignicion"] for c in malla
                          if c.get("prob_ignicion") is not None]

        # --- Focos: el rango de años sale de los propios datos -----------
        anios = sorted({str(f["fecha"])[:4] for f in focos if f.get("fecha")})
        con_evento = sum(1 for f in focos if f.get("evento"))

        # --- Calibración vigente ------------------------------------------
        try:
            calibracion = almacen_db.leer_calibracion()
        except Exception:  # noqa: BLE001 — la pantalla vale sin esto
            calibracion = None

        cfg_supabase = settings.SUPABASE
        opciones_cookie = auth.opciones_cookie()

        return bien({
            "servicio": {
                "nombre": "SIPRO FIRE backend (Django)",
                "version": "2.0.0-django",
                "depuracion": settings.DEBUG,
                "origenes_cors": settings.CORS_ALLOWED_ORIGINS,
            },
            "almacen": {
                "modo": almacen_db.MODO_ALMACEN,
                # Solo el host: la clave y la ruta completa no salen de aquí.
                "proyecto": urlparse(cfg_supabase["url"]).netloc,
                "esquema": cfg_supabase["esquema"],
                "tablas": list(TABLAS_ESPERADAS),
                "timeout_s": cfg_supabase["timeout_s"],
            },
            "sesion": {
                "duracion_horas": auth.DURACION_SESION_S / 3600,
                "cookie": auth.COOKIE_SESION,
                "samesite": opciones_cookie["samesite"],
                "secure": opciones_cookie["secure"],
                "httponly": opciones_cookie["httponly"],
            },
            "malla": {
                "celdas": len(malla),
                "filas": (max(filas) + 1) if filas else 0,
                "columnas": (max(columnas) + 1) if columnas else 0,
                "resolucion_m": 500,
                "area_celda_ha": AREA_POR_CELDA_HA,
                "bbox": settings.APOLO["bbox"],
                "centro": {"lat": settings.APOLO["lat"], "lon": settings.APOLO["lon"]},
            },
            "focos": {
                "total": len(focos),
                "desde": anios[0] if anios else None,
                "hasta": anios[-1] if anios else None,
                "etiquetados": con_evento,
                "eventos": [
                    {"anio": e.get("anio"), "nombre": e.get("nombre"),
                     "area_km2": e.get("area_km2"), "dias": e.get("duracion_dias")}
                    for e in historicos
                ],
            },
            "modelo": {
                "algoritmo": "XGBoost V3",
                # Métrica del entrenamiento (cuaderno del proyecto). No se
                # recalcula en cada arranque: es un dato documentado, y así se
                # etiqueta en la pantalla.
                "auc_roc": 0.9214,
                "features": 8,
                "celdas_con_probabilidad": len(probabilidades),
                "probabilidad_min": round(min(probabilidades), 4) if probabilidades else None,
                "probabilidad_max": round(max(probabilidades), 4) if probabilidades else None,
                "probabilidad_media": (round(sum(probabilidades) / len(probabilidades), 4)
                                       if probabilidades else None),
            },
            "automata": {
                "vecindad": "Moore (8) con peso por distancia",
                "paso_min": 15,
                "constantes_defecto": CONSTANTES_POR_DEFECTO,
                "calibracion_vigente": calibracion,
            },
            "fuentes": {
                "firms": {
                    "clave_configurada": firms.clave_configurada(),
                    "fuente": settings.FIRMS["fuente"],
                    "dias": settings.FIRMS["dias"],
                    "bbox": settings.FIRMS["bbox"],
                },
                "colas": estado_colas(),
                "cache": estado_cache(),
            },
        })
    except Exception as e:  # noqa: BLE001
        return mal(e)


# ===========================================================================
# AUTENTICACIÓN
# ===========================================================================
@require_http_methods(["POST"])
def auth_login(request: HttpRequest):
    cuerpo = cuerpo_json(request)
    email, password = cuerpo.get("email"), cuerpo.get("password")
    try:
        usuario = almacen_db.verificar_credenciales(email, password)
    except ErrorAPI as e:
        return mal(e)
    except Exception as e:  # noqa: BLE001
        return mal(e)
    if not usuario:
        return error_simple("Email o contraseña incorrectos", 401)

    token = auth.emitir_token(usuario, request)
    almacen_db.marcar_ultimo_acceso(usuario["id"])
    almacen_db.registrar_bitacora({"usuario": usuario["nombre"], "accion": "Inició sesión", "tipo": "auth"})

    respuesta = bien({"access_token": token, **usuario})
    opts = auth.opciones_cookie()
    respuesta.set_cookie(auth.COOKIE_SESION, token, max_age=opts["max_age"], path=opts["path"],
                          httponly=opts["httponly"], samesite=opts["samesite"], secure=opts["secure"])
    return respuesta


@require_http_methods(["GET"])
def auth_yo(request: HttpRequest):
    usuario = auth.verificar_peticion(request)
    if not usuario:
        return error_simple("Sesión no válida o expirada", 401)
    usuario = {k: v for k, v in usuario.items() if k not in ("expira", "jti")}
    return bien(usuario)


@require_http_methods(["POST"])
def auth_logout(request: HttpRequest):
    usuario = auth.verificar_peticion(request)
    if not usuario:
        return error_simple("Sesión no válida o expirada", 401)
    if usuario.get("jti"):
        almacen_db.revocar_sesion(usuario["jti"])
    respuesta = bien({"cerrada": True})
    respuesta.delete_cookie(auth.COOKIE_SESION, path="/")
    return respuesta


# ===========================================================================
# AMBIENTE — Capa 1 (terreno), Capa 2 (meteorología), Capa 3 (FIRMS)
# ===========================================================================
@require_http_methods(["GET"])
def ambiente_focos_historicos(request: HttpRequest):
    """Focos históricos del proyecto. Fuente distinta de NASA FIRMS."""
    try:
        return bien(firms.obtener_focos_historicos(
            zona=request.GET.get("zona", "apolo"),
            limite=int(request.GET.get("limite", 2000)),
            desde=request.GET.get("desde") or None,
            hasta=request.GET.get("hasta") or None))
    except Exception as e:  # noqa: BLE001
        return mal(e)


@require_http_methods(["GET"])
def ambiente_meteo(request: HttpRequest):
    try:
        lat = _num(request.GET.get("lat"), settings.APOLO["lat"])
        lon = _num(request.GET.get("lon"), settings.APOLO["lon"])
        datos = meteo.obtener_meteorologia(lat, lon)
        peligro = meteo.indice_peligro(datos)
        return bien({**datos, "peligro": peligro}, procedencia=datos["procedencia"])
    except Exception as e:  # noqa: BLE001
        return mal(e)


@require_http_methods(["GET"])
def ambiente_climatologia(request: HttpRequest):
    try:
        lat = _num(request.GET.get("lat"), settings.APOLO["lat"])
        lon = _num(request.GET.get("lon"), settings.APOLO["lon"])
        return bien(meteo.obtener_climatologia(lat, lon))
    except Exception as e:  # noqa: BLE001
        return mal(e)


@require_http_methods(["GET"])
def ambiente_firms(request: HttpRequest):
    try:
        zona = request.GET.get("zona", "apolo")
        datos = firms.obtener_focos_activos(zona)
        return bien(datos, procedencia=datos["procedencia"])
    except Exception as e:  # noqa: BLE001
        return mal(e)


@require_http_methods(["GET"])
def ambiente_terreno_osm(request: HttpRequest):
    try:
        datos = terreno.obtener_terreno_osm()
        return bien(datos, procedencia=datos["procedencia"])
    except Exception as e:  # noqa: BLE001
        return mal(e)


@require_http_methods(["GET"])
def ambiente_terreno_dem(request: HttpRequest):
    fila = _num(request.GET.get("fila"))
    columna = _num(request.GET.get("columna"))
    radio = min(_num(request.GET.get("radio"), 20), 60)
    if fila is None or columna is None:
        return error_simple("Faltan los parámetros fila y columna", 400)
    try:
        return bien(terreno.obtener_dem(int(fila), int(columna), int(radio)))
    except Exception as e:  # noqa: BLE001
        return mal(e)


# ===========================================================================
# SIMULACIÓN
# ===========================================================================
@require_http_methods(["GET"])
def simulacion_parametros_auto(request: HttpRequest):
    try:
        grid = grid_srv.obtener_grid()
        foco = None
        if request.GET.get("fila") and request.GET.get("columna"):
            foco = grid_srv.obtener_indice()["por_fila_col"].get(
                f"{int(float(request.GET['fila']))},{int(float(request.GET['columna']))}")
        elif request.GET.get("lat") and request.GET.get("lon"):
            foco = grid_srv.celda_mas_cercana(float(request.GET["lat"]), float(request.GET["lon"]))

        resultado = derivar_parametros(grid, {
            "foco": foco,
            "historicos": grid_srv.obtener_historicos(),
            "horizonte_horas": _num(request.GET.get("horas"), 6),
            "temporada": {
                "incluir": request.GET.get("incluir_estacion") in ("1", "true"),
                "temporada": request.GET.get("estacion", "auto"),
            },
        })
        return bien(resultado)
    except Exception as e:  # noqa: BLE001
        return mal(e)


@require_http_methods(["POST"])
def simulacion_ejecutar(request: HttpRequest):
    # Control de acceso REAL, no cosmético: quien no tenga el permiso
    # `ejecutar_simulacion` en la matriz recibe 403 aunque escriba la URL a
    # mano o llame con curl. Es lo que deja al Administrador fuera de la
    # operación del GIS.
    error = auth.exigir_permiso(request, "ejecutar_simulacion")
    if error:
        return error
    usuario = request.usuario

    cuerpo = cuerpo_json(request)
    parametros = cuerpo.get("parametros") or cuerpo
    if parametros.get("foco_fila") is None or parametros.get("foco_columna") is None:
        return error_simple("Faltan foco_fila y foco_columna", 400)

    t0 = time.time()
    try:
        resultado = ejecutar_simulacion(parametros, {"usuario": usuario, "radio_dem": cuerpo.get("radioDem")})
    except Exception as e:  # noqa: BLE001
        return mal(e)

    # A partir de aquí la simulación YA está hecha y el escenario YA está
    # guardado. Lo que queda son registros secundarios: si alguno falla se
    # anota y se sigue, pero no se tira la respuesta. Antes esto estaba fuera
    # del try y un error al insertar las alertas devolvía un 500 con una
    # simulación perfectamente buena ya persistida detrás.
    avisos = []

    try:
        almacen_db.registrar_bitacora({
            "usuario": usuario["nombre"], "accion": "Ejecutó simulación",
            "detalle": parametros.get("nombre_escenario"), "tipo": "sim",
        })
    except Exception as e:  # noqa: BLE001
        print(f"[simulacion] no se pudo registrar en la bitácora: {e}")
        avisos.append("La simulación se guardó, pero no se pudo registrar en la bitácora.")

    if resultado.get("alertas"):
        try:
            almacen_db.guardar_alertas(
                [{**a, "escenario_id": resultado["escenario_id"]} for a in resultado["alertas"]])
        except Exception as e:  # noqa: BLE001
            print(f"[simulacion] no se pudieron guardar las alertas: {e}")
            avisos.append(
                f"La simulación se guardó, pero sus {len(resultado['alertas'])} alertas no se "
                "pudieron persistir. Se muestran igualmente en esta respuesta."
            )

    ms = int((time.time() - t0) * 1000)
    print(f"[simulacion] {len(resultado['iteraciones'])} iteraciones en {ms} ms")
    extra = {"ms": ms}
    if avisos:
        extra["avisos"] = avisos
    return bien(resultado, **extra)


# ===========================================================================
# SIMULACIÓN INTERACTIVA — la consola
# ===========================================================================
# Una corrida que se puede pausar a mitad, meterle una tormenta y seguir.
# El detalle de por qué esto no rompe la reproducibilidad está en
# `motor/sesion_simulacion.py`; aquí solo está el contrato HTTP.
#
# Todos exigen `ejecutar_simulacion`: pausar y alterar una simulación ES
# simular, así que el Administrador queda fuera igual que del resto del GIS.
@require_http_methods(["GET"])
def escenarios_catalogo(request: HttpRequest):
    """El catálogo de sucesos meteorológicos que se pueden inyectar."""
    error = auth.exigir_permiso(request, "ver_simulaciones")
    if error:
        return error
    return bien({"escenarios": catalogo_escenarios(), "referencias": REFERENCIAS_ESCENARIOS})


@require_http_methods(["POST"])
def consola_iniciar(request: HttpRequest):
    error = auth.exigir_permiso(request, "ejecutar_simulacion")
    if error:
        return error

    cuerpo = cuerpo_json(request)
    parametros = cuerpo.get("parametros") or cuerpo
    if parametros.get("foco_fila") is None or parametros.get("foco_columna") is None:
        return error_simple("Faltan foco_fila y foco_columna", 400)

    try:
        grid = grid_srv.obtener_grid()
        entorno = preparar_entorno(parametros, {
            "radio_dem": cuerpo.get("radioDem"),
            # Se puede pedir una corrida sin DEM ni barreras de OSM: son dos
            # consultas a servicios externos y, para tantear escenarios uno
            # detrás de otro, esperar por ellas no compensa.
            "usar_terreno": cuerpo.get("usar_terreno", True),
        })
        sesion = sesion_sim.crear(
            grid, parametros,
            opciones={
                "elevacion": entorno["elevacion"],
                "barreras_extra": entorno["barreras_extra"],
                "resistencia_extra": entorno["resistencia_extra"],
                "serie_viento": entorno["serie_viento"],
                "serie_ambiental": entorno["serie_ambiental"],
                "constantes": entorno["constantes"],
            },
            guion=cuerpo.get("guion") or [],
            pasos_iniciales=int(cuerpo.get("pasos_iniciales") or 0),
            usuario=request.usuario["nombre"],
        )
        return bien(sesion)
    except Exception as e:  # noqa: BLE001
        return mal(e)


@require_http_methods(["POST"])
def consola_avanzar(request: HttpRequest, id_: str):
    error = auth.exigir_permiso(request, "ejecutar_simulacion")
    if error:
        return error
    cuerpo = cuerpo_json(request)
    try:
        if cuerpo.get("completar"):
            return bien(sesion_sim.completar(id_))
        return bien(sesion_sim.avanzar(id_, int(cuerpo.get("pasos") or sesion_sim.PASOS_POR_TANDA)))
    except Exception as e:  # noqa: BLE001
        return mal(e)


@require_http_methods(["POST"])
def consola_inyectar(request: HttpRequest, id_: str):
    error = auth.exigir_permiso(request, "ejecutar_simulacion")
    if error:
        return error
    cuerpo = cuerpo_json(request)
    if not cuerpo.get("escenario"):
        return error_simple("Falta el id del escenario a inyectar", 400)
    try:
        return bien(sesion_sim.inyectar(
            id_, cuerpo["escenario"],
            intensidad=cuerpo.get("intensidad"),
            ajustes=cuerpo.get("ajustes"),
            duracion_pasos=cuerpo.get("duracion_pasos"),
            paso_inicio=cuerpo.get("paso_inicio"),
        ))
    except Exception as e:  # noqa: BLE001
        return mal(e)


@require_http_methods(["GET", "DELETE"])
def consola_sesion(request: HttpRequest, id_: str):
    error = auth.exigir_permiso(request, "ejecutar_simulacion")
    if error:
        return error
    if request.method == "DELETE":
        sesion_sim.cerrar(id_)
        return bien({"cerrada": True})
    try:
        return bien(sesion_sim.estado(id_, desde=int(request.GET.get("desde") or 0)))
    except Exception as e:  # noqa: BLE001
        return mal(e)


@require_http_methods(["POST"])
def consola_guardar(request: HttpRequest, id_: str):
    """Convierte la sesión interactiva en un escenario guardado de verdad.

    Hasta aquí todo vivía en memoria. Este es el paso que lo mete en Supabase
    con su guion, para que aparezca en el Historial y se pueda comparar.
    """
    error = auth.exigir_permiso(request, "ejecutar_simulacion")
    if error:
        return error
    cuerpo = cuerpo_json(request)
    try:
        datos = sesion_sim.resultado_completo(id_)
        parametros = {
            **datos["parametros"],
            "guion": datos["metadatos"].get("guion") or [],
            "nombre_escenario": cuerpo.get("nombre") or datos["parametros"].get("nombre_escenario"),
            "descripcion": cuerpo.get("descripcion"),
        }
        resultado = guardar_sesion_como_escenario(
            parametros, datos["iteraciones"], datos["metadatos"],
            {"usuario": request.usuario})
    except Exception as e:  # noqa: BLE001
        return mal(e)

    avisos = []
    try:
        almacen_db.registrar_bitacora({
            "usuario": request.usuario["nombre"],
            "accion": "Guardó una simulación interactiva",
            "detalle": f"{len(datos['metadatos'].get('guion') or [])} suceso(s) meteorológico(s)",
            "tipo": "sim",
        })
    except Exception as e:  # noqa: BLE001
        print(f"[consola] no se pudo registrar en la bitácora: {e}")
        avisos.append("El escenario se guardó, pero no se pudo registrar en la bitácora.")

    if resultado.get("alertas"):
        try:
            almacen_db.guardar_alertas(
                [{**a, "escenario_id": resultado["escenario_id"]} for a in resultado["alertas"]])
        except Exception as e:  # noqa: BLE001
            print(f"[consola] no se pudieron guardar las alertas: {e}")
            avisos.append("El escenario se guardó, pero sus alertas no se pudieron persistir.")

    sesion_sim.cerrar(id_)
    return bien(resultado, **({"avisos": avisos} if avisos else {}))


@require_http_methods(["GET"])
def simulacion_detalle(request: HttpRequest, id_: str):
    datos = obtener_simulacion(id_)
    if not datos:
        return error_simple("Escenario no encontrado", 404)
    return bien(datos)


# ===========================================================================
# ESCENARIOS (historial, alertas, gráfica)
# ===========================================================================
@require_http_methods(["GET"])
def escenarios_lista(request: HttpRequest):
    # Ficha ligera: el almacén NO se trae las iteraciones completas de los 200
    # escenarios solo para pintar una lista (serían decenas de MB por carga).
    try:
        fichas = almacen_db.listar_escenarios_resumen()
    except Exception as e:  # noqa: BLE001
        return mal(e)
    return bien([{
        "escenario_id": f["escenario_id"],
        "nombre": f.get("nombre"),
        "creado_por": f.get("creado_por"),
        "creado_en": f.get("creado_en"),
        "parametros": f.get("parametros"),
        "num_iteraciones_ejecutadas": f.get("num_iteraciones") or 0,
        "area_final_quemada_ha": f.get("area_final_ha") or 0,
        "alerta_maxima": f.get("alerta_maxima"),
    } for f in fichas])


@require_http_methods(["GET", "DELETE"])
def escenario_detalle(request: HttpRequest, id_: str):
    if request.method == "DELETE":
        error = auth.exigir_permiso(request, "ver_simulaciones")
        if error:
            return error
        almacen_db.borrar_escenario(id_)
        return bien({"borrado": id_})

    esc = almacen_db.obtener_escenario(id_)
    if not esc:
        return error_simple("Escenario no encontrado", 404)
    return bien({**esc, "iteraciones": reconstruir_iteraciones(esc)})


@require_http_methods(["GET"])
def alertas_riesgo(request: HttpRequest):
    try:
        alertas = calcular_alertas_riesgo()
        almacen_db.reemplazar_alertas_riesgo(alertas)
    except Exception as e:  # noqa: BLE001
        print(f"[alertas riesgo] {e}")
        alertas = almacen_db.alertas_de_riesgo(limite=20)
    return bien({"activas": alertas, "generado_en": datetime.now(timezone.utc).isoformat()})


@require_http_methods(["GET"])
def escenario_alertas(request: HttpRequest, id_: str):
    esc = almacen_db.obtener_escenario(id_)
    if not esc:
        return bien({"activas": [], "historial": []})
    alertas = esc.get("alertas") or []
    max_iter = max((a["iteracion"] for a in alertas), default=None)
    return bien({
        "historial": alertas,
        "activas": [] if max_iter is None else [a for a in alertas if a["iteracion"] == max_iter],
    })


@require_http_methods(["GET"])
def escenario_grafica(request: HttpRequest, id_: str):
    esc = almacen_db.obtener_escenario(id_)
    if not esc:
        return bien([])
    pasos = esc.get("iteraciones_delta") or esc.get("iteraciones") or []
    minutos = (esc.get("metadatos_motor") or {}).get("minutos_por_iteracion", 15)
    return bien([{
        "iteracion": it["iteracion"], "tiempo_minutos": it["iteracion"] * minutos,
        "celdas_ardiendo": it["num_celdas_ardiendo"], "celdas_quemadas": it["num_celdas_quemadas"],
        "area_quemada_ha": it["num_celdas_quemadas"] * AREA_POR_CELDA_HA,
    } for it in pasos])


# ===========================================================================
# CALIBRACIÓN
# ===========================================================================
_calibracion_en_curso: dict | None = None
_calibracion_lock = threading.Lock()


@require_http_methods(["GET", "POST"])
def calibracion_vista(request: HttpRequest):
    global _calibracion_en_curso

    if request.method == "GET":
        return bien({
            "actual": almacen_db.leer_calibracion(),
            "historial": almacen_db.leer_historial_calibracion(),
            "en_curso": (
                {"progreso": _calibracion_en_curso["progreso"], "iniciada": _calibracion_en_curso["iniciada"]}
                if _calibracion_en_curso else None
            ),
        })

    error = auth.exigir_permiso(request, "ejecutar_simulacion")
    if error:
        return error
    usuario = request.usuario

    with _calibracion_lock:
        if _calibracion_en_curso:
            return JsonResponse({"ok": False, "error": "Ya hay una calibración en curso",
                                  "progreso": _calibracion_en_curso["progreso"]}, status=409)
        estado_calib = {"progreso": 0.0, "iniciada": datetime.now(timezone.utc).isoformat(), "mejor": None}
        _calibracion_en_curso = estado_calib

    cuerpo = cuerpo_json(request)

    def _trabajo():
        global _calibracion_en_curso
        try:
            def on_progreso(f, mejor):
                estado_calib["progreso"] = f
                estado_calib["mejor"] = mejor["aptitud"] if mejor else None

            # EL MUNDO DE LA CALIBRACIÓN TIENE QUE SER EL DE LA SIMULACIÓN.
            # Antes esta llamada no pasaba `elevacion` ni `barreras_extra`, así
            # que el optimizador buscaba los parámetros en un municipio sin
            # relieve y sin ríos, y esos parámetros se aplicaban luego a
            # corridas que sí los tenían. La p_base resultante venía inflada
            # para compensar unas barreras que durante la calibración no
            # existían, y esa es una de las causas de la sobreestimación.
            elevacion_cal = None
            barreras_cal = None
            resistencia_cal = None
            try:
                osm = terreno.obtener_terreno_osm()
                barreras_cal = set(osm["barreras"])
                resistencia_cal = osm["resistencia"]
                print(f"[calibracion] capa OSM: {len(barreras_cal)} barreras duras, "
                      f"{len(resistencia_cal)} celdas con resistencia")
            except Exception as e:  # noqa: BLE001
                print(f"[calibracion] sin capa OSM, se calibra sin barreras: {e}")
            try:
                # DEM centrado en el municipio, con radio amplio: la
                # calibración recorre varios eventos repartidos por Apolo.
                ix = grid_srv.obtener_indice()
                ref = ix.get("ref") or {}
                dem = terreno.obtener_dem(ref.get("fila", 0), ref.get("columna", 0), 200)
                elevacion_cal = dem["alturas"]
                print(f"[calibracion] DEM: {len(elevacion_cal)} celdas con altura")
            except Exception as e:  # noqa: BLE001
                print(f"[calibracion] sin DEM, se calibra en terreno plano: {e}")

            resultado = calibrar(
                grid_srv.obtener_grid(), grid_srv.obtener_focos(),
                poblacion=cuerpo.get("poblacion", 12), generaciones=cuerpo.get("generaciones", 10),
                on_progreso=on_progreso,
                elevacion=elevacion_cal,
                barreras_extra=barreras_cal,
                resistencia_extra=resistencia_cal,
            )
            almacen_db.registrar_bitacora({
                "usuario": usuario["nombre"], "accion": "Calibró constantes K",
                "detalle": f"F1 = {resultado['f1'] * 100:.1f}%", "tipo": "sim",
            })
            print(f"[calibracion] terminada · F1 = {resultado['f1'] * 100:.1f}%")
        except Exception as e:  # noqa: BLE001
            print(f"[calibracion] fallida: {e}")
            estado_calib["error"] = str(e)
        finally:
            _calibracion_en_curso = None

    threading.Thread(target=_trabajo, daemon=True).start()
    return bien({"iniciada": True, "consultar": "/api/calibracion"})


# ===========================================================================
# INFORMES
# ===========================================================================
@require_http_methods(["GET", "POST"])
def informes_lista(request: HttpRequest):
    if request.method == "GET":
        usuario = auth.verificar_peticion(request)
        if not usuario:
            return error_simple("Sesión no válida o expirada", 401)
        return bien(almacen_db.listar_informes())

    error = auth.exigir_permiso(request, "generar_reportes")
    if error:
        return error
    usuario = request.usuario

    cuerpo = cuerpo_json(request)
    html, nombre = cuerpo.get("html"), cuerpo.get("nombre")
    if not html or not nombre:
        return error_simple("Faltan nombre y html del informe", 400)

    informe = almacen_db.guardar_informe({
        "escenario_id": cuerpo.get("escenario_id"), "nombre": nombre, "html": html,
        "generado_por": usuario["nombre"], "resumen": cuerpo.get("resumen"),
    })
    almacen_db.registrar_bitacora({"usuario": usuario["nombre"], "accion": "Generó informe",
                                    "detalle": nombre, "tipo": "report"})
    ficha = {k: v for k, v in informe.items() if k != "html"}
    return bien(ficha)


@require_http_methods(["GET"])
def informe_detalle(request: HttpRequest, id_: str):
    usuario = auth.verificar_peticion(request)
    if not usuario:
        return error_simple("Sesión no válida o expirada", 401)
    informe = almacen_db.obtener_informe(id_)
    if not informe:
        return error_simple("Informe no encontrado", 404)
    return bien(informe)


# ===========================================================================
# USUARIOS
# ===========================================================================
@require_http_methods(["GET", "POST"])
def usuarios_lista(request: HttpRequest):
    # EL GET PIDE PERMISO, no solo sesión.
    #
    # Antes solo comprobaba que hubiera una sesión válida, así que CUALQUIER
    # usuario autenticado podía listar todas las cuentas del sistema: correos,
    # nombres y roles. Lo destapó la prueba del brigadista: un rol que no debe
    # tocar nada administrativo obtenía la lista entera con un GET a mano.
    #
    # Ocultar la pantalla en el menú no servía de nada, porque la restricción
    # estaba en la interfaz y no en el servidor. Ahora exige
    # `gestionar_usuarios`, que es el mismo permiso que habilita la pantalla.
    #
    # Solo lo usan las pantallas de administración (Gestión de usuarios y el
    # tablero del administrador), y ambas ya requieren ese permiso, así que
    # no se rompe nada.
    denegado = auth.exigir_permiso(request, "gestionar_usuarios")
    if denegado:
        return denegado
    usuario = request.usuario

    if request.method == "GET":
        return bien(almacen_db.listar_usuarios())

    if usuario.get("rol") != "administrador":
        return error_simple("Solo un administrador puede gestionar usuarios", 403)

    cuerpo = cuerpo_json(request)
    email, nombre, rol = cuerpo.get("email"), cuerpo.get("nombre"), cuerpo.get("rol")
    if not email or not nombre or not rol:
        return error_simple("Faltan email, nombre o rol", 400)
    try:
        creado = almacen_db.crear_usuario(cuerpo)
    except ErrorAPI as e:
        return mal(e)
    almacen_db.registrar_bitacora({"usuario": usuario["nombre"], "accion": "Creó usuario",
                                    "detalle": f"{nombre} · rol {rol}", "tipo": "user"})
    return bien(creado)


@require_http_methods(["PATCH", "DELETE"])
def usuario_detalle(request: HttpRequest, id_: str):
    usuario = auth.verificar_peticion(request)
    if not usuario:
        return error_simple("Sesión no válida o expirada", 401)
    if usuario.get("rol") != "administrador":
        return error_simple("Solo un administrador puede gestionar usuarios", 403)

    usuarios = almacen_db.listar_usuarios()
    objetivo = next((u for u in usuarios if u["id"] == id_), None)
    if not objetivo:
        return error_simple("Usuario no encontrado", 404)

    if request.method == "DELETE":
        if objetivo["email"] == usuario["email"]:
            return error_simple("No puedes desactivar tu propia cuenta.", 400)
        actualizado = almacen_db.desactivar_usuario(id_)
        almacen_db.revocar_sesiones_de_usuario(id_)
        almacen_db.registrar_bitacora({"usuario": usuario["nombre"], "accion": "Desactivó usuario",
                                        "detalle": actualizado["nombre"], "tipo": "user"})
        return bien({**actualizado, "mensaje": "Usuario desactivado (el registro se conserva en la base)."})

    cambios = cuerpo_json(request)
    es_el_mismo = objetivo["email"] == usuario["email"]
    if es_el_mismo and (cambios.get("activo") is False or (cambios.get("rol") and cambios["rol"] != "administrador")):
        return error_simple("No puedes desactivarte ni quitarte tu propio rol de administrador.", 400)

    try:
        actualizado = almacen_db.actualizar_usuario(id_, cambios)
    except ErrorAPI as e:
        return mal(e)
    if cambios.get("activo") is False or cambios.get("rol"):
        almacen_db.revocar_sesiones_de_usuario(id_)
    almacen_db.registrar_bitacora({"usuario": usuario["nombre"], "accion": "Actualizó usuario",
                                    "detalle": f"{actualizado['nombre']} · {cambios}", "tipo": "user"})
    return bien(actualizado)


@require_http_methods(["POST"])
def usuario_restablecer_password(request: HttpRequest, id_: str):
    usuario = auth.verificar_peticion(request)
    if not usuario:
        return error_simple("Sesión no válida o expirada", 401)
    if usuario.get("rol") != "administrador":
        return error_simple("Solo un administrador puede gestionar usuarios", 403)

    cuerpo = cuerpo_json(request)
    nueva = (cuerpo.get("password") or "").strip()
    if len(nueva) < 6:
        return error_simple("La contraseña temporal debe tener al menos 6 caracteres", 400)

    try:
        actualizado = almacen_db.restablecer_password(id_, nueva)
    except ErrorAPI as e:
        return mal(e)
    almacen_db.revocar_sesiones_de_usuario(id_)
    almacen_db.registrar_bitacora({"usuario": usuario["nombre"], "accion": "Restableció contraseña",
                                    "detalle": actualizado["nombre"], "tipo": "user"})
    return bien({**actualizado, "mensaje": "Contraseña restablecida. El usuario deberá entrar con la nueva."})


# ===========================================================================
# ROLES Y PERMISOS
# ===========================================================================
@require_http_methods(["GET", "PUT"])
def permisos_vista(request: HttpRequest):
    usuario = auth.verificar_peticion(request)
    if not usuario:
        return error_simple("Sesión no válida o expirada", 401)

    if request.method == "GET":
        return bien(almacen_db.leer_matriz_permisos())

    if usuario.get("rol") != "administrador":
        return error_simple("Solo un administrador puede gestionar usuarios", 403)

    cuerpo = cuerpo_json(request)
    matriz, password = cuerpo.get("matriz"), cuerpo.get("password")
    if not matriz or not isinstance(matriz, dict):
        return error_simple("Falta la matriz de permisos", 400)

    try:
        ok = almacen_db.verificar_credenciales(usuario["email"], password or "")
    except Exception:  # noqa: BLE001
        ok = None
    if not ok:
        return error_simple("Contraseña incorrecta. Los permisos no se guardaron.", 403)

    guardada = almacen_db.guardar_matriz_permisos(matriz)
    almacen_db.registrar_bitacora({"usuario": usuario["nombre"], "accion": "Modificó la matriz de permisos",
                                    "detalle": "Actualización de roles y permisos", "tipo": "user"})
    return bien(guardada)


# ===========================================================================
# HISTÓRICOS Y BITÁCORA
# ===========================================================================
@require_http_methods(["GET"])
def historicos(request: HttpRequest):
    return bien(grid_srv.obtener_historicos())


# ===========================================================================
# Reportes de campo de los brigadistas
# ===========================================================================
# Tamaño máximo de la foto ya codificada en base64. 400 KB de data URI son
# unos 300 KB de imagen, que es de sobra para una foto reducida a 1024 px.
# El límite existe porque el reporte se guarda en una columna de texto: sin
# tope, una foto de 8 MP la reventaría y perderíamos el reporte entero.
LIMITE_FOTO_B64 = 400_000

ESTADOS_OBSERVADOS = {"humo_visible", "fuego_activo", "area_quemada"}


@require_http_methods(["GET", "POST"])
def reportes_campo(request: HttpRequest):
    """Reportes de incendio enviados desde el terreno.

    CONTROL DE ACCESO, por permiso y no por nombre de rol
        GET   `ver_reportes_campo`  → brigadista, analista y UGR
        POST  `reportar_incendio`   → SOLO el brigadista

    Que el POST pida un permiso distinto del GET es lo que impide que el
    analista o la UGR creen o alteren reportes: la UGR los consulta como
    información de apoyo y nada más. Y se comprueba en el SERVIDOR, no solo
    ocultando el botón, porque ocultar un botón no es control de acceso.
    """
    if request.method == "GET":
        denegado = auth.exigir_permiso(request, "ver_reportes_campo")
        if denegado:
            return denegado
        return bien(almacen_db.listar_reportes_campo())

    # --- POST: crear un reporte --------------------------------------------
    denegado = auth.exigir_permiso(request, "reportar_incendio")
    if denegado:
        return denegado

    usuario = auth.verificar_peticion(request)
    cuerpo = cuerpo_json(request)

    # --- Validación --------------------------------------------------------
    try:
        lat = float(cuerpo.get("lat"))
        lon = float(cuerpo.get("lon"))
    except (TypeError, ValueError):
        return error_simple(
            "Hacen falta las coordenadas del reporte: márcalas en el mapa o "
            "escríbelas a mano.", 400)

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return error_simple("Las coordenadas están fuera de rango.", 400)

    estado = (cuerpo.get("estado") or "").strip()
    if estado not in ESTADOS_OBSERVADOS:
        return error_simple(
            f"El estado observado tiene que ser uno de: "
            f"{', '.join(sorted(ESTADOS_OBSERVADOS))}.", 400)

    descripcion = (cuerpo.get("descripcion") or "").strip()
    if len(descripcion) > 1000:
        descripcion = descripcion[:1000]

    foto = cuerpo.get("foto") or None
    if foto:
        if not isinstance(foto, str) or not foto.startswith("data:image/"):
            return error_simple("La fotografía no tiene un formato válido.", 400)
        if len(foto) > LIMITE_FOTO_B64:
            return error_simple(
                "La fotografía es demasiado grande. Vuelve a enviarla: la "
                "aplicación la reduce automáticamente antes de subirla.", 413)

    reporte = almacen_db.crear_reporte_campo({
        "lat": lat, "lon": lon, "estado": estado,
        "descripcion": descripcion, "foto": foto,
        "brigadista": usuario.get("nombre") or usuario.get("email"),
        "rol": usuario.get("rol"),
    })
    return bien({**reporte, "mensaje": "El reporte será enviado para su revisión"})

@require_http_methods(["GET", "POST"])
def bitacora_vista(request: HttpRequest):
    if request.method == "GET":
        # La bitácora es el registro de auditoría: quién entró, quién simuló,
        # quién tocó los permisos. Se leía SIN sesión, o sea que cualquiera con
        # la URL veía la actividad entera del sistema. Ahora pide el permiso
        # `ver_bitacora`, que es justo lo que dice la matriz.
        denegado = auth.exigir_permiso(request, "ver_bitacora")
        if denegado:
            return denegado
        return bien(almacen_db.listar_bitacora())

    usuario = auth.verificar_peticion(request)
    if not usuario:
        return error_simple("Sesión no válida o expirada", 401)
    cuerpo = cuerpo_json(request)
    almacen_db.registrar_bitacora({"usuario": usuario["nombre"], **cuerpo})
    return bien({"registrado": True})
