"""
Calibra el motor v3 sobre Apolo desde la consola (sin la interfaz web).

    cd validacion_rurrenabaque/python
    python calibrar_apolo.py                 # población 12 · 10 generaciones · 5 semillas
    python calibrar_apolo.py --rapido        # población 10 · 8 generaciones · 3 semillas

Hace exactamente lo mismo que Monitoreo → Capa 4 → Calibrar constantes K:
llama a `api.motor.calibracion.calibrar` con el grid de Apolo, sus focos FIRMS
etiquetados por evento, la capa OSM y el DEM, y guarda el resultado en el
almacén del backend (Supabase), de donde lo lee congelar_parametros.py.

Además deja una copia en ../resultados/calibracion_v3.json.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import os
import sys

import f11_ejecutar_ca_rbq as F11

SALIDA = Path("../resultados/calibracion_v3.json")


def cargar_backend():
    """django.setup() SIN las variables falsas de Supabase que pone f11.

    f11 fija SUPABASE_URL = http://127.0.0.1:1 para no tocar la base de datos
    durante la validación. Aquí sí hay que guardar la calibración, así que se
    deja que el backend lea su .env.
    """
    sys.path.insert(0, str(F11.BACKEND))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("SIPRO_OMITIR_ARRANQUE", "1")
    os.environ.setdefault("PRECALENTAR", "false")
    import django
    django.setup()
    from api.motor import automata as mod
    return mod


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--poblacion", type=int, default=12)
    ap.add_argument("--generaciones", type=int, default=10)
    ap.add_argument("--semillas", type=int, default=5)
    ap.add_argument("--rapido", action="store_true")
    args = ap.parse_args()
    if args.rapido:
        args.poblacion, args.generaciones, args.semillas = 10, 8, 3

    print("=" * 74)
    print("CALIBRACIÓN DEL MOTOR v3 SOBRE APOLO")
    print("=" * 74)
    mod = cargar_backend()
    print(f"Motor     : {getattr(mod, 'MODELO', 'v2 (¡no es v3!)')}")
    if getattr(mod, "MODELO", None) != "velocidad_v3":
        print("El motor instalado no es v3. Copia automata.py del paquete motor_v3.")
        return 2

    from api.motor import calibracion as C

    # El resultado se escribe en disco ANTES de intentar Supabase: una caída
    # de la base de datos no puede tirar media hora de cálculo.
    persistir_original = C._persistir_calibracion

    def persistir(resultado):
        SALIDA.parent.mkdir(parents=True, exist_ok=True)
        SALIDA.write_text(json.dumps(resultado, indent=2, ensure_ascii=False, default=str),
                          encoding="utf-8")
        print(f"\nGuardado en disco: {SALIDA}")
        try:
            persistir_original(resultado)
            print("Guardado también en el backend (Supabase).")
        except Exception as e:  # noqa: BLE001
            print(f"No se pudo guardar en Supabase ({type(e).__name__}). No importa:")
            print(f"  python congelar_parametros.py --desde-json {SALIDA}")
    C._persistir_calibracion = persistir
    from api.servicios import grid as grid_srv
    grid_srv.cargar()
    GRID = grid_srv.obtener_grid()
    FOCOS = grid_srv.obtener_focos()
    ix = grid_srv.obtener_indice()
    import statistics
    nd = [c["ndvi"] for c in GRID if c.get("ndvi") is not None]
    print(f"Grid      : {len(GRID):,} celdas · NDVI mediana {statistics.median(nd):.3f}")

    elevacion = barreras = resistencia = None
    from api.servicios import terreno
    try:
        osm = terreno.obtener_terreno_osm()
        barreras, resistencia = set(osm["barreras"]), osm["resistencia"]
        print(f"Terreno   : {len(barreras)} barreras duras · {len(resistencia)} celdas con resistencia")
    except Exception as e:  # noqa: BLE001
        print(f"Terreno   : SIN capa OSM ({type(e).__name__}). Reintenta más tarde.")
        return 3
    try:
        ref = ix.get("ref") or {}
        dem = terreno.obtener_dem(ref.get("fila", 0), ref.get("columna", 0), 200)
        elevacion = dem["alturas"]
        print(f"DEM       : {len(elevacion):,} celdas con altura")
    except Exception as e:  # noqa: BLE001
        print(f"DEM       : no disponible ({type(e).__name__}); se sigue con la pendiente del grid")

    per = C.construir_perimetros_reales(GRID, FOCOS)
    print(f"Eventos   : {len(per)} → " + ", ".join(f"{p['evento']} ({len(p['celdas'])} celdas, "
                                                  f"{len(p['iniciales'])} focos 6 h)" for p in per))
    print(f"Genes     : {', '.join(C.NOMBRES)}")
    total = args.poblacion * (args.generaciones + 1)
    print(f"Búsqueda  : población {args.poblacion} · {args.generaciones} generaciones · "
          f"{args.semillas} semillas → {total} evaluaciones\n")

    t0 = time.time()

    def progreso(frac, mejor):
        el = time.time() - t0
        eta = el / max(frac, 1e-6) - el
        apt = mejor["aptitud"] if mejor else float("nan")
        pb = mejor["genes"]["p_base"] if mejor else float("nan")
        print(f"  {frac * 100:5.1f} %  · mejor aptitud {apt:.3f} · p_base {pb:.3f} "
              f"({pb * 100:.0f} m/h) · faltan ~{eta / 60:.0f} min", flush=True)

    r = C.calibrar(GRID, FOCOS, poblacion=args.poblacion, generaciones=args.generaciones,
                   on_progreso=progreso, elevacion=elevacion, barreras_extra=barreras,
                   resistencia_extra=resistencia, semillas=list(range(1, args.semillas + 1)))

    print("\n" + "-" * 74)
    print(f"Terminado en {(time.time() - t0) / 60:.1f} min · aptitud (F1 con castigo de área) {r['f1']:.3f}")
    print(f"p_base = {r['p_base']:.4f}  →  velocidad base {r['p_base'] * 100:.1f} m/h")
    print(f"\n{'GEN':<22}{'VALOR':>10}{'RANGO':>20}")
    for n in C.NOMBRES:
        v = r["p_base"] if n == "p_base" else r["constantes"][n]
        lo, hi, _ = C.GENES[n]
        borde = "  ← en el borde" if (abs(v - lo) < 0.02 * (hi - lo) or abs(v - hi) < 0.02 * (hi - lo)) else ""
        print(f"{n:<22}{v:>10.4f}{f'[{lo:g} – {hi:g}]':>20}{borde}")
    print(f"\n{'EVENTO':<10}{'F1':>8}{'IoU':>8}{'A.sim/A.real':>14}")
    for d in r["detalle"]:
        print(f"{d['evento']:<10}{d['f1']:>8.3f}{d.get('iou', 0):>8.3f}{d.get('razon_area', 0):>14.2f}")

    print(f"\nSiguiente: python congelar_parametros.py --desde-json {SALIDA}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
