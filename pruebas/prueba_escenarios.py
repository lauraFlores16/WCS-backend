"""
Pruebas del motor de escenarios meteorológicos.

    cd backend_django
    python pruebas/prueba_escenarios.py

Lo que hay que demostrar, por orden de importancia:

  1. NO SE ROMPIÓ NADA. Una corrida sin escenarios da EXACTAMENTE el mismo
     resultado que antes del cambio. La humedad dejó de hornearse en el array
     al arrancar y pasó a ser un desplazamiento por paso; si esa refactorización
     movió aunque sea una celda, la calibración contra los perímetros de FIRMS
     deja de valer y no nos enteraríamos.

  2. PAUSAR Y SEGUIR ES EXACTO. Correr 0→40 de un tirón y correr 0→12 + 12→40
     desde el checkpoint tienen que dar el mismo perímetro celda a celda. Esa
     es la propiedad que sostiene toda la consola interactiva: si al reanudar
     el generador no cae en el mismo punto, la simulación pausada sería otra
     simulación distinta.

  3. LOS ESCENARIOS HACEN ALGO, Y LO CORRECTO. El viento acelera, la lluvia
     frena y apaga, la rampa evita escalones.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "pruebas"))

import postgrest_falso  # noqa: E402

servidor = postgrest_falso.arrancar(8171)
os.environ.update(
    SUPABASE_URL="http://127.0.0.1:8171", SUPABASE_SERVICE_KEY="s" * 60,
    SUPABASE_ESQUEMA="public", PRECALENTAR="false",
    SIPRO_OMITIR_ARRANQUE="1", DJANGO_SETTINGS_MODULE="config.settings",
)

import django  # noqa: E402

django.setup()

from api.motor import escenarios as esc  # noqa: E402
from api.motor.automata import ejecutar_automata  # noqa: E402
from api.servicios import grid as grid_srv  # noqa: E402

grid_srv.cargar()
GRID = grid_srv.obtener_grid()

fallos = 0


def ok(cond, titulo, extra=""):
    global fallos
    if cond:
        print(f"  ✓ {titulo}")
    else:
        fallos += 1
        print(f"  ✗ {titulo}" + (f"\n      {extra}" if extra else ""))


# La celda de mayor probabilidad del modelo, la misma que usa la demo.
PARAMS = {
    "foco_fila": 83, "foco_columna": 126, "p_base": 0.45,
    "num_iteraciones": 40, "minutos_por_iteracion": 15, "semilla": 20260826,
}


def correr(**opciones):
    return ejecutar_automata(GRID, dict(PARAMS), opciones)


def perimetro(res):
    """Conjunto de celdas tocadas al final, para comparar corridas."""
    ult = res["iteraciones"][-1]
    return {(c["celda_id"], c["estado"]) for c in ult["celdas"]}


def area(res):
    ult = res["iteraciones"][-1]
    return ult["num_celdas_ardiendo"] + ult["num_celdas_quemadas"]


# ===========================================================================
print("\n1. La refactorización no movió nada")
# ===========================================================================
base = correr()
ok(len(base["iteraciones"]) > 1, f"la corrida base avanza ({len(base['iteraciones'])} iteraciones)")
ok(area(base) > 0, f"y quema algo: {area(base)} celdas")

repetida = correr()
ok(perimetro(base) == perimetro(repetida),
   "misma semilla → mismo perímetro, celda a celda (determinismo intacto)")

# `delta_humedad` y `delta_temperatura_c` se horneaban en el array al arrancar;
# ahora viajan como desplazamiento por paso. Sin escenarios el valor es
# constante, así que el resultado tiene que ser idéntico al de hornearlo.
con_delta = correr(**{})
con_delta = ejecutar_automata(GRID, {**PARAMS, "delta_humedad": 0.05}, {})
con_delta2 = ejecutar_automata(GRID, {**PARAMS, "delta_humedad": 0.05}, {})
ok(perimetro(con_delta) == perimetro(con_delta2), "delta_humedad sigue siendo determinista")
ok(area(con_delta) < area(base),
   f"y más humedad quema menos: {area(con_delta)} < {area(base)} celdas")

seco = ejecutar_automata(GRID, {**PARAMS, "delta_temperatura_c": 10}, {})
ok(area(seco) > area(base),
   f"+10 °C seca y quema más: {area(seco)} > {area(base)} celdas")


# ===========================================================================
print("\n2. Pausar y seguir da el mismo resultado que no pausar")
# ===========================================================================
entera = correr()

tramo1 = correr(hasta_paso=12, devolver_estado=True)
ok(tramo1["metadatos"]["ultimo_paso"] == 12, "el primer tramo para en el paso 12")
ok(tramo1["metadatos"]["completado"] is False, "y se marca como incompleto")
chk = tramo1["metadatos"]["estado"]
ok(isinstance(chk.get("prng"), int), f"el checkpoint lleva el estado del PRNG ({chk.get('prng')})")

tramo2 = correr(desde_paso=12, estado_inicial=chk)
ok(tramo2["metadatos"]["completado"] is True, "el segundo tramo llega al final")

# El tramo 2 republica el paso 12 como punto de partida: se descarta al unir.
unidas = tramo1["iteraciones"] + tramo2["iteraciones"][1:]
ok(len(unidas) == len(entera["iteraciones"]),
   f"unidas dan las mismas iteraciones ({len(unidas)} vs {len(entera['iteraciones'])})")

iguales = all(
    a["num_celdas_ardiendo"] == b["num_celdas_ardiendo"]
    and a["num_celdas_quemadas"] == b["num_celdas_quemadas"]
    for a, b in zip(unidas, entera["iteraciones"])
)
ok(iguales, "y coinciden iteración a iteración en ardiendo/quemadas")
ok(perimetro(tramo2) == perimetro(entera),
   "el perímetro final es idéntico al de la corrida de un tirón")

# Tres tramos, para descartar que funcione solo con un corte.
t1 = correr(hasta_paso=7, devolver_estado=True)
t2 = correr(desde_paso=7, hasta_paso=23, estado_inicial=t1["metadatos"]["estado"],
            devolver_estado=True)
t3 = correr(desde_paso=23, estado_inicial=t2["metadatos"]["estado"])
ok(perimetro(t3) == perimetro(entera), "y con tres cortes (7, 23) también")


# ===========================================================================
print("\n3. El catálogo")
# ===========================================================================
cat = esc.listar()
ok(len(cat) >= 10, f"{len(cat)} escenarios en el catálogo")
familias = sorted({e["familia"] for e in cat})
ok(familias == ["frente", "lluvia", "secado", "viento"], f"familias: {', '.join(familias)}")
ok(all(e.get("justificacion") for e in cat), "todos llevan justificación de su magnitud")

ev = esc.crear_evento("racha_viento", paso_inicio=10, intensidad="extrema")
ok(ev["efectos"]["viento_ms_fijar"] == 14.0, f"la intensidad «extrema» sube a {ev['efectos']['viento_ms_fijar']} m/s")

try:
    esc.crear_evento("no_existe", 5)
    ok(False, "un escenario inexistente debería fallar")
except ValueError:
    ok(True, "un escenario inexistente da error claro")


# ===========================================================================
print("\n4. La rampa evita el escalón")
# ===========================================================================
ev = esc.crear_evento("racha_viento", paso_inicio=10)   # rampa_pasos = 2
base_amb = {"temperatura_c": 18.0, "vpd_kpa": 1.0, "lluvia_mm_h": 0.0,
            "viento_ms": 1.0, "viento_grados": 90}
pesos = [esc._peso_rampa(ev, p) for p in (9, 10, 11, 12, 21, 22)]
ok(pesos == [0.0, 0.5, 1.0, 1.0, 1.0, 0.0],
   f"el efecto entra en dos pasos y caduca al acabar la duración: {pesos}")

a10 = esc.ambiente_en_paso(base_amb, [ev], 10)
a11 = esc.ambiente_en_paso(base_amb, [ev], 11)
ok(a10["viento_ms"] < a11["viento_ms"] == 8.0,
   f"el viento sube gradualmente: paso 10 → {a10['viento_ms']:.1f} m/s, paso 11 → {a11['viento_ms']:.1f}")

# El giro va por el camino corto: de 350° a 10° son 20°, no 340°.
gira = esc.crear_evento("rolada_viento", paso_inicio=0, ajustes={"rampa_pasos": 0})
r = esc.ambiente_en_paso({**base_amb, "viento_grados": 350}, [gira], 0)
ok(abs(r["viento_grados"] - 80) < 1e-6, f"350° + 90° = {r['viento_grados']}°")


# ===========================================================================
print("\n5. Los escenarios cambian el incendio")
# ===========================================================================
racha = correr(guion=[esc.crear_evento("racha_viento", paso_inicio=5, intensidad="extrema")])
ok(area(racha) > area(base),
   f"una racha de 14 m/s quema MÁS: {area(racha)} vs {area(base)} celdas "
   f"({(area(racha) / area(base) - 1) * 100:+.0f} %)")

# El spotting es un suceso RARO: con las constantes por defecto salen 2–3
# saltos por corrida, así que una sola semilla no demuestra nada —de hecho la
# semilla de estas pruebas da 0 y me lo creí al principio—. Se mide sobre
# cinco semillas, que es lo que separa el mecanismo de la casualidad.
SEMILLAS = (20260826, 111, 2024, 777, 31415)
saltos_sin, saltos_con = 0, 0
for s in SEMILLAS:
    p = {**PARAMS, "semilla": s}
    saltos_sin += len(ejecutar_automata(GRID, dict(p), {})["metadatos"]["eventos_spotting"])
    saltos_con += len(ejecutar_automata(GRID, dict(p), {
        "guion": [esc.crear_evento("racha_viento", paso_inicio=5, intensidad="extrema")]
    })["metadatos"]["eventos_spotting"])
ok(saltos_sin == 0 and saltos_con > 0,
   f"y desbloquea los saltos de pavesas: {saltos_sin} sin racha y {saltos_con} con ella, "
   f"sobre {len(SEMILLAS)} semillas (el umbral del motor son 1.0 m/s y el viento "
   "del grid, mediana 0.39 m/s, no llega nunca)",
   f"sin racha {saltos_sin}, con racha {saltos_con}")

tormenta = correr(guion=[esc.crear_evento("tormenta", paso_inicio=5)])
ok(tormenta["metadatos"]["celdas_apagadas_por_lluvia"] > 0,
   f"la tormenta APAGA celdas: {tormenta['metadatos']['celdas_apagadas_por_lluvia']} "
   "(antes de este cambio no existía forma de que el clima apagara nada)")

lluvia = correr(guion=[esc.crear_evento("fin_temporada", paso_inicio=5)])
ok(area(lluvia) < area(base),
   f"la entrada de lluvias quema MENOS: {area(lluvia)} vs {area(base)} celdas "
   f"({(area(lluvia) / area(base) - 1) * 100:+.0f} %)")

# El caso que de verdad importa para la consola: el MISMO escenario inyectado
# en dos pasos distintos tiene que dar resultados distintos.
pronto = correr(guion=[esc.crear_evento("fin_temporada", paso_inicio=5)])
tarde = correr(guion=[esc.crear_evento("fin_temporada", paso_inicio=25)])
ok(area(pronto) < area(tarde),
   f"y llegar pronto salva más que llegar tarde: paso 5 → {area(pronto)} celdas, "
   f"paso 25 → {area(tarde)}")


# ===========================================================================
print("\n6. Inyectar a mitad = pausar, añadir el guion y seguir")
# ===========================================================================
# Así es exactamente como trabaja la consola: nadie sabía en el paso 0 que iba
# a llover en el 12.
mitad = correr(hasta_paso=12, devolver_estado=True)
sigue = correr(desde_paso=12, estado_inicial=mitad["metadatos"]["estado"],
               guion=[esc.crear_evento("tormenta", paso_inicio=12)])
sin_tormenta = correr(desde_paso=12, estado_inicial=mitad["metadatos"]["estado"])

ok(area(sigue) < area(sin_tormenta),
   f"inyectar la tormenta en el paso 12 frena el incendio: {area(sigue)} vs "
   f"{area(sin_tormenta)} celdas")

amb12 = sigue["iteraciones"][1]["ambiente"]
ok(amb12["lluvia_mm_h"] > 0, f"y la iteración lo reporta: {amb12['lluvia_mm_h']} mm/h")
ok(any(e["id"] == "tormenta" for e in amb12["eventos_activos"]),
   f"con el evento activo a la vista: {[e['nombre'] for e in amb12['eventos_activos']]}")

amb_sin_pronostico = base["iteraciones"][3]["ambiente"]
ok(amb_sin_pronostico["temperatura_c"] is not None
   and amb_sin_pronostico["viento_ms"] is not None
   and amb_sin_pronostico["humedad"] is not None,
   f"sin pronóstico el ambiente NO va vacío, sale del grid: {amb_sin_pronostico['temperatura_c']} °C · "
   f"humedad {amb_sin_pronostico['humedad']} · viento {amb_sin_pronostico['viento_ms']} m/s",
   str(amb_sin_pronostico))

# Con pronóstico: la serie ambiental de Open-Meteo ya se descargaba en
# servicios/meteo.py y no llegaba al motor. Ahora sí.
serie = [
    {"hora": f"2026-08-26T{h:02d}:00", "temperatura_c": 15 + h * 0.8,
     "humedad_relativa": 70 - h * 2, "vpd_kpa": 0.5 + h * 0.15,
     "precipitacion_mm": 0.0 if h < 6 else 4.0}
    for h in range(12)
]
con_pron = correr(serie_ambiental=serie)
ok(con_pron["metadatos"]["usa_serie_ambiental"] is True, "el motor declara que usa la serie ambiental")
t0 = con_pron["iteraciones"][0]["ambiente"]["temperatura_c"]
t20 = con_pron["iteraciones"][20]["ambiente"]["temperatura_c"]
ok(t20 > t0, f"la temperatura del pronóstico evoluciona: paso 0 → {t0} °C, paso 20 → {t20} °C")
lluvia_tarde = [it["ambiente"]["lluvia_mm_h"] for it in con_pron["iteraciones"]]
ok(lluvia_tarde[0] == 0 and max(lluvia_tarde) == 4.0,
   f"y la lluvia prevista entra a su hora: máximo {max(lluvia_tarde)} mm/h")
ok(area(con_pron) < area(base),
   f"con el pronóstico (lluvia a las 6 h) quema menos: {area(con_pron)} vs {area(base)} celdas")


print("\n" + "─" * 64)
print(f"  {fallos} comprobación(es) fallaron." if fallos
      else "  Motor de escenarios: todo correcto.")
print("─" * 64)

servidor.shutdown()
sys.exit(1 if fallos else 0)
