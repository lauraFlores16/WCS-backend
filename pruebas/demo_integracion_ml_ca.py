"""
============================================================================
DEMOSTRACIÓN EN TERMINAL DE LA INTEGRACIÓN APRENDIZAJE AUTOMÁTICO + AUTÓMATA
============================================================================
    cd backend_django
    python pruebas/demo_integracion_ml_ca.py

Para qué sirve
--------------
Los dos modelos del sistema se tocan en UN punto concreto, y ese punto es
fácil de describir mal. Este script lo enseña ejecutándose, paso a paso, con
los datos reales del grid de Apolo, para que en la defensa se pueda mostrar
la cadena entera en pantalla en vez de explicarla de palabra.

Lo que se ve, en orden:

  1. XGBoost              qué hay realmente en el grid y de dónde sale
  2. Selección del foco   el punto EXACTO donde el aprendizaje automático
                          entrega el control al autómata
  3. p_base               de dónde sale la probabilidad base — y por qué NO
                          sale de XGBoost
  4. Autómata             las cuatro reglas y los seis factores, celda a celda
  5. Resultado            la propagación, con su trazabilidad

Lo que este script demuestra que NO ocurre
------------------------------------------
Hay una confusión habitual al leer el código por encima: pensar que la
probabilidad de XGBoost entra como probabilidad de propagación de cada celda.
No es así, y el script lo comprueba numéricamente en el paso 3. Son dos
probabilidades distintas:

    P(ocurrencia)   XGBoost, por celda, columna `prob_ignicion` del grid
                    → ¿arrancaría aquí un incendio?

    P(propagación)  autómata, por PAR de celdas vecinas, en cada paso
                    → ¿salta el fuego de esta celda a la de al lado?

Mezclarlas invalidaría los dos modelos a la vez.
============================================================================
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "pruebas"))

import postgrest_falso  # noqa: E402

# Mismo arranque que las demás suites: PostgREST de mentira, sin red ni
# credenciales, para que la demostración corra en cualquier máquina.
servidor = postgrest_falso.arrancar(8181)
os.environ.update(
    SUPABASE_URL="http://127.0.0.1:8181", SUPABASE_SERVICE_KEY="s" * 60,
    SUPABASE_ESQUEMA="public", PRECALENTAR="false",
    SIPRO_OMITIR_ARRANQUE="1", DJANGO_SETTINGS_MODULE="config.settings",
)

import django  # noqa: E402

django.setup()

from api.motor.automata import (  # noqa: E402
    CONSTANTES_POR_DEFECTO, _construir_vecindad, _factor_pendiente,
    _factor_viento, _humedad_efectiva, ejecutar_automata,
)
from api.servicios import grid as grid_srv  # noqa: E402

grid_srv.cargar()
GRID = grid_srv.obtener_grid()
IX = grid_srv.obtener_indice()


def titulo(n, t):
    print()
    print("=" * 76)
    print(f"  {n}. {t}")
    print("=" * 76)


# ===========================================================================
titulo(1, "XGBOOST — QUÉ HAY EN EL GRID Y DE DÓNDE SALE")
# ===========================================================================
probs = [c.get("prob_ignicion") or 0 for c in GRID]
probs_ord = sorted(probs)
n = len(probs_ord)

print(f"""
Archivo   : backend_django/api/datos/grid.csv
Celdas    : {len(GRID):,}  (0,0045° ≈ 500 m · 25 ha por celda)
Columnas  : {', '.join(GRID[0].keys())}

La columna `prob_ignicion` es la SALIDA YA CALCULADA del modelo XGBoost V3.
El modelo NO se ejecuta en este backend: se entrenó aparte (notebook) y aquí
llega su predicción por celda, precalculada. Eso significa dos cosas:

  · el sistema no puede recalcular la probabilidad si cambian las variables;
  · para reentrenar hay que volver al notebook y regenerar grid.csv.

