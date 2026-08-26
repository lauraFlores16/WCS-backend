"""
Prueba de la normalización de SUPABASE_URL.

    cd backend_django
    python pruebas/prueba_url_supabase.py

El fallo que reproduce
──────────────────────
En Supabase → Project Settings → API hay dos URLs muy parecidas:

    Project URL   https://xxxx.supabase.co
    RESTful API   https://xxxx.supabase.co/rest/v1

Si en .env va la segunda, el cliente le añade su propio `/rest/v1/<tabla>` y
la petición acaba saliendo a `/rest/v1/rest/v1/usuarios`. PostgREST responde

    404 · PGRST125 · "Invalid path specified in request URL"

para las OCHO tablas, y el mensaje suena a que el esquema SQL no se ejecutó
—cuando la base está perfectamente—. Aquí se comprueba lo tres cosas:

  1. el normalizador recorta el sufijo y deja intacto lo demás;
  2. con la URL "mala" en .env, el backend ahora consulta bien;
  3. sin normalizar, el doble de PostgREST devuelve el PGRST125 exacto
     (o sea: el error del usuario se reproduce, no se supone).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "pruebas"))

import postgrest_falso  # noqa: E402

PUERTO = 8143
servidor = postgrest_falso.arrancar(PUERTO)
BASE = f"http://127.0.0.1:{PUERTO}"

# La URL se pone MAL a propósito: con el /rest/v1 de más, como la tenía el
# usuario. Si la normalización funciona, todo lo demás debe ir igual.
os.environ["SUPABASE_URL"] = f"{BASE}/rest/v1"
os.environ["SUPABASE_SERVICE_KEY"] = "clave-de-mentira-para-pruebas"
os.environ["SUPABASE_ESQUEMA"] = "public"
os.environ["PRECALENTAR"] = "false"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

from api.almacen import supabase as sb  # noqa: E402
from api.errores import ErrorAPI  # noqa: E402
from config.settings import _url_proyecto_supabase  # noqa: E402

fallos = 0


def ok(condicion, titulo, detalle=""):
    global fallos
    if condicion:
        print(f"  ✓ {titulo}")
    else:
        fallos += 1
        print(f"  ✗ {titulo}" + (f"\n      {detalle}" if detalle else ""))


print("\n1. El normalizador")

casos = [
    ("https://abc.supabase.co", "https://abc.supabase.co", False),
    ("https://abc.supabase.co/", "https://abc.supabase.co", False),
    ("https://abc.supabase.co/rest/v1", "https://abc.supabase.co", True),
    ("https://abc.supabase.co/rest/v1/", "https://abc.supabase.co", True),
    ("  https://abc.supabase.co/rest  ", "https://abc.supabase.co", True),
    ("", "", False),
]
for bruta, esperada, esperaba_aviso in casos:
    limpia, aviso = _url_proyecto_supabase(bruta)
    ok(limpia == esperada,
       f"«{bruta.strip() or '(vacía)'}» → «{limpia}»",
       f"esperaba «{esperada}»")
    ok(bool(aviso) == esperaba_aviso,
       f"   …{'avisa' if esperaba_aviso else 'sin aviso'}")

# Una URL que legítimamente contenga 'rest' en otro sitio no se toca.
limpia, _ = _url_proyecto_supabase("https://rest.miempresa.com")
ok(limpia == "https://rest.miempresa.com",
   "no recorta un dominio que se llama 'rest'", limpia)


print("\n2. Con la URL 'mala' en .env, el backend consulta bien")

ok(settings.SUPABASE["url"] == BASE,
   f"settings recorta /rest/v1 → {settings.SUPABASE['url']}")
ok(settings.SUPABASE["url_bruta"] == f"{BASE}/rest/v1",
   "conserva la URL original para poder explicarlo")
ok(bool(settings.SUPABASE["url_aviso"]), "deja el aviso a la vista")

estado = sb.comprobar_conexion()
malas = {t: e for t, e in estado.items() if e}
ok(not malas, f"las {len(estado)} tablas responden", str(malas)[:300])


print("\n3. Sin normalizar, sale el PGRST125 del usuario")

# Se fuerza la URL sin recortar, exactamente como estaba antes del arreglo.
settings.SUPABASE["url"] = f"{BASE}/rest/v1"
try:
    sb.select("usuarios", "select=*&limit=1")
    ok(False, "debería haber fallado con PGRST125")
except ErrorAPI as e:
    texto = str(e)
    ok("PGRST125" in texto, "reproduce el código exacto del error del usuario", texto[:200])
    ok("Invalid path specified in request URL" in texto,
       "…y el mensaje exacto")
    ok("SUPABASE_URL" in texto and "/rest/v1" in texto,
       "el mensaje ahora dice qué arreglar, no solo el código",
       texto[:400])
    print("\n    Mensaje que vería el usuario:")
    for linea in texto.splitlines():
        print("      " + linea)

settings.SUPABASE["url"] = BASE


print("\n4. Una tabla que no existe se distingue de una ruta mala")

try:
    sb.select("tabla_que_no_existe", "select=*&limit=1")
    ok(False, "debería haber fallado")
except ErrorAPI as e:
    texto = str(e)
    ok("PGRST205" in texto, "tabla inexistente → PGRST205 (no PGRST125)", texto[:200])
    ok("esquema" in texto.lower(), "y la pista habla del esquema SQL", texto[:300])


print("\n" + "─" * 62)
if fallos:
    print(f"  {fallos} comprobación(es) fallaron.")
else:
    print("  La normalización de SUPABASE_URL funciona.")
print("─" * 62)

servidor.shutdown()
sys.exit(1 if fallos else 0)
