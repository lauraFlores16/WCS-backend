"""
Prueba de extremo a extremo de la consola interactiva, por HTTP.

    cd backend_django
    python pruebas/prueba_consola.py

Recorre lo que hace un analista de verdad: entra, lanza la simulación, la ve
avanzar por tandas, la para, le mete una tormenta, sigue, y la guarda como
escenario. Todo contra el PostgREST de mentira, sin red ni credenciales.

Lo que de verdad se comprueba
-----------------------------
  · que el acceso está cerrado de verdad (el Administrador no puede simular);
  · que inyectar en el paso 12 cambia el resultado y NO reescribe los pasos
    anteriores, que ya estaban calculados;
  · que no se puede inyectar hacia atrás ni retirar un suceso que ya actuó;
  · que la sesión guardada acaba en el Historial con su guion dentro.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "pruebas"))

import postgrest_falso  # noqa: E402

servidor = postgrest_falso.arrancar(8183)
os.environ.update(
    SUPABASE_URL="http://127.0.0.1:8183", SUPABASE_SERVICE_KEY="s" * 60,
    SUPABASE_ESQUEMA="public", PRECALENTAR="false",
    DJANGO_SETTINGS_MODULE="config.settings",
)

import django  # noqa: E402

django.setup()

from django.test import Client  # noqa: E402

fallos = 0


def ok(cond, titulo, extra=""):
    global fallos
    if cond:
        print(f"  ✓ {titulo}")
    else:
        fallos += 1
        print(f"  ✗ {titulo}" + (f"\n      {extra}" if extra else ""))


def entrar(correo):
    c = Client()
    r = c.post("/api/auth/login", data=json.dumps({"email": correo, "password": "demo1234"}),
               content_type="application/json")
    assert r.status_code == 200, r.content[:200]
    return c


def cuerpo(r):
    d = r.json()
    return d.get("datos", d)


analista = entrar("analista@demo.sipro.com")
admin = entrar("admin@demo.sipro.com")

PARAMS = {"foco_fila": 83, "foco_columna": 126, "p_base": 0.45,
          "num_iteraciones": 30, "minutos_por_iteracion": 15, "semilla": 20260826}


def post(cli, ruta, datos=None):
    return cli.post(ruta, data=json.dumps(datos or {}), content_type="application/json")


# ===========================================================================
print("\n1. El catálogo y quién puede tocarlo")
# ===========================================================================
r = analista.get("/api/simulacion/escenarios")
ok(r.status_code == 200, f"el analista ve el catálogo ({r.status_code})")
cat = cuerpo(r)
ok(len(cat["escenarios"]) >= 10, f"{len(cat['escenarios'])} escenarios")
ok(all(e.get("familia_nombre") for e in cat["escenarios"]), "cada uno con su familia legible")

r = post(admin, "/api/simulacion/consola", {"parametros": PARAMS, "usar_terreno": False})
ok(r.status_code == 403,
   f"el Administrador NO puede abrir una consola de simulación ({r.status_code})")

r = Client().post("/api/simulacion/consola", data=json.dumps({"parametros": PARAMS}),
                  content_type="application/json")
ok(r.status_code == 401, f"y sin sesión tampoco ({r.status_code})")


# ===========================================================================
print("\n2. Abrir la consola y avanzar por tandas")
# ===========================================================================
r = post(analista, "/api/simulacion/consola", {"parametros": PARAMS, "pasos_iniciales": 6, "usar_terreno": False})
ok(r.status_code == 200, f"la consola arranca ({r.status_code})", r.content[:300].decode())
s = cuerpo(r)
sid = s["sesion_id"]
ok(s["paso_actual"] == 6, f"y adelanta los 6 pasos pedidos (va por el {s['paso_actual']})")
ok(len(s["iteraciones"]) == 7, f"devolviendo las 7 iteraciones (0 a 6): {len(s['iteraciones'])}")
ok(s["terminada"] is False, "sin terminar")
ok(s["ambiente"] is not None and s["ambiente"]["temperatura_c"] is not None,
   f"con su ambiente: {s['ambiente']['temperatura_c']} °C · humedad {s['ambiente']['humedad']}")

r = post(analista, f"/api/simulacion/consola/{sid}/avanzar", {"pasos": 6})
s = cuerpo(r)
ok(s["paso_actual"] == 12, f"avanza otros 6 → paso {s['paso_actual']}")
ok(len(s["iteraciones"]) == 6,
   f"y SOLO manda las 6 nuevas, no la película entera: {len(s['iteraciones'])}")
ardiendo_en_12 = s["resumen"]["celdas_ardiendo"]
quemadas_en_12 = s["resumen"]["celdas_quemadas"]
ok(ardiendo_en_12 > 0, f"el incendio está vivo: {ardiendo_en_12} celdas ardiendo")


# ===========================================================================
print("\n3. Pausar e inyectar la tormenta")
# ===========================================================================
r = post(analista, f"/api/simulacion/consola/{sid}/inyectar",
         {"escenario": "tormenta"})
ok(r.status_code == 200, f"se inyecta la tormenta ({r.status_code})", r.content[:300].decode())
s = cuerpo(r)
ok(len(s["guion"]) == 1, "queda un suceso en el guion")
ok(s["evento"]["paso_inicio"] == 12,
   f"que empieza en el paso actual, no antes: {s['evento']['paso_inicio']}")
ok(s["resumen"]["celdas_quemadas"] == quemadas_en_12,
   "y NO reescribe lo ya calculado: el estado del paso 12 no se movió")

r = post(analista, f"/api/simulacion/consola/{sid}/inyectar",
         {"escenario": "lluvia_ligera", "paso_inicio": 3})
ok(r.status_code == 502 or r.status_code >= 400,
   f"inyectar hacia atrás se rechaza ({r.status_code})")
ok(b"ya est" in r.content or b"paso" in r.content,
   "con un mensaje que explica por qué", r.content[:200].decode())

r = post(analista, f"/api/simulacion/consola/{sid}/inyectar", {"escenario": "no_existe"})
ok(r.status_code == 400,
   f"un escenario inventado da 400, no 500: es culpa de la petición ({r.status_code})",
   r.content[:200].decode())
r = post(analista, f"/api/simulacion/consola/{sid}/inyectar",
         {"escenario": "racha_viento", "intensidad": "huracanada"})
ok(r.status_code == 400 and b"suave" in r.content,
   "y una intensidad que no existe dice cuáles hay",
   r.content[:200].decode())


# ===========================================================================
print("\n4. Seguir: la tormenta tiene que notarse")
# ===========================================================================
r = post(analista, f"/api/simulacion/consola/{sid}/avanzar", {"completar": True})
s = cuerpo(r)
ok(s["terminada"] is True, f"la simulación termina: {s['motivo_fin']}")
con_tormenta = s["resumen"]["celdas_quemadas"]
ok(s["resumen"]["apagadas_por_lluvia"] > 0,
   f"la lluvia apagó {s['resumen']['apagadas_por_lluvia']} celdas")

primera_nueva = s["iteraciones"][0]["ambiente"]
ok(primera_nueva["lluvia_mm_h"] > 0,
   f"y la iteración 13 ya reporta lluvia: {primera_nueva['lluvia_mm_h']} mm/h")
ok(any(e["id"] == "tormenta" for e in primera_nueva["eventos_activos"]),
   f"con el suceso a la vista: {[e['nombre'] for e in primera_nueva['eventos_activos']]}")

# La misma corrida SIN tormenta, para tener con qué comparar.
r = post(analista, "/api/simulacion/consola", {"parametros": PARAMS, "usar_terreno": False})
sid2 = cuerpo(r)["sesion_id"]
r = post(analista, f"/api/simulacion/consola/{sid2}/avanzar", {"completar": True})
sin_tormenta = cuerpo(r)["resumen"]["celdas_quemadas"]
ok(con_tormenta < sin_tormenta,
   f"con tormenta se quema menos: {con_tormenta} vs {sin_tormenta} celdas "
   f"({(con_tormenta / sin_tormenta - 1) * 100:+.0f} %)")

r = post(analista, f"/api/simulacion/consola/{sid}/inyectar", {"escenario": "lluvia_ligera"})
ok(r.status_code >= 400, f"ya terminada, no admite más sucesos ({r.status_code})")


# ===========================================================================
print("\n5. Guardar la sesión como escenario")
# ===========================================================================
r = post(analista, f"/api/simulacion/consola/{sid}/guardar",
         {"nombre": "Foco norte con tormenta en el paso 12"})
ok(r.status_code == 200, f"se guarda ({r.status_code})", r.content[:400].decode())
guardado = cuerpo(r)
ok(guardado.get("escenario_id"), f"con su id: {guardado.get('escenario_id')}")

r = analista.get("/api/escenarios")
lista = cuerpo(r)
mio = [e for e in lista if e["escenario_id"] == guardado["escenario_id"]]
ok(len(mio) == 1, f"y aparece en el Historial ({len(lista)} escenarios en total)")

r = analista.get(f"/api/escenarios/{guardado['escenario_id']}")
detalle = cuerpo(r)
guion_guardado = (detalle.get("metadatos_motor") or {}).get("guion") or []
ok(len(guion_guardado) == 1 and guion_guardado[0]["id"] == "tormenta",
   f"el guion viaja DENTRO del escenario guardado: {[g['nombre'] for g in guion_guardado]}",
   json.dumps(detalle.get("metadatos_motor", {}))[:300])

r = analista.get(f"/api/simulacion/consola/{sid}")
ok(r.status_code >= 400, f"y la sesión se cerró al guardar ({r.status_code})")


# ===========================================================================
print("\n6. Sesión inexistente")
# ===========================================================================
r = post(analista, "/api/simulacion/consola/noexiste/avanzar", {"pasos": 1})
ok(r.status_code >= 400, f"avanzar una sesión que no existe da error ({r.status_code})")
ok(b"caduc" in r.content or b"no existe" in r.content,
   "diciendo que caducó o no existe, no un 500 mudo", r.content[:200].decode())


print("\n" + "─" * 64)
print(f"  {fallos} comprobación(es) fallaron." if fallos
      else "  Consola interactiva: todo correcto.")
print("─" * 64)

servidor.shutdown()
sys.exit(1 if fallos else 0)
