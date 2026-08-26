"""
Prueba del control de peticiones salientes (api/lib/cola.py) contra un
servidor local que siempre responde 429.

    cd backend_django
    python pruebas/prueba_cola.py

Comprueba las tres cosas que se cambiaron tras la tormenta de reintentos
contra la API de elevación de Open-Meteo:

  · un 429 se reintenta UNA vez, no cuatro (reintentar un límite de cuota
    consume la cuota que intentas sortear);
  · el cortacircuitos se abre y corta la sangría en pocas peticiones;
  · la elevación tiene presupuesto propio, así que quedarse sin cuota de DEM
    NO deja al sistema sin meteorología.
"""
from __future__ import annotations

import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ["SUPABASE_URL"] = "http://127.0.0.1:1"      # no se usa
os.environ["SUPABASE_SERVICE_KEY"] = "no-se-usa"
os.environ["PRECALENTAR"] = "false"
os.environ["SIPRO_OMITIR_ARRANQUE"] = "1"  # esta prueba no toca el almacén

import django  # noqa: E402

django.setup()

from api.lib import cola  # noqa: E402

PUERTO = 8151
peticiones = {"total": 0}
_candado = threading.Lock()


class SiempreLimitado(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):
        pass

    def do_GET(self):
        with _candado:
            peticiones["total"] += 1
        cuerpo = b'{"error":"rate limited"}'
        self.send_response(429)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)


servidor = ThreadingHTTPServer(("127.0.0.1", PUERTO), SiempreLimitado)
threading.Thread(target=servidor.serve_forever, daemon=True).start()

# El host de pruebas se registra con los mismos límites que Open-Meteo pero
# sin esperas, para no tardar un minuto en comprobar la lógica.
cola.LIMITES[f"127.0.0.1:{PUERTO}"] = {"intervalo_s": 0.0, "por_minuto": 1000}
cola.ESPERA_BASE_S = 0.01

fallos = []


def ok(condicion, descripcion, extra=""):
    if condicion:
        print(f"  ✓ {descripcion}")
    else:
        fallos.append(descripcion)
        print(f"  ✗ {descripcion}  {extra}")


URL = f"http://127.0.0.1:{PUERTO}/v1/algo"

print("\n1. Un 429 no se reintenta cuatro veces")
peticiones["total"] = 0
try:
    cola.pedir(URL, etiqueta="Servicio de prueba")
    ok(False, "debería haber fallado")
except cola.ErrorExterno as e:
    esperadas = cola.REINTENTOS_LIMITE_TASA + 1
    ok(peticiones["total"] == esperadas,
       f"1 intento + {cola.REINTENTOS_LIMITE_TASA} reintento = {esperadas} peticiones "
       f"(hizo {peticiones['total']})")
    ok(e.estado_http == 503, "se traduce a 503 con reintentar_en_s para el frontend")

print("\n2. El cortacircuitos corta la sangría")
peticiones["total"] = 0
errores_seguidos = 0
for _ in range(6):
    try:
        cola.pedir(URL, etiqueta="Servicio de prueba")
    except cola.ErrorExterno:
        errores_seguidos += 1
ok(errores_seguidos == 6, "las 6 llamadas fallan de cara al que las pide")
# Tras FALLOS_PARA_ABRIR fallos el circuito se abre y ya no se toca la red
tope = (cola.FALLOS_PARA_ABRIR - 1) * (cola.REINTENTOS_LIMITE_TASA + 1)
ok(peticiones["total"] <= tope + 2,
   f"deja de tocar la red pronto: {peticiones['total']} peticiones en 6 llamadas "
   f"(antes del arreglo habrían sido {6 * 5})")

estado = cola.estado_colas()[f"127.0.0.1:{PUERTO}"]
ok(estado["disponible"] is False, "el host queda marcado como no disponible")
ok(estado["reintenta_en_s"] > 0, f"y dice cuándo reintentar ({estado['reintenta_en_s']} s)")

print("\n3. La elevación tiene presupuesto propio")
ok(cola.clave_limite("https://api.open-meteo.com/v1/elevation?latitude=1&longitude=2")
   == "api.open-meteo.com/v1/elevation",
   "la elevación se contabiliza aparte")
ok(cola.clave_limite("https://api.open-meteo.com/v1/forecast?latitude=1")
   == "api.open-meteo.com",
   "el pronóstico mantiene el presupuesto del host")
ok(cola.clave_limite("https://api.open-meteo.com/v1/elevation")
   != cola.clave_limite("https://api.open-meteo.com/v1/forecast"),
   "quedarse sin cuota de DEM NO deja al sistema sin meteorología")

print("\n4. Todas las peticiones se identifican")
ok("SIPRO" in cola.USER_AGENT,
   f"User-Agent propio en vez del genérico de requests ({cola.USER_AGENT[:38]}…)")

servidor.shutdown()

print("\n" + "─" * 62)
if fallos:
    print(f"  {len(fallos)} COMPROBACIONES FALLIDAS:")
    for f in fallos:
        print(f"    · {f}")
    sys.exit(1)
print("  Control de peticiones salientes: todo correcto.")
print("─" * 62 + "\n")
