"""
============================================================================
ALMACÉN — puerta única hacia la base de datos
============================================================================
Este archivo no implementa nada: reexporta el adaptador que toca. Hoy hay
uno solo y es **Supabase** (db_supabase.py); el resto del backend importa
siempre desde aquí y no sabe qué hay detrás.

MODO ESTRICTO (decisión del proyecto)
-------------------------------------
Si faltan SUPABASE_URL / SUPABASE_SERVICE_KEY, esto revienta al importar,
con un mensaje que dice exactamente qué poner y dónde. NO existe respaldo
automático a los JSON de disco: un respaldo silencioso es peor que un fallo
ruidoso, porque el sistema parecería funcionar mientras escribe los datos en
un sitio que nadie va a mirar.

`db_json.py` sigue en el árbol con el mismo contrato, por si hace falta
migrar datos viejos o trabajar sin conexión a propósito.
============================================================================
"""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

if not settings.USAR_SUPABASE:
    raise ImproperlyConfigured(
        "\n"
        "  SIPRO FIRE necesita Supabase y no encuentra las credenciales.\n"
        "\n"
        "  Crea backend_django/.env (puedes copiar .env.example) y pon:\n"
        "\n"
        "      SUPABASE_URL=https://TU-PROYECTO.supabase.co\n"
        "      SUPABASE_SERVICE_KEY=<la service_role, NO la anon key>\n"
        "\n"
        "  Las dos están en Supabase → Project Settings → API.\n"
        "  Después comprueba la conexión con:\n"
        "\n"
        "      python manage.py verificar_supabase\n"
    )

from .db_supabase import (  # noqa: E402,F401  (import tras la validación, a propósito)
    MODO_ALMACEN,
    USUARIOS_DEMO,
    # escenarios
    guardar_escenario, listar_escenarios, listar_escenarios_resumen,
    obtener_escenario, borrar_escenario,
    # alertas
    guardar_alertas, alertas_de_escenario, alertas_de_riesgo, reemplazar_alertas_riesgo,
    # calibración
    guardar_calibracion, leer_calibracion, leer_historial_calibracion,
    # bitácora
    registrar_bitacora, listar_bitacora,
    crear_reporte_campo, listar_reportes_campo,
    # informes
    guardar_informe, listar_informes, obtener_informe, informe_de_escenario,
    # sesiones
    crear_sesion, obtener_sesion, revocar_sesion, revocar_sesiones_de_usuario,
    limpiar_sesiones,
    # usuarios
    migrar_passwords, listar_usuarios, crear_usuario, actualizar_usuario,
    desactivar_usuario, verificar_credenciales, marcar_ultimo_acceso,
    restablecer_password,
    # permisos
    leer_matriz_permisos, guardar_matriz_permisos,
    # validación multizona
    vz_guardar_zona, vz_leer_zona, vz_listar_zonas, vz_borrar_zona,
    vz_guardar_paquete, vz_leer_paquete, vz_listar_paquetes, vz_actualizar_paquete,
    vz_particion, vz_asignar_rol,
    vz_guardar_trabajo, vz_leer_trabajo, vz_listar_trabajos,
    vz_guardar_mapbiomas, vz_leer_mapbiomas, vz_listar_mapbiomas,
)