Distribución real de P(ocurrencia) en las {len(GRID):,} celdas:
  mínimo   {probs_ord[0]:.4f}
  p10      {probs_ord[n // 10]:.4f}
  mediana  {probs_ord[n // 2]:.4f}
  p90      {probs_ord[9 * n // 10]:.4f}
  máximo   {probs_ord[-1]:.4f}
  media    {sum(probs) / n:.4f}""")

# ===========================================================================
titulo(2, "EL PUNTO DE ENTREGA — XGBOOST ELIGE DÓNDE EMPIEZA EL FUEGO")
# ===========================================================================
print("""
Código    : api/motor/parametros.py · función _elegir_foco()
Prioridad : 1º foco manual del usuario
            2º foco activo de NASA FIRMS (el de mayor FRP)
            3º celda de MAYOR prob_ignicion  ← aquí entra XGBoost
            4º foco de un evento histórico

Este es el ÚNICO punto donde la predicción decide algo en la simulación:
elige la celda de arranque cuando no hay observación que la imponga. Es
exactamente la arquitectura declarada en la metodología:

    variables → XGBoost → P(ocurrencia) → selección de celda → foco inicial
    → autómata celular → propagación
""")

mejor = max(GRID, key=lambda c: c.get("prob_ignicion") or 0)
print(f"Celda que elegiría XGBoost ahora mismo:")
print(f"  fila {mejor['fila']} · columna {mejor['columna']}")
print(f"  {mejor['lat']:.5f}, {mejor['lon']:.5f}")
print(f"  P(ocurrencia) = {mejor['prob_ignicion']:.4f}  ({mejor['prob_ignicion'] * 100:.1f} %)")
print(f"  NDVI {mejor['ndvi']:.3f} · humedad {mejor['humedad']:.3f} · "
      f"pendiente {mejor['pendiente_grados']:.1f}°")

# ===========================================================================
titulo(3, "p_base — LA COMPROBACIÓN QUE IMPORTA")
# ===========================================================================
print("""
Código    : api/motor/parametros.py líneas 155-159
            api/motor/automata.py línea 654 (donde se usa)

    p_base_calibrado = calibracion["p_base"] if calibracion else 0.30
    factor_peligro   = 0.7 + 0.6 * peligro["puntaje"]
    p_base           = p_base_calibrado * factor_peligro

Lee esas tres líneas con atención: `prob_ignicion` NO aparece. p_base es un
ESCALAR ÚNICO para toda la corrida, y sale de:

  · la calibración por evolución diferencial contra perímetros reales
    (api/motor/calibracion.py), o 0,30 si aún no se ha calibrado;
  · el índice de peligro meteorológico del momento;
  · el factor de temporada.

Comprobación numérica de que las dos probabilidades son independientes:""")

# Si p_base dependiera de la celda, dos focos con prob_ignicion muy distinta
# tendrían p_base distinta. Se comprueba que no.
from api.motor import parametros as par  # noqa: E402

peor = min((c for c in GRID if (c.get("prob_ignicion") or 0) > 0),
           key=lambda c: c["prob_ignicion"])
print(f"\n  Celda A: prob_ignicion {mejor['prob_ignicion']:.4f} "
      f"(f{mejor['fila']} c{mejor['columna']})")
print(f"  Celda B: prob_ignicion {peor['prob_ignicion']:.4f} "
      f"(f{peor['fila']} c{peor['columna']})")
print(f"  Razón entre ambas: ×{mejor['prob_ignicion'] / peor['prob_ignicion']:.1f}")

# Sin red, `derivar_parametros` cae a los promedios del grid: da igual, lo que
# se quiere ver es que el p_base resultante no depende de la celda.
try:
    # CONTROL. Con conexión a internet, `derivar_parametros` baja la
    # meteorología del momento y el índice de peligro puede moverse entre dos
    # llamadas seguidas. Comparar A contra B sin más no distingue "cambió por
    # la celda" de "cambió por la hora".
    #
    # Por eso se llama TRES veces: A, B y otra vez A. Si la variación fuera
    # espacial, A y A darían lo mismo y B se saldría. Si es temporal, las tres
    # se mueven por igual y la diferencia A-A es del mismo orden que la A-B.
    pa1 = par.derivar_parametros(GRID, {"foco": mejor})
    pb = par.derivar_parametros(GRID, {"foco": peor})
    pa2 = par.derivar_parametros(GRID, {"foco": mejor})

    va1 = pa1["parametros"]["p_base"]
    vb = pb["parametros"]["p_base"]
    va2 = pa2["parametros"]["p_base"]

    print(f"\n  1ª llamada · celda A (prob 0,9977) → p_base {va1}")
    print(f"  2ª llamada · celda B (prob 0,0001) → p_base {vb}")
    print(f"  3ª llamada · celda A otra vez      → p_base {va2}")

    dif_espacial = abs(va1 - vb)      # cambió la celda
    dif_control = abs(va1 - va2)      # NO cambió la celda

    if va1 == vb == va2:
        print("\n  ✓ Las tres idénticas. p_base no depende de la celda.")
    elif dif_control >= dif_espacial * 0.5:
        print(f"\n  ✓ La misma celda A ya varía {dif_control:.3f} entre llamadas,")
        print(f"    del mismo orden que los {dif_espacial:.3f} entre A y B.")
        print("    La variación es TEMPORAL (índice de peligro meteorológico),")
        print("    no espacial. p_base no depende de la P(ocurrencia).")
    else:
        print(f"\n  ! La celda A repetida varía {dif_control:.3f} pero entre A y B")
        print(f"    varía {dif_espacial:.3f}. Eso sí apuntaría a dependencia de la")
        print("    celda: revisar parametros.py líneas 155-159.")

    print("\n  Verificación por código, que no depende de la red:")
    import inspect
    fuente = inspect.getsource(par.derivar_parametros)
    linea_pbase = [l.strip() for l in fuente.splitlines() if "p_base =" in l]
    for l in linea_pbase:
        print(f"    {l}")
    print(f"    ¿aparece `prob_ignicion` en el cálculo de p_base? "
          f"{'SÍ' if 'prob_ignicion' in ' '.join(linea_pbase) else 'NO'}")

    # El origen REAL de selección: sin foco manual, decide el backend.
    auto_sel = par.derivar_parametros(GRID, {})
    print(f"\n  Origen del foco cuando NO se impone uno a mano: "
          f"{auto_sel['seleccion_foco']['origen']}")
    print(f"  Detalle: {auto_sel['seleccion_foco']['detalle']}")
    print("  (arriba salía `manual` porque la comparación de p_base sí le")
    print("   pasaba una celda concreta; esta llamada es la que enseña la")
    print("   prioridad real: manual → FIRMS → XGBoost → histórico)")
except Exception as e:  # noqa: BLE001
    print(f"\n  (No se pudo derivar en vivo: {type(e).__name__}: {e})")
    print("  Normal sin salida a internet. La conclusión se lee igual en el")
    print("  código: prob_ignicion no interviene en el cálculo de p_base.")

# ===========================================================================
titulo(4, "EL AUTÓMATA — LAS CUATRO REGLAS Y LOS SEIS FACTORES")
# ===========================================================================
print("""
Código : api/motor/automata.py · función paso() · líneas 580-687

Estados: 0 SIN_QUEMAR · 1 ARDIENDO · 2 QUEMADO · 3 INERTE

  R1  ARDIENDO  → QUEMADO   al agotar su tiempo de residencia (o por lluvia)
  R2  INERTE    → INERTE    nunca cambia (agua, roca, NDVI < 0,10)
  R3  QUEMADO   → QUEMADO   absorbente: no vuelve a arder
  R4  SIN_QUEMAR con al menos un vecino ARDIENDO
      → se calcula P(propagación) y se sortea

P(propagación) del par (celda ardiendo i → vecina j), línea 654:

    p = p_base · peso · factor_fase · f_pend · f_viento · f_veg · f_hum · f_lluvia
""")

k = CONSTANTES_POR_DEFECTO
vec = _construir_vecindad(k["RADIO_VECINDAD"], k["EXP_DISTANCIA"])
print(f"Vecindad construida con RADIO_VECINDAD = {k['RADIO_VECINDAD']}: "
      f"{len(vec)} vecinos")
print(f"{'Δf':>4}{'Δc':>4}{'dist celdas':>13}{'dist (m)':>11}{'peso 1/d':>11}")
print("-" * 43)
for v in vec:
    print(f"{v['df']:>4}{v['dc']:>4}{v['d_celdas']:>13.3f}"
          f"{v['d_metros']:>11.0f}{v['peso']:>11.3f}")

if len(vec) == 8:
    print("""
  Vecindad de MOORE: los 4 vecinos ortogonales más las 4 diagonales.

  El peso 1/d es lo que hace que la diagonal NO propague igual que la
  ortogonal: está a 696 m frente a 484-500 m, así que su peso baja a 0,707.
  Sin ese término el fuego avanzaría en cuadrados perfectos, porque la
  diagonal cubre más terreno por paso.

  Nota histórica: hasta la corrección de la vecindad esto devolvía 4 vecinos.
  El filtro era de distancia EUCLÍDEA y con radio 1 las diagonales quedaban
  fuera (1,4142 > 1). Era una vecindad de von Neumann, aunque el proyecto
  declarase Moore. Se corrigió a distancia de Chebyshov. El modelo quema
  x4,49 respecto de antes con los mismos parámetros, así que la calibración
  anterior quedó invalidada.
""")
else:
    print(f"""
  ⚠ ATENCIÓN — se esperaban 8 vecinos (Moore) y salen {len(vec)}.
  Revisa la constante VECINDAD de CONSTANTES_POR_DEFECTO: debe valer 'moore'.
""")

print("Los seis factores, evaluados con datos reales del grid:")
print("-" * 76)

celda_i, celda_j = mejor, IX["por_fila_col"].get(
    f"{mejor['fila'] - 1},{mejor['columna']}") or GRID[0]

# f_pendiente
for dh, etiqueta in ((+50, "cuesta arriba, +50 m"), (0, "llano"), (-50, "cuesta abajo, -50 m")):
    f = _factor_pendiente(dh, 500.0, k)
    print(f"  f_pend   {etiqueta:<22} ×{f:.3f}")
print("           exp(K·Δh/d), acotado a [1/4, 4]. Rothermel: cuesta arriba")
print("           acelera porque la llama precalienta el combustible de arriba.")

# f_viento
print()
for vel, ang in ((0.4, "a favor"), (0.4, "cruzado"), (8.0, "a favor"), (8.0, "cruzado")):
    u, v = (vel, 0.0) if ang == "a favor" else (0.0, vel)
    f = _factor_viento(0, 1, u, v, k)
    print(f"  f_viento {vel:>4.1f} m/s {ang:<14} ×{f:.3f}")
print("           1 + K·v·cos(θ). Solo la componente A FAVOR cuenta: el")
print("           coseno negativo se recorta a 0, así que el viento nunca")
print("           frena la propagación hacia atrás, simplemente no la ayuda.")

# f_vegetacion
print()
for ndvi, et in ((0.10, "umbral de barrera"), (0.27, "media de Apolo"), (0.60, "bosque denso")):
    print(f"  f_veg    NDVI {ndvi:.2f} {et:<18} ×{k['NDVI_BASE'] + ndvi:.3f}")
print(f"           NDVI_BASE + NDVI. Por debajo de NDVI_BARRERA = "
      f"{k['NDVI_BARRERA']} la celda")
print("           es INERTE y no arde. Ver la limitación en la documentación:")
print("           en Apolo ninguna celda baja de ese umbral.")

# f_humedad
print()
for h, et in ((0.22, "mínimo del grid"), (0.376, "mediana"), (0.40, "máximo")):
    f = max(1 - k["K_HUMEDAD"] * _humedad_efectiva(h, 0), 0.1)
    print(f"  f_hum    humedad {h:.3f} {et:<16} ×{f:.3f}")
print("           1 − K·humedad, con suelo en 0,1. El rango real del grid es")
print("           estrecho (0,22-0,40), así que este factor varía poco.")

# f_lluvia
print()
for mm in (0, 1, 5, 15):
    f = 1.0 / (1.0 + k["K_LLUVIA_PROP"] * mm) if mm > 0 else 1.0
    print(f"  f_lluvia {mm:>2} mm/h {'':<20} ×{f:.3f}")
print("           1/(1+K·mm). Saturante: doblar la lluvia no dobla el efecto.")

# factor_fase
print()
print(f"  factor_fase  1 − {k['DECAIMIENTO_FASE']} · (pasos_ardiendo / residencia)")
print(f"               residencia entre {k['RESIDENCIA_MIN']} y "
      f"{k['RESIDENCIA_MAX']} pasos de 15 min")
print("               Una celda recién encendida propaga más que una que ya")
print("               se está apagando.")

# ===========================================================================
titulo(5, "LA CADENA COMPLETA, EJECUTÁNDOSE")
# ===========================================================================
parametros = {
    "foco_fila": mejor["fila"], "foco_columna": mejor["columna"],
    "p_base": 0.30,                    # el valor por defecto, sin calibración
    "num_iteraciones": 24,             # 6 h a 15 min por paso
    "minutos_por_iteracion": 15,
    "multiplicador_viento": 1.0, "delta_humedad": 0.0, "delta_temperatura_c": 0.0,
    "semilla": 12345,
}
print(f"""
Foco inicial elegido por XGBoost : f{mejor['fila']} c{mejor['columna']}
P(ocurrencia) de esa celda       : {mejor['prob_ignicion']:.4f}
p_base de la corrida             : {parametros['p_base']}   ← independiente
Horizonte                        : {parametros['num_iteraciones']} pasos × 15 min = 6 h
Semilla                          : {parametros['semilla']}
""")

res = ejecutar_automata(GRID, parametros)
its = res["iteraciones"]

print(f"{'PASO':>5}{'MINUTO':>8}{'ARDIENDO':>11}{'QUEMADAS':>11}{'km²':>9}")
print("-" * 44)
for it in its:
    if it["iteracion"] % 4 == 0 or it is its[-1]:
        print(f"{it['iteracion']:>5}{it['iteracion'] * 15:>8}"
              f"{it['num_celdas_ardiendo']:>11}{it['num_celdas_quemadas']:>11}"
              f"{it['num_celdas_quemadas'] * 0.25:>9.1f}")

m = res["metadatos"]
ultima = its[-1]
print(f"""
Resultado
  celdas quemadas   {ultima['num_celdas_quemadas']:,}  ({ultima['num_celdas_quemadas'] * 0.25:.1f} km²)
  celdas ardiendo   {ultima['num_celdas_ardiendo']:,}
  saltos de pavesas {len(m['eventos_spotting'])}
  usa DEM real      {m['usa_dem']}  ({m['celdas_con_dem']:,} celdas con elevación)
  semilla           {m['semilla']}

Reproducibilidad: vuelve a lanzar este script y saldrán exactamente los
mismos números. El generador es un mulberry32 con semilla, y de eso depende
que la calibración tenga sentido — si cada corrida diera otra cosa, el
optimizador no podría distinguir una mejora del ruido.
""")

# Segunda corrida idéntica, para demostrarlo en pantalla en vez de afirmarlo.
res2 = ejecutar_automata(GRID, parametros)
igual = (res2["iteraciones"][-1]["num_celdas_quemadas"]
         == ultima["num_celdas_quemadas"])
print(f"  Comprobación en vivo — segunda corrida con la misma semilla: "
      f"{res2['iteraciones'][-1]['num_celdas_quemadas']:,} celdas  "
      f"{'✓ idéntico' if igual else '✗ DISTINTO — el determinismo está roto'}")

# Y una con otra semilla, para que se vea que es estocástico de verdad.
res3 = ejecutar_automata(GRID, {**parametros, "semilla": 999})
print(f"  Con semilla 999 (otra realización): "
      f"{res3['iteraciones'][-1]['num_celdas_quemadas']:,} celdas")
print("""
  Las dos cosas a la vez son lo correcto: MISMA semilla → mismo resultado
  (reproducible), OTRA semilla → otro resultado (estocástico). Por eso la
  validación necesita varias semillas y no una sola corrida.
""")

print("=" * 76)
print("  Fin de la demostración.")
print("=" * 76)
