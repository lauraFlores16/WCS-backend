"""Puerto de backend/almacen/permisos_defecto.js — catálogo de permisos y
matriz por defecto por rol."""
from __future__ import annotations

PERMISOS = [
    {"id": "ver_monitoreo", "etiqueta": "Ver monitoreo", "descripcion": "Acceso al módulo de monitoreo y datos espaciales"},
    {"id": "ver_variables", "etiqueta": "Ver variables ambientales", "descripcion": "Visualización de NDVI, temperatura, humedad, etc."},
    {"id": "ver_focos", "etiqueta": "Ver focos", "descripcion": "Consulta de focos de calor históricos y activos"},
    {"id": "consultar_probabilidad", "etiqueta": "Consultar probabilidad", "descripcion": "Acceso al mapa de probabilidad de incendio"},
    {"id": "ejecutar_simulacion", "etiqueta": "Ejecutar simulación", "descripcion": "Lanzar simulaciones con autómatas celulares"},
    {"id": "ver_simulaciones", "etiqueta": "Ver simulaciones", "descripcion": "Consulta del historial de simulaciones"},
    {"id": "generar_reportes", "etiqueta": "Generar reportes", "descripcion": "Creación y descarga de reportes"},
    {"id": "gestionar_usuarios", "etiqueta": "Gestionar usuarios", "descripcion": "Acceso al CRUD de usuarios"},
    {"id": "configuracion", "etiqueta": "Configuración del sistema", "descripcion": "Acceso a la configuración general"},
    {"id": "ver_bitacora", "etiqueta": "Ver bitácora", "descripcion": "Acceso al registro de actividades"},
]

ROLES = ["administrador", "analista", "ugr", "brigada"]

# ---------------------------------------------------------------------------
# Permisos que pertenecen a la OPERACIÓN del GIS: mapa, focos, probabilidad y
# simulación. El Administrador NO los tiene: su trabajo es administrar el
# sistema (cuentas, actividad, configuración), no operar el análisis.
#
# Antes el rol administrador tenía la matriz entera en `True` y además el
# frontend se saltaba la comprobación, así que entraba a todo. Los dos
# atajos se quitaron.
# ---------------------------------------------------------------------------
PERMISOS_OPERATIVOS = {
    "ver_monitoreo", "ver_variables", "ver_focos",
    "consultar_probabilidad", "ejecutar_simulacion", "ver_simulaciones",
}

MATRIZ_DEFECTO = {
    "administrador": {
        p["id"]: (p["id"] not in PERMISOS_OPERATIVOS) for p in PERMISOS
    },
    "analista": {
        "ver_monitoreo": True, "ver_variables": True, "ver_focos": True,
        "consultar_probabilidad": True, "ejecutar_simulacion": True, "ver_simulaciones": True,
        "generar_reportes": True, "gestionar_usuarios": False, "configuracion": False, "ver_bitacora": False,
    },
    "ugr": {
        "ver_monitoreo": True, "ver_variables": True, "ver_focos": True,
        "consultar_probabilidad": True, "ejecutar_simulacion": False, "ver_simulaciones": True,
        "generar_reportes": True, "gestionar_usuarios": False, "configuracion": False, "ver_bitacora": False,
    },
    "brigada": {
        "ver_monitoreo": True, "ver_variables": True, "ver_focos": True,
        "consultar_probabilidad": False, "ejecutar_simulacion": False, "ver_simulaciones": True,
        "generar_reportes": False, "gestionar_usuarios": False, "configuracion": False, "ver_bitacora": False,
    },
}


def normalizar_matriz(matriz: dict | None = None) -> dict:
    matriz = matriz or {}
    salida = {}
    for rol in ROLES:
        salida[rol] = {}
        for p in PERMISOS:
            salida[rol][p["id"]] = bool((matriz.get(rol) or {}).get(p["id"]))
    return salida


def corregir_administrador(matriz: dict) -> tuple[dict, list[str]]:
    """Quita al rol administrador los permisos operativos y le garantiza los suyos.

    Se aplica al arrancar sobre la matriz que hay guardada en la base. Hace
    falta porque la fila `permisos` de un despliegue anterior se guardó cuando
    el administrador lo tenía todo en `True`: cambiar el valor por defecto en
    el código no toca lo ya escrito.

    Devuelve (matriz corregida, lista de permisos que se han cambiado).
    """
    corregida = normalizar_matriz(matriz)
    admin = corregida.setdefault("administrador", {})
    cambios = []

    for permiso in PERMISOS_OPERATIVOS:
        if admin.get(permiso):
            admin[permiso] = False
            cambios.append(f"-{permiso}")

    # Y al revés: un administrador sin sus propios permisos se quedaría fuera
    # de su propia pantalla de gestión.
    for permiso in ("gestionar_usuarios", "ver_bitacora", "configuracion", "generar_reportes"):
        if not admin.get(permiso):
            admin[permiso] = True
            cambios.append(f"+{permiso}")

    return corregida, cambios
