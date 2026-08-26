"""
Prueba de integración del adaptador de Supabase contra el PostgREST de mentira.

    cd backend_django
    python pruebas/prueba_supabase.py

Ejercita el contrato entero (usuarios, sesiones, escenarios, alertas,
calibraciones, bitácora, informes y permisos) y comprueba además los cuatro
puntos donde Supabase es más estricto que los JSON de disco:

  · columnas que no existen    (`celda_id`, `creado_en` en alertas)
  · lotes con claves desiguales (PGRST102 · "All object keys must match")
  · claves ajenas al borrar     (escenario con alertas e informes colgando)
  · CHECK de `bitacora.tipo`
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import postgrest_falso  # noqa: E402  (vive junto a este archivo)

PUERTO = 8137
servidor = postgrest_falso.arrancar(PUERTO)

os.environ["SUPABASE_URL"] = f"http://127.0.0.1:{PUERTO}"
os.environ["SUPABASE_SERVICE_KEY"] = "clave-de-mentira-para-pruebas"
os.environ["SUPABASE_ESQUEMA"] = "public"
os.environ["PRECALENTAR"] = "false"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from api.almacen import db as almacen  # noqa: E402
from api.errores import ErrorAPI  # noqa: E402

fallos = []


def ok(condicion, descripcion, extra=""):
    if condicion:
        print(f"  ✓ {descripcion}")
    else:
        fallos.append(descripcion)
        print(f"  ✗ {descripcion}  {extra}")


print("\n1. Usuarios y contraseñas")
# django.setup() ya ejecutó apps.ready(), que siembra los usuarios demo: para
# cuando llegamos aquí la tabla ya está poblada. Lo que se comprueba es que
# quedaron los 4 y que volver a llamar no duplica ni re-hashea nada.
usuarios = almacen.listar_usuarios()
ok(len(usuarios) == 4, f"el arranque sembró los 4 usuarios demo (hay {len(usuarios)})")
ok(almacen.migrar_passwords() == 0, "migrar_passwords es idempotente (segunda pasada: 0 cambios)")
ok(len(almacen.listar_usuarios()) == 4, "…y no duplica usuarios")
ok(all("password" not in u for u in usuarios), "listar_usuarios NUNCA expone la contraseña")

sesion_usuario = almacen.verificar_credenciales("analista@demo.sipro.com", "demo1234")
ok(sesion_usuario and sesion_usuario["rol"] == "analista", "login correcto del analista")
ok(almacen.verificar_credenciales("analista@demo.sipro.com", "malísima") is None,
   "contraseña incorrecta rechazada")

nuevo = almacen.crear_usuario({"email": "nuevo@demo.sipro.com", "nombre": "Brigadista Nuevo",
                                "rol": "brigada", "password": "clave1234"})
ok(nuevo["nombre"] == "Brigadista Nuevo", "crear_usuario")
try:
    almacen.crear_usuario({"email": "nuevo@demo.sipro.com", "nombre": "Duplicado", "rol": "brigada"})
    ok(False, "email duplicado rechazado")
except ErrorAPI as e:
    ok(e.estado_http == 400, "email duplicado rechazado con 400")

almacen.actualizar_usuario(nuevo["id"], {"activo": False})
try:
    almacen.verificar_credenciales("nuevo@demo.sipro.com", "clave1234")
    ok(False, "cuenta desactivada bloquea el login")
except ErrorAPI as e:
    ok(e.estado_http == 403, "cuenta desactivada bloquea el login (403)")

almacen.restablecer_password(nuevo["id"], "otraclave")
almacen.actualizar_usuario(nuevo["id"], {"activo": True})
ok(almacen.verificar_credenciales("nuevo@demo.sipro.com", "otraclave") is not None,
   "restablecer_password deja entrar con la nueva")
ok(almacen.verificar_credenciales("nuevo@demo.sipro.com", "clave1234") is None,
   "la contraseña anterior deja de valer")

print("\n2. Sesiones")
almacen.crear_sesion({"id": "s-1", "usuario_id": "u-analista",
                      "emitida_en": "2026-01-01T00:00:00+00:00",
                      "expira_en": "2099-01-01T00:00:00+00:00",
                      "user_agent": "prueba", "ip": "127.0.0.1"})
ok(almacen.obtener_sesion("s-1") is not None, "crear/obtener sesión")
almacen.revocar_sesion("s-1")
ok(almacen.obtener_sesion("s-1")["revocada"] is True, "revocar sesión")

almacen.crear_sesion({"id": "s-2", "usuario_id": "u-ugr",
                      "expira_en": "2099-01-01T00:00:00+00:00"})
almacen.revocar_sesiones_de_usuario("u-ugr")
ok(almacen.obtener_sesion("s-2")["revocada"] is True, "revocar todas las sesiones de un usuario")
borradas = almacen.limpiar_sesiones()
ok(borradas == 2, f"limpiar_sesiones borra las revocadas (borró {borradas})")

print("\n3. Escenarios (columnas + datos jsonb)")
ESC = "esc-prueba-1"
escenario = {
    "escenario_id": ESC,
    "nombre": "Apolo · prueba",
    "descripcion": "campo sin columna propia → debe acabar en `datos`",
    "creado_por": "Analista Demo",
    "creado_en": "2026-08-24T12:00:00+00:00",
    "parametros": {"foco_fila": 83, "foco_columna": 126, "p_base": 0.3},
    "foco_coordenadas": {"lat": -14.32, "lon": -68.54},
    "variables_promedio": {"ndvi": 0.42},
    "metadatos_motor": {"usa_dem": False, "saltos_spotting": 0},
    "area_final_ha": 175.0,
    "horas_serie_viento": 48,
    "diagnostico": [{"etiqueta": "Foco", "valor": "fila 83"}],
    "iteraciones_delta": [
        {"iteracion": 0, "num_celdas_ardiendo": 1, "num_celdas_quemadas": 0, "viento": None,
         "cambios": [{"celda_id": "c1", "lat": -14.3, "lon": -68.5, "estado": "ardiendo"}]},
        {"iteracion": 1, "num_celdas_ardiendo": 3, "num_celdas_quemadas": 1, "viento": None,
         "cambios": [{"celda_id": "c2", "lat": -14.3, "lon": -68.5, "estado": "ardiendo"}]},
        {"iteracion": 7, "num_celdas_ardiendo": 0, "num_celdas_quemadas": 7, "viento": None,
         "cambios": []},
    ],
    "alertas": [],
}
almacen.guardar_escenario(escenario)
leido = almacen.obtener_escenario(ESC)
ok(leido is not None, "guardar/obtener escenario")
ok(leido["nombre"] == "Apolo · prueba", "columna `nombre` va y vuelve")
ok(leido["descripcion"].startswith("campo sin columna"), "campo extra sobrevive dentro de `datos`")
ok(leido["horas_serie_viento"] == 48, "otro campo extra sobrevive")
ok(len(leido["iteraciones_delta"]) == 3, "los deltas vuelven como `iteraciones_delta`")
ok("iteraciones" not in leido,
   "NO queda una clave `iteraciones` suelta (reconstruir_iteraciones la malinterpretaría)")

from api.motor.simulacion import reconstruir_iteraciones  # noqa: E402

its = reconstruir_iteraciones(leido)
ok(len(its) == 3 and its[-1]["num_celdas_quemadas"] == 7,
   "reconstruir_iteraciones funciona sobre lo leído de la base")

print("\n4. Alertas (columnas que no existen + lote heterogéneo)")
# Tal cual las produce motor/alertas.py, y ES un lote heterogéneo:
#   · amarilla/naranja → hablan del incendio entero, SIN lat/lon
#   · roja             → apunta a una celda, CON lat/lon (y con `celda_id`,
#                        que no es columna)
# Además todas traen `creado_en` y la columna se llama `creada_en`.
# Si el adaptador no filtrara Y homogeneizara el lote, PostgREST respondería
# 400: sin filtrar por la columna inexistente, y PGRST102 por las claves
# desiguales. Este segundo caso es el que se escapó en producción.
alertas_motor = [
    {"escenario_id": ESC, "nivel": "amarilla", "mensaje": "60 celdas ardiendo",
     "iteracion": 3, "creado_en": "2026-08-24T12:05:00+00:00"},
    {"escenario_id": ESC, "nivel": "naranja", "mensaje": "Crecimiento del 45%",
     "iteracion": 4, "creado_en": "2026-08-24T12:05:30+00:00"},
    {"escenario_id": ESC, "nivel": "roja", "mensaje": "Celda c2f3 comprometida",
     "iteracion": 5, "celda_id": "c2f3ab", "lat": -14.31, "lon": -68.52,
     "creado_en": "2026-08-24T12:06:00+00:00"},
]
almacen.guardar_alertas(alertas_motor)
guardadas = almacen.alertas_de_escenario(ESC)
ok(len(guardadas) == 3, f"se guarda el lote heterogéneo entero (hay {len(guardadas)})")
ok(all(a["creada_en"] for a in guardadas), "`creado_en` del motor se mapea a `creada_en`")
ok(all(a["origen"] == "simulacion" for a in guardadas), "origen por defecto = simulacion")
roja = next(a for a in guardadas if a["nivel"] == "roja")
amarilla = next(a for a in guardadas if a["nivel"] == "amarilla")
ok(roja["lat"] == -14.31 and roja["lon"] == -68.52,
   "la roja conserva sus coordenadas (es el acceso al foco desde el panel)")
ok(amarilla["lat"] is None and amarilla["lon"] is None,
   "la amarilla queda con lat/lon a NULL, no rompe el lote")
ok("celda_id" not in roja, "`celda_id` se descarta (no es columna de la tabla)")

riesgo = almacen.reemplazar_alertas_riesgo([
    {"nivel": "naranja", "mensaje": "Riesgo elevado: 120 celdas", "lat": -14.6, "lon": -68.2,
     "creada_en": "2026-08-24T12:10:00+00:00"},
])
ok(len(riesgo) == 1, "reemplazar_alertas_riesgo inserta")
ok(len(almacen.alertas_de_riesgo()) == 1, "alertas_de_riesgo solo devuelve las de riesgo")
ok(len(almacen.alertas_de_escenario(ESC)) == 3, "reemplazar riesgo NO toca las de simulación")

print("\n5. Listado del historial (ligero) ")
resumen = almacen.listar_escenarios_resumen()
ok(len(resumen) == 1, "listar_escenarios_resumen devuelve el escenario")
ok("iteraciones" not in resumen[0] and "datos" not in resumen[0],
   "el resumen NO se trae las iteraciones (es lo que lo hace barato)")
ok(resumen[0]["num_iteraciones"] == 7, f"num_iteraciones = 7 (dio {resumen[0]['num_iteraciones']})")
ok(resumen[0]["alerta_maxima"] == "roja", "alerta_maxima calculada = roja")
ok(resumen[0]["area_final_ha"] == 175.0, "area_final_ha sale de su columna")

print("\n6. Informes")
informe = almacen.guardar_informe({"escenario_id": ESC, "nombre": "Informe prueba",
                                    "html": "<h1>hola</h1>", "generado_por": "Analista Demo",
                                    "resumen": {"area_final_ha": 175.0}})
ok(informe["id"], "guardar_informe devuelve la ficha con id")
ok(almacen.obtener_informe(informe["id"])["html"] == "<h1>hola</h1>", "obtener_informe trae el HTML")
ok("html" not in almacen.listar_informes()[0], "listar_informes NO arrastra el HTML")

print("\n7. Bitácora (CHECK de `tipo`)")
almacen.registrar_bitacora({"usuario": "Analista Demo", "accion": "Ejecutó simulación", "tipo": "sim"})
almacen.registrar_bitacora({"usuario": "Analista Demo", "accion": "Algo raro", "tipo": "inventado"})
bitacora = almacen.listar_bitacora()
ok(len(bitacora) == 2, "se registran las 2 entradas")
ok(any(b["tipo"] == "inventado" for b in bitacora) is False,
   "un `tipo` fuera del CHECK se degrada a 'data' en vez de perder el registro")

print("\n8. Calibración")
almacen.guardar_calibracion({"fecha": "2026-08-01T00:00:00+00:00", "f1": 0.10,
                              "metodo": "vieja", "p_base": 0.2, "constantes": {"K_VIENTO": 0.1},
                              "detalle": []})
almacen.guardar_calibracion({"fecha": "2026-08-20T00:00:00+00:00", "f1": 0.143,
                              "metodo": "nueva", "p_base": 0.214, "constantes": {"K_VIENTO": 0.478},
                              "detalle": []})
vigente = almacen.leer_calibracion()
ok(vigente["metodo"] == "nueva", "leer_calibracion devuelve la vigente (la última)")
ok(len(almacen.leer_historial_calibracion()) == 2, "el historial conserva las dos")

print("\n9. Permisos")
matriz = almacen.leer_matriz_permisos()
ok(matriz["administrador"]["gestionar_usuarios"] is True, "matriz por defecto: admin lo puede todo")
ok(matriz["brigada"]["ejecutar_simulacion"] is False, "matriz por defecto: brigada no simula")
matriz["brigada"]["ejecutar_simulacion"] = True
almacen.guardar_matriz_permisos(matriz)
ok(almacen.leer_matriz_permisos()["brigada"]["ejecutar_simulacion"] is True,
   "la matriz guardada se lee de vuelta")

print("\n10. Borrado con claves ajenas")
# El escenario tiene 2 alertas y 1 informe colgando. Sin limpiar antes, la FK
# haría fallar el DELETE (409). Es el punto que la versión Node no cubría.
almacen.borrar_escenario(ESC)
ok(almacen.obtener_escenario(ESC) is None, "borrar_escenario elimina el escenario")
ok(almacen.alertas_de_escenario(ESC) == [], "…y sus alertas")
ok(almacen.listar_informes() == [], "…y sus informes")
ok(len(almacen.alertas_de_riesgo()) == 1, "…sin tocar las alertas de riesgo (no son suyas)")

servidor.shutdown()

print("\n" + "─" * 62)
if fallos:
    print(f"  {len(fallos)} COMPROBACIONES FALLIDAS:")
    for f in fallos:
        print(f"    · {f}")
    sys.exit(1)
print("  Todas las comprobaciones del adaptador Supabase pasaron.")
print("─" * 62 + "\n")
