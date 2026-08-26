import os
import sys
import threading
import time

from django.apps import AppConfig

# Comandos que no sirven peticiones: no tiene sentido cargar el grid ni tocar
# Supabase para ellos (y `verificar_supabase` necesita poder correr AUNQUE la
# conexión esté mal, que es justo para lo que existe).
_COMANDOS_OMITIDOS = {
    "migrate", "makemigrations", "collectstatic", "shell", "test",
    "verificar_supabase", "help", "version",
}


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"

    def ready(self):
        if any(c in sys.argv for c in _COMANDOS_OMITIDOS):
            return
        # Escotilla para pruebas que solo necesitan importar un módulo suelto
        # (p. ej. pruebas/prueba_cola.py, que no toca el almacén). NO sirve
        # para saltarse Supabase al servir: el almacén sigue siendo estricto y
        # revienta igual en cuanto alguien lo importe.
        if os.environ.get("SIPRO_OMITIR_ARRANQUE") == "1":
            return

        from django.conf import settings

        print("SIPRO FIRE backend (Django) — arrancando…")

        # --- 1. Supabase es obligatorio -----------------------------------
        # Se comprueba ANTES de importar el almacén para poder dar un mensaje
        # útil en vez de una traza de ImproperlyConfigured.
        if not settings.USAR_SUPABASE:
            self._morir(
                "Faltan las credenciales de Supabase.",
                [
                    "Crea backend_django/.env (copia .env.example) y pon:",
                    "",
                    "    SUPABASE_URL=https://TU-PROYECTO.supabase.co",
                    "    SUPABASE_SERVICE_KEY=<la service_role, NO la anon key>",
                    "",
                    "Las dos están en Supabase → Project Settings → API.",
                ],
            )

        from .almacen import supabase as sb

        estado = sb.comprobar_conexion()
        fallos = {t: e for t, e in estado.items() if e}
        if fallos:
            # Todas las tablas suelen caer por la misma causa: se separa el
            # error escueto de la explicación para no repetirla ocho veces.
            detalle = []
            pistas = []
            for tabla, error in fallos.items():
                escueto, _, pista = str(error).partition("\n  → ")
                detalle.append(f"· {tabla}: {escueto}")
                if pista and pista not in pistas:
                    pistas.append(pista)
            cierre = ([""] + ["→ " + p for p in pistas] if pistas else [
                "",
                "Revisa que ejecutaste el esquema SQL en tu proyecto y que",
                f"las tablas viven en el esquema '{settings.SUPABASE['esquema']}'.",
            ])
            self._morir(
                f"Supabase respondió, pero {len(fallos)} de {len(estado)} tablas no están accesibles.",
                detalle + cierre + [
                    "",
                    "Diagnóstico completo:  python manage.py verificar_supabase",
                ],
            )
        print(f"  Almacén: Supabase ({settings.SUPABASE['url']}) · esquema "
              f"{settings.SUPABASE['esquema']} · {len(estado)} tablas OK")

        # --- 2. Grid en memoria -------------------------------------------
        from .servicios import grid as grid_srv

        t0 = time.time()
        grid_srv.cargar()
        print(f"[grid] {len(grid_srv.obtener_grid())} celdas y "
              f"{len(grid_srv.obtener_focos())} focos cargados en "
              f"{int((time.time() - t0) * 1000)} ms")

        # --- 3. Mantenimiento de arranque ---------------------------------
        from .almacen import db as almacen_db

        try:
            n = almacen_db.migrar_passwords()
            if n:
                print(f"  Usuarios sembrados/con contraseña hasheada: {n}")
        except Exception as e:  # noqa: BLE001
            print(f"  No se pudieron migrar las contraseñas: {e}")

        try:
            n = almacen_db.limpiar_sesiones()
            if n:
                print(f"  Sesiones caducadas eliminadas: {n}")
        except Exception as e:  # noqa: BLE001
            print(f"  No se pudieron limpiar las sesiones: {e}")

        # La fila `permisos` de un despliegue anterior se guardó cuando el rol
        # administrador lo tenía todo en True. Cambiar el valor por defecto en
        # el código no reescribe lo ya guardado, así que se corrige aquí.
        try:
            from .almacen.permisos_defecto import corregir_administrador

            actual = almacen_db.leer_matriz_permisos()
            corregida, cambios = corregir_administrador(actual)
            if cambios:
                almacen_db.guardar_matriz_permisos(corregida)
                print(f"  Matriz de permisos ajustada para 'administrador': {', '.join(cambios)}")
                print("    (el Administrador ya no accede al mapa, monitoreo ni simulación)")
        except Exception as e:  # noqa: BLE001
            print(f"  No se pudo revisar la matriz de permisos: {e}")

        print(f"\n  Orígenes autorizados: {', '.join(settings.CORS_ALLOWED_ORIGINS)}")
        print(f"  NASA FIRMS: {'configurada' if settings.FIRMS['clave'] else 'SIN configurar (añade NASA_FIRMS_MAP_KEY en backend_django/.env)'}\n")

        if settings.PRECALENTAR:
            threading.Thread(target=self._precalentar, daemon=True).start()

    @staticmethod
    def _morir(titulo: str, lineas: list[str]):
        ancho = 74
        print("\n" + "─" * ancho, file=sys.stderr)
        print(f"  SIPRO FIRE no puede arrancar: {titulo}", file=sys.stderr)
        print("─" * ancho, file=sys.stderr)
        for linea in lineas:
            print(f"  {linea}", file=sys.stderr)
        print("─" * ancho + "\n", file=sys.stderr)
        sys.exit(1)

    @staticmethod
    def _precalentar():
        from django.conf import settings

        from .servicios import meteo, terreno

        tareas = [
            ("meteorología de Apolo",
             lambda: meteo.obtener_meteorologia(settings.APOLO["lat"], settings.APOLO["lon"])),
            ("climatología ERA5",
             lambda: meteo.obtener_climatologia(settings.APOLO["lat"], settings.APOLO["lon"])),
            ("capa de terreno OSM", terreno.obtener_terreno_osm),
        ]
        for nombre, tarea in tareas:
            try:
                t0 = time.time()
                tarea()
                print(f"[precalentado] {nombre} lista ({int((time.time() - t0) * 1000)} ms)")
            except Exception as e:  # noqa: BLE001
                print(f"[precalentado] {nombre} no disponible: {e}")
