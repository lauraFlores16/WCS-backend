"""
Comprueba la conexión con Supabase y el estado de las tablas.

    python manage.py verificar_supabase
    python manage.py verificar_supabase --sembrar

Está pensado para ser lo PRIMERO que corres tras poner las credenciales en
backend_django/.env: dice si la URL y la clave sirven, qué tablas ve, cuántas
filas tiene cada una, y si los usuarios demo están sembrados. Corre aunque la
conexión esté rota (por eso `apps.py` lo excluye del arranque normal).
"""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from ...almacen import supabase as sb


def _rol_de_la_clave(clave: str) -> str:
    """Dice si la clave es la `service_role` o la `anon`, sin salir del equipo.

    Las claves de Supabase son JWT: el payload va en base64url y lleva el
    claim `role`. Se lee en local y solo para avisar — no se verifica la firma
    ni se manda a ningún sitio; el que decide si vale es Supabase.

    Los proyectos nuevos usan el formato `sb_secret_…` / `sb_publishable_…`,
    que no es un JWT pero se distingue por el prefijo.
    """
    import base64
    import json as _json

    if clave.startswith("sb_secret_"):
        return "service_role"
    if clave.startswith("sb_publishable_"):
        return "anon"

    partes = clave.split(".")
    if len(partes) != 3:
        return ""
    try:
        relleno = "=" * (-len(partes[1]) % 4)
        datos = _json.loads(base64.urlsafe_b64decode(partes[1] + relleno))
    except Exception:  # noqa: BLE001 — una clave ilegible simplemente no se juzga
        return ""
    return str(datos.get("role") or "")


