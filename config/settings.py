"""
============================================================================
SIPRO FIRE — settings de Django
============================================================================
Equivalente a `backend/config.js` del backend Node/Express que este proyecto
reemplaza. Lee `backend_django/.env` si existe (mismo formato KEY=VALUE que
antes); ninguna de estas variables llega al navegador.

Todo tiene un valor por defecto sensato: `python manage.py runserver` arranca
aunque no crees el .env, igual que pasaba con `node index.js`.
============================================================================
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Lector de .env mínimo (sin depender de python-dotenv, mismo espíritu que el
# `cargarEnv()` de config.js: instalar solo lo imprescindible).
# ---------------------------------------------------------------------------
def _cargar_env(ruta: Path) -> None:
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        limpia = linea.strip()
        if not limpia or limpia.startswith("#"):
            continue
        if "=" not in limpia:
            continue
        clave, _, valor = limpia.partition("=")
        clave = clave.strip()
        valor = valor.strip().strip('"').strip("'")
        os.environ.setdefault(clave, valor)


_cargar_env(BASE_DIR / ".env")


def _bool(nombre: str, defecto: bool) -> bool:
    valor = os.environ.get(nombre)
    if valor is None:
        return defecto
    return valor.strip().lower() in ("1", "true", "si", "sí", "yes", "on")


def _desde_raiz(ruta: str) -> Path:
    p = Path(ruta)
    return p if p.is_absolute() else (BASE_DIR / ruta).resolve()


def _url_proyecto_supabase(bruta: str) -> tuple[str, str]:
    """Deja SUPABASE_URL en la forma que espera el cliente: solo el proyecto.

    Devuelve (url_limpia, aviso). `aviso` es "" si no hubo que tocar nada.

    En Supabase → Project Settings → API aparecen dos cosas parecidas y es muy
    fácil copiar la que no es:

        Project URL   https://xxxx.supabase.co             ← esta
        RESTful API   https://xxxx.supabase.co/rest/v1     ← esta NO

    El cliente añade `/rest/v1/<tabla>` por su cuenta, así que si la URL ya lo
    trae, la petición sale a `/rest/v1/rest/v1/usuarios`. PostgREST no ve una
    tabla llamada `rest`: ve una ruta con segmentos de más, y responde

        PGRST125 · "Invalid path specified in request URL"

    que suena a que faltan las tablas cuando en realidad el esquema está bien.
    En vez de dejar que ese error llegue al usuario, se recorta aquí.
    """
    url = (bruta or "").strip().rstrip("/")
    if not url:
        return "", ""
    for sufijo in ("/rest/v1", "/rest"):
        if url.endswith(sufijo):
            return url[: -len(sufijo)].rstrip("/"), (
                f"SUPABASE_URL traía «{sufijo}» al final; se usa solo la URL del "
                "proyecto porque el cliente ya añade /rest/v1 por su cuenta."
            )
    return url, ""


# ---------------------------------------------------------------------------
# Django "de fábrica"
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "sipro-desarrollo-local-django")
DEBUG = _bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "*").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "corsheaders",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    # Sin CsrfViewMiddleware ni SessionMiddleware a propósito: la sesión no usa
    # el framework de auth de Django, sino un token HMAC propio en una cookie
    # httpOnly (api/auth.py), igual que hacía el backend Express. La cookie
    # nunca la lee JavaScript, y el nivel de protección CSRF es el mismo que
    # tenía el prototipo original (SameSite de la cookie).
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# No usamos el ORM (el almacén es JSON en disco, igual que el prototipo
# Node), pero Django exige un DATABASES configurado. Un sqlite vacío que
# nunca se migra ni se toca.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db_sin_usar.sqlite3",
    }
}

LANGUAGE_CODE = "es"
TIME_ZONE = "America/La_Paz"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Las simulaciones pueden traer series de viento largas (equivalente al
# express.json({ limit: "12mb" }) del backend Node).
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024

# ---------------------------------------------------------------------------
# CORS — mismo criterio que backend/config.js: lista de orígenes explícita,
# credenciales habilitadas (la cookie de sesión viaja con cada petición).
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGENES", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if o.strip()
]
CORS_ALLOW_CREDENTIALS = True

# ---------------------------------------------------------------------------
# SIPRO FIRE — configuración propia de la aplicación (antes backend/config.js)
# ---------------------------------------------------------------------------
PUERTO = int(os.environ.get("PORT", "8000"))

JWT_SECRETO = os.environ.get("JWT_SECRETO", "sipro-desarrollo-local")

# Municipio de Apolo (Franz Tamayo, La Paz).
APOLO = {"lat": -14.65, "lon": -68.25,
         "bbox": {"oeste": -69.05, "sur": -15.05, "este": -67.55, "norte": -13.95}}

FIRMS = {
    "clave": os.environ.get("NASA_FIRMS_MAP_KEY", ""),
    "fuente": os.environ.get("NASA_FIRMS_FUENTE", "VIIRS_SNPP_NRT"),
    # 1 = últimas 24 h disponibles. El endpoint de área acepta de 1 a 10 días.
    # Estaba en 5, que acumulaba detecciones de cinco días y daba sensación de
    # mapa saturado aunque cada punto fuera real.
    "dias": int(os.environ.get("NASA_FIRMS_DIAS", "1")),
    "bbox": "-69.05,-15.05,-67.55,-13.95",
}

# grid.csv, focos.csv y eventos_historicos.json — se empaquetan dentro de
# api/datos/ (copiados de backend/datos/ del prototipo Node).
CSV_DIR = _desde_raiz(os.environ.get("CSV_DIR", "./api/datos"))

# ---------------------------------------------------------------------------
# SUPABASE — el almacén del sistema
# ---------------------------------------------------------------------------
# Usuarios, sesiones, escenarios, alertas, calibraciones, bitácora, informes y
# la matriz de permisos viven en PostgreSQL (Supabase). El backend habla con
# la API REST (PostgREST) usando la SERVICE ROLE KEY, que omite RLS y NUNCA
# sale de este proceso: el navegador jamás toca Supabase directamente.
#
# El modo es ESTRICTO: si faltan las credenciales o la base no responde, el
# backend no arranca. Así nunca se escribe en un almacén de repuesto por
# accidente, que era el riesgo de tener dos caminos posibles.
_SUPABASE_URL, _SUPABASE_URL_AVISO = _url_proyecto_supabase(
    os.environ.get("SUPABASE_URL", ""))

SUPABASE = {
    "url": _SUPABASE_URL,
    # URL tal cual la escribió el usuario en .env, y el aviso si hubo que
    # recortarla. `verificar_supabase` los enseña para que se entienda por qué
    # la URL efectiva no es exactamente la que puso.
    "url_bruta": os.environ.get("SUPABASE_URL", "").strip(),
    "url_aviso": _SUPABASE_URL_AVISO,
    "service_key": os.environ.get("SUPABASE_SERVICE_KEY", ""),
    # Tu esquema es `public` (el que expone Supabase por defecto). Se deja
    # configurable por si algún día mueves las tablas a un esquema propio.
    "esquema": os.environ.get("SUPABASE_ESQUEMA", "public"),
    "timeout_s": float(os.environ.get("SUPABASE_TIMEOUT_S", "20")),
}
USAR_SUPABASE = bool(SUPABASE["url"] and SUPABASE["service_key"])

# Semilla de los 4 usuarios demo la primera vez que la tabla está vacía.
# Ponlo en false si prefieres crear las cuentas a mano en Supabase.
SEMBRAR_USUARIOS_DEMO = _bool("SEMBRAR_USUARIOS_DEMO", True)

# Carpeta heredada del prototipo (ya no se usa como almacén; se conserva
# porque `.env.example` la menciona y algún despliegue viejo puede apuntar ahí).
DATOS_DIR = _desde_raiz(os.environ.get("DATOS_DIR", "./almacen_datos"))

# Caché en disco de las respuestas de las APIs externas.
CACHE_DIR = _desde_raiz(os.environ.get("CACHE_DIR", "./.cache"))

PRECALENTAR = _bool("PRECALENTAR", True)

# TTLs de caché (segundos) — mismos valores que TTL de config.js (allí en ms).
TTL_SEGUNDOS = {
    "meteo": 10 * 60,
    "climatologia": 30 * 24 * 60 * 60,
    "viento_historico": 365 * 24 * 60 * 60,
    "dem": None,  # nunca caduca
    "osm": 7 * 24 * 60 * 60,
    "firms": 10 * 60,
}

for _dir in (DATOS_DIR, CACHE_DIR):
    _dir.mkdir(parents=True, exist_ok=True)
