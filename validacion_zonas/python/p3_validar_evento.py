"""
P3 — Validación OFICIAL de un paquete: 30 repeticiones del autómata con los
parámetros congelados y cruce con la cicatriz observada.

    python p3_validar_evento.py --paquete ixiamas-la-paz-2022-E004
    python p3_validar_evento.py --paquete ixiamas-la-paz-2022-E004 --sin-spotting   # sensibilidad
    python p3_validar_evento.py --todos            # todos los paquetes sin resultado

Usa EXACTAMENTE el mismo código que el botón «Ejecutar validación» del
dashboard (api/motor/validacion_externa.py), así que lo que se ve en pantalla
con 30 repeticiones tiene que coincidir con lo que escribe este script.

Escribe en <paquete>/resultados/: metricas_validacion.json, ca_repeticiones.csv,
comparacion_espacial.csv y resumen_ejecucion.json.
"""
from __future__ import annotations

import argparse
import os
import sys

from comun import RAIZ_BACKEND, actualizar_indice


def preparar_django():
    sys.path.insert(0, str(RAIZ_BACKEND))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("SIPRO_OMITIR_ARRANQUE", "1")
    os.environ.setdefault("PRECALENTAR", "false")
    os.environ.setdefault("SUPABASE_URL", "http://127.0.0.1:1")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "no-se-usa")
    import django
    django.setup()
    from api.motor import validacion_externa
    return validacion_externa


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paquete", default=None)
    ap.add_argument("--todos", action="store_true", help="Todos los paquetes sin resultado oficial")
    ap.add_argument("--repeticiones", type=int, default=30)
    ap.add_argument("--sin-spotting", action="store_true")
    args = ap.parse_args()

    V = preparar_django()
    if args.todos:
        ids = [p["id"] for p in V.listar_paquetes() if not p["historico"] and p["iou"] is None]
    elif args.paquete:
        ids = [args.paquete]
    else:
        print("Indica --paquete <id> o --todos. Paquetes disponibles:")
        for p in V.listar_paquetes():
            print(f"  {p['id']:<50} {p['rol'] or '—':<12} IoU {p['iou']}")
        return 2

    for pid in ids:
        print("=" * 74)
        print(f"VALIDACIÓN · {pid} · {args.repeticiones} repeticiones"
              + (" · SIN pavesas" if args.sin_spotting else ""))
        print("=" * 74)

        def avance(frac, rep):
            print(f"  semilla {rep['semilla']:>2}: {rep['celdas_quemadas']:>5} celdas "
                  f"({rep['area_km2']:.2f} km²)")

        res = V.ejecutar(args.repeticiones, not args.sin_spotting, avance, pid=pid)
        carpeta = V.guardar_oficial(pid, res)
        m = res["metricas"]
        print("-" * 74)
        print(f"  IoU {m['iou']} · F1 {m['f1']} · precisión {m['precision']} · recall {m['recall']}")
        print(f"  área observada {m['area_observada_km2']} km² · simulada {m['area_simulada_km2']} km² "
              f"→ {m['comportamiento']}")
        print(f"  dispersión entre semillas: CV {res['dispersion']['cv_pct']} %")
        print(f"  escrito en {carpeta}")
    actualizar_indice()
    return 0


if __name__ == "__main__":
    sys.exit(main())