class Command(BaseCommand):
    help = "Comprueba la conexión con Supabase, las tablas y los usuarios demo."

    # Sin comprobaciones del sistema: cargarían el URLconf → views → almacen/db,
    # que revienta a propósito cuando faltan las credenciales. Este comando
    # existe justamente para diagnosticar ese caso, así que tiene que poder
    # correr con la configuración rota.
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument(
            "--sembrar", action="store_true",
            help="Si la tabla `usuarios` está vacía, crea los 4 usuarios demo.",
        )

    def handle(self, *args, **opciones):
        cfg = settings.SUPABASE

        self.stdout.write(self.style.MIGRATE_HEADING("\n1. Configuración"))
        if not sb.configurado():
            self.stdout.write(self.style.ERROR(
                "   ✗ Faltan SUPABASE_URL y/o SUPABASE_SERVICE_KEY.\n"
                "     Créalas en backend_django/.env (copia .env.example).\n"
                "     Están en Supabase → Project Settings → API.\n"
                "     Usa la service_role (secreta), NO la anon key."
            ))
            return
        clave = cfg["service_key"]
        # Se enseñan las dos URLs por separado: la que está escrita en .env y
        # la que de verdad se va a usar. Si no coinciden hay que decir por qué.
        self.stdout.write(f"   En .env  : {cfg.get('url_bruta') or cfg['url']}")
        self.stdout.write(f"   Petición : {cfg['url']}/rest/v1/<tabla>")
        self.stdout.write(f"   Esquema  : {cfg['esquema']}")
        if cfg.get("url_aviso"):
            self.stdout.write(self.style.WARNING(f"   ⚠ {cfg['url_aviso']}"))

        self.stdout.write(f"   Clave    : {clave[:8]}…{clave[-4:]} ({len(clave)} caracteres)")
        rol = _rol_de_la_clave(clave)
        if rol == "service_role":
            self.stdout.write(self.style.SUCCESS("   ✓ Es la service_role (omite RLS)."))
        elif rol == "anon":
            self.stdout.write(self.style.ERROR(
                "   ✗ Es la ANON key, no la service_role.\n"
                "     La anon está sujeta a RLS y no verá estas tablas.\n"
                "     Supabase → Project Settings → API → service_role (secret)."))
        elif len(clave) < 40:
            self.stdout.write(self.style.WARNING(
                "   ⚠ La clave parece demasiado corta para ser una service_role."))

        self.stdout.write(self.style.MIGRATE_HEADING("\n2. Tablas"))
        estado = sb.comprobar_conexion()
        fallos = 0
        # Las 8 tablas suelen fallar por la MISMA causa, y el mensaje trae la
        # explicación de cómo arreglarla. Repetirla 8 veces esconde el resto
        # del informe: se guarda aparte y se enseña una sola vez al final.
        pistas: list[str] = []
        vacias = 0
        legibles = 0
        for tabla, error in estado.items():
            if error:
                fallos += 1
                escueto, _, pista = str(error).partition("\n  → ")
                if pista and pista not in pistas:
                    pistas.append(pista)
                self.stdout.write(self.style.ERROR(f"   ✗ {tabla:<15} {escueto}"))
                continue
            try:
                filas = sb.select(tabla, "select=*&limit=1000")
                n = len(filas)
                legibles += 1
                if n == 0:
                    vacias += 1
                sufijo = "+" if n >= 1000 else ""
                self.stdout.write(self.style.SUCCESS(f"   ✓ {tabla:<15} {n}{sufijo} filas"))
            except Exception as e:  # noqa: BLE001
                self.stdout.write(self.style.WARNING(f"   ~ {tabla:<15} accesible, pero no se pudo contar: {e}"))

        # --- El «✓ 0 filas» puede ser mentira -------------------------------
        # Con RLS activo y sin política para el rol de la clave, un SELECT no
        # falla: devuelve el conjunto VACÍO. Así que las ocho tablas salen con
        # su ✓ y «0 filas», que se lee como «la base está recién creada» cuando
        # en realidad significa «esta clave no ve nada». Peor todavía: el
        # apartado 3 mandaba entonces a `--sembrar`, que es el camino
        # equivocado. Si la clave no es la service_role y TODO sale vacío, hay
        # que decirlo aquí y no dejar que el informe siga como si nada.
        if not fallos and legibles and vacias == legibles and rol != "service_role":
            self.stdout.write(self.style.ERROR(
                f"\n   ⚠ Las {legibles} tablas responden, pero TODAS con 0 filas.\n"
                "     Con una clave sujeta a RLS eso no significa «están vacías»:\n"
                "     un SELECT sin política devuelve el conjunto vacío en vez de\n"
                "     dar error. Es decir, el ✓ de arriba no prueba que la clave\n"
                "     sirva — solo que la URL y el esquema son correctos.\n"
                "\n"
                "     Pon la service_role en SUPABASE_SERVICE_KEY y repite esto\n"
                "     antes de sembrar nada."))

        if fallos:
            self.stdout.write(self.style.ERROR(
                f"\n   {fallos} tabla(s) no accesibles."))
            if pistas:
                self.stdout.write(self.style.MIGRATE_HEADING("\n   Qué hacer"))
                for p in pistas:
                    self.stdout.write(self.style.WARNING("   → " + p.replace("\n  ", "\n  ")))
            else:
                self.stdout.write(self.style.ERROR(
                    "   Comprueba que ejecutaste el esquema SQL y que las tablas\n"
                    f"   están en el esquema '{cfg['esquema']}'. Si usas otro,\n"
                    "   ponlo en SUPABASE_ESQUEMA."))
            return

        self.stdout.write(self.style.MIGRATE_HEADING("\n3. Usuarios"))
        usuarios = sb.select("usuarios", "select=id,email,rol,activo&order=orden.asc")
        if usuarios:
            for u in usuarios:
                marca = "activo" if u.get("activo") else "DESACTIVADO"
                self.stdout.write(f"   · {u['email']:<28} {u['rol']:<15} {marca}")
        elif rol == "anon":
            # No se siembra con la anon key. El INSERT lo rechazaría RLS de
            # todas formas, pero decir «la tabla está vacía, siembra» cuando el
            # problema es la clave manda a perseguir el error equivocado.
            self.stdout.write(self.style.ERROR(
                "   No se lee ningún usuario, pero con la anon key eso no dice nada:\n"
                "   RLS puede estar escondiendo filas que sí existen. Cambia la clave\n"
                "   por la service_role antes de sembrar."))
        elif opciones["sembrar"]:
            from ...almacen import db_supabase
            n = db_supabase.migrar_passwords()
            self.stdout.write(self.style.SUCCESS(f"   ✓ Sembrados {n} usuarios demo (contraseña: demo1234)"))
        else:
            self.stdout.write(self.style.WARNING(
                "   La tabla está vacía. Vuelve a lanzar con --sembrar para crear\n"
                "   los 4 usuarios demo, o créalos a mano en Supabase."))

        self.stdout.write(self.style.MIGRATE_HEADING("\n4. Escritura"))
        try:
            from ...almacen import db_supabase
            db_supabase.registrar_bitacora({
                "usuario": "verificar_supabase", "accion": "Comprobó la conexión",
                "detalle": "Prueba de escritura del comando de verificación", "tipo": "sys",
            })
            self.stdout.write(self.style.SUCCESS("   ✓ INSERT de prueba en `bitacora` correcto"))
        except Exception as e:  # noqa: BLE001
            self.stdout.write(self.style.ERROR(
                f"   ✗ No se pudo escribir: {e}\n"
                "     ¿Seguro que la clave es la service_role y no la anon?"))
            return

        self.stdout.write(self.style.SUCCESS("\n   Todo listo. Ya puedes: python manage.py runserver 8000\n"))
