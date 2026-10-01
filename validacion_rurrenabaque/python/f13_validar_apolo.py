"""
============================================================================
VALIDACIÓN DEL AUTÓMATA EN APOLO — MISMO MÉTODO QUE RURRENABAQUE
============================================================================
Ruta: validacion_rurrenabaque/python/f13_validar_apolo.py

    cd validacion_rurrenabaque/python
    python f13_validar_apolo.py

POR QUÉ EXISTE ESTE SCRIPT
    Para poder decir «el modelo está bien calibrado en Apolo y en
    Rurrenabaque» hacen falta dos números comparables. Los que había no lo
    eran.

    Las métricas de Apolo registradas en `eventos_historicos.json` (IoU 1,4-5,5 %,
    sobreestimación de 4,5x a 18,5x) salen de simular UN foco durante 86-91
    DÍAS contra una temporada entera con decenas de igniciones independientes.
    Eso no mide el modelo de propagación: mide un experimento mal planteado.
    Con ~8.500 pasos desde un solo punto y sin barreras activas, el autómata
    tiene que inundar el municipio. La sobreestimación de 18x es del diseño de
    la prueba, no de la física del modelo.

    Este script aplica a Apolo EXACTAMENTE el mismo procedimiento que a
    Rurrenabaque:

        ventana corta del evento (no la temporada entera)
        todos los focos del primer día como arranque (no uno solo)
        N repeticiones estocásticas con semillas registradas
        probabilidad de quema por celda y umbral congelado
        misma métrica, mismo tamaño de celda, mismo tratamiento del NoData

    Solo así los dos resultados se pueden poner en la misma tabla.

QUÉ USA COMO REFERENCIA OBSERVADA
    Dos opciones, y la diferencia importa:

    --referencia firms   (por defecto)
        El perímetro se construye con las detecciones FIRMS del evento,
        dilatadas ±1 celda. Es lo que ya hace `calibracion.py`. Ventaja: está
        disponible ahora mismo, sin GEE. Limitación seria: FIRMS es un
        MUESTREO DISPERSO de anomalías térmicas, no un polígono de área
        quemada. Un perímetro así subestima la superficie real, y por tanto
        INFLA artificialmente la sobreestimación aparente del modelo.

    --referencia dnbr --observado ruta.csv
        La cicatriz real medida por satélite, en el mismo formato que produce
        el bloque 07 de GEE para Rurrenabaque (con su banda `valido`). Es la
        comparación honesta y la que debe ir a la memoria.

    Mientras no exista el dNBR de Apolo, este script corre con FIRMS y lo
    declara en pantalla y en el JSON. No es un sustituto: es un puente.

LO QUE NO HACE
    No ajusta nada. Usa los parámetros que haya en
    config/parametros_ca_congelados.json, los mismos que Rurrenabaque.

CÓMO SE EJECUTA
    python f13_validar_apolo.py
    python f13_validar_apolo.py --repeticiones 30 --ventana-dias 4
    python f13_validar_apolo.py --referencia dnbr --observado ../datos/apolo_observado_grid.csv
============================================================================
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[2]
BACKEND = RAIZ
AREA_CELDA_KM2 = 0.25
MINUTOS_POR_ITERACION = 15


def cargar_motor():
    sys.path.insert(0, str(BACKEND))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("SIPRO_OMITIR_ARRANQUE", "1")
    os.environ.setdefault("PRECALENTAR", "false")
    os.environ.setdefault("SUPABASE_URL", "http://127.0.0.1:1")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "no-se-usa")
    import django
    django.setup()
    from api.motor import automata as mod
    from api.motor.calibracion import construir_perimetros_reales, dilatar
    from api.servicios import grid as grid_srv
    return mod, construir_perimetros_reales, dilatar, grid_srv


def metricas(tp, fp, fn, tn):
    def div(a, b):
        return float(a) / float(b) if b else None
    p = div(tp, tp + fp)
    r = div(tp, tp + fn)
    f1 = (2 * p * r / (p + r)) if p and r and (p + r) else None
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "iou": round(div(tp, tp + fp + fn), 4) if div(tp, tp + fp + fn) is not None else None,
        "precision": round(p, 4) if p is not None else None,
        "recall": round(r, 4) if r is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "accuracy": round(div(tp + tn, tp + fp + fn + tn), 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="../config/parametros_ca_congelados.json")
    ap.add_argument("--resultados", default="../resultados")
    ap.add_argument("--repeticiones", type=int, default=30)
    ap.add_argument("--umbral", type=float, default=None)
    ap.add_argument("--ventana-dias", type=int, default=4,
                    help="Ventana del evento, en días. La misma que usa la "
                         "calibración. NO la temporada entera.")
    ap.add_argument("--referencia", choices=["firms", "dnbr", "mcd64"],
                    default="firms",
                    help="mcd64 usa el CSV de f9_importar_area_quemada.py, "
                         "la misma referencia que Rurrenabaque")
    ap.add_argument("--observado", default=None,
                    help="CSV observado con banda `valido`, para --referencia dnbr")
    ap.add_argument("--horas-max", type=float, default=None,
                    help="Tope del horizonte, en horas. Sin él se usa la "
                         "ventana entera del evento. Ver la nota sobre escala "
                         "temporal en el encabezado.")
    ap.add_argument("--sin-clima", action="store_true",
                    help="No traer el clima horario real del evento. Sin ciclo "
                         "diurno el frente no se frena nunca de noche.")
    ap.add_argument("--sin-terreno", action="store_true",
                    help="Correr sin DEM ni barreras, para medir cuánto aportan")
    args = ap.parse_args()

    res = Path(args.resultados)
    res.mkdir(parents=True, exist_ok=True)

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if cfg.get("p_base") is None:
        print("=" * 74)
        print("BLOQUEADO — `p_base` sigue en null en el archivo de parámetros")
        print("=" * 74)
        print("Es el mismo bloqueo que en Rurrenabaque, y a propósito: Apolo y")
        print("Rurrenabaque tienen que validarse con EL MISMO conjunto de")
        print("parámetros congelados, o los dos resultados no son comparables.")
        print(f"\nEdita {args.config} y elige un p_base.")
        return 2

    umbral = args.umbral if args.umbral is not None else cfg["ejecucion"]["umbral_prob_quemada"]

    print("=" * 74)
    print("VALIDACIÓN DEL AUTÓMATA EN APOLO")
    print("=" * 74)
    print("Mismo método que Rurrenabaque: ventana corta, multi-foco,")
    print(f"{args.repeticiones} repeticiones, umbral {umbral}.")

    mod, construir_perimetros, dilatar, grid_srv = cargar_motor()
    grid_srv.cargar()
    GRID = grid_srv.obtener_grid()
    FOCOS = grid_srv.obtener_focos()
    ix = grid_srv.obtener_indice()

    constantes = {**mod.CONSTANTES_POR_DEFECTO, **cfg["constantes"]}
    vec = mod._construir_vecindad(constantes["RADIO_VECINDAD"],
                                  constantes["EXP_DISTANCIA"],
                                  constantes.get("VECINDAD", "moore"))
    print(f"\nGrid      : {len(GRID):,} celdas")
    print(f"Vecindad  : {len(vec)} vecinos ({constantes.get('VECINDAD','moore')})")
    print(f"p_base    : {cfg['p_base']}")

    # --- Capa de terreno: el MISMO mundo que la calibración ----------------
    elevacion = barreras = resistencia = None
    if not args.sin_terreno:
        try:
            from api.servicios import terreno
            osm = terreno.obtener_terreno_osm()
            barreras = set(osm["barreras"])
            resistencia = osm["resistencia"]
            print(f"Terreno   : {len(barreras)} barreras duras · "
                  f"{len(resistencia)} celdas con resistencia (OSM)")
        except Exception as e:  # noqa: BLE001
            print(f"Terreno   : sin capa OSM ({type(e).__name__}). Se corre sin barreras,")
            print("            y eso favorece la sobreestimación. Declárarlo.")
        try:
            from api.servicios import terreno
            ref = ix.get("ref") or {}
            dem = terreno.obtener_dem(ref.get("fila", 0), ref.get("columna", 0), 200)
            elevacion = dem["alturas"]
            print(f"DEM       : {len(elevacion)} celdas con altura")
        except Exception as e:  # noqa: BLE001
            print(f"DEM       : no disponible ({type(e).__name__}). El factor de")
            print("            pendiente usará el respaldo, que no distingue subida de bajada.")
    else:
        print("Terreno   : desactivado por --sin-terreno")

    # --- Los eventos, en ventana corta -------------------------------------
    perimetros = construir_perimetros(GRID, FOCOS, ventana_dias=args.ventana_dias)
    if not perimetros:
        print("\nNo se pudieron construir perímetros. Revisa focos.csv.")
        return 1

    print(f"\nEventos con ventana de {args.ventana_dias} días: {len(perimetros)}")
    print(f"{'EVENTO':>10}{'DESDE':>13}{'CELDAS':>9}{'km²':>9}{'FOCOS INI':>11}{'PASOS':>7}")
    print("-" * 59)
    for p in perimetros:
        print(f"{p['evento']:>10}{str(p['desde'])[:10]:>13}{len(p['celdas']):>9}"
              f"{p['area_km2']:>9.1f}{len(p['iniciales']):>11}{p['pasos']:>7}")

    if args.referencia in ("dnbr", "mcd64"):
        if not args.observado or not Path(args.observado).exists():
            print(f"\n--referencia {args.referencia} necesita --observado.")
            if args.referencia == "mcd64":
                print("  python f9_importar_area_quemada.py --zona apolo \\")
                print("         --entrada ../datos/mcd64a1_apolo.csv --desde N --hasta M")
            return 1
        etiqueta = ("dNBR sobre Sentinel/Landsat" if args.referencia == "dnbr"
                    else "MODIS MCD64A1 (463 m)")
        print(f"\nReferencia observada: {etiqueta}.")
        if args.referencia == "mcd64":
            print("  Misma referencia que Rurrenabaque, así que las dos")
            print("  validaciones son comparables entre sí.")
    else:
        print("\nReferencia observada: FIRMS dilatado ±1 celda.")
        print("  ⚠ FIRMS es un muestreo disperso de anomalías térmicas, no un")
        print("  polígono de área quemada. Un perímetro así SUBESTIMA la")
        print("  superficie real y por tanto INFLA la sobreestimación aparente")
        print("  del modelo. Para la memoria hace falta el dNBR de Apolo, igual")
        print("  que en Rurrenabaque.")

    # --- Ejecutar ----------------------------------------------------------
    filas_ev, filas_rep = [], []
    por_id = ix["por_id"]
    por_fc = ix["por_fila_col"]

    for p in perimetros:
        print(f"\n--- Evento {p['evento']} · {str(p['desde'])[:10]} a {str(p['hasta'])[:10]} ---")
        pasos = p["pasos"]
        if args.horas_max:
            pasos = min(pasos, int(round(args.horas_max * 60 / MINUTOS_POR_ITERACION)))

        # --- Clima horario REAL del evento ------------------------------
        # Es lo que introduce el ciclo diurno: de noche la humedad sube y la
        # propagación se frena sola. Sin esto el frente crece indefinidamente
        # y cualquier horizonte largo acaba cubriendo el municipio entero.
        serie_amb = None
        if not args.sin_clima:
            try:
                from api.servicios.meteo import obtener_serie_ambiental_historica
                celda_foco = por_fc.get(f"{p['foco']['fila']},{p['foco']['columna']}")
                serie_amb = obtener_serie_ambiental_historica(
                    celda_foco["lat"], celda_foco["lon"],
                    str(p["desde"])[:10], str(p["hasta"])[:10])
                print(f"  clima ERA5 del evento: {len(serie_amb)} horas")
            except Exception as e:  # noqa: BLE001
                print(f"  sin clima horario ({type(e).__name__}): el frente no")
                print("  tendrá ciclo diurno y tenderá a sobreestimar.")

        serie_vto = None
        try:
            from api.servicios.meteo import obtener_serie_viento_historica
            celda_foco = por_fc.get(f"{p['foco']['fila']},{p['foco']['columna']}")
            serie_vto = obtener_serie_viento_historica(
                celda_foco["lat"], celda_foco["lon"], p["desde"], p["hasta"])
            print(f"  viento ERA5 horario: {len(serie_vto)} horas")
        except Exception as e:  # noqa: BLE001
            print(f"  sin viento horario ({type(e).__name__})")

        parametros = {
            "foco_fila": p["foco"]["fila"], "foco_columna": p["foco"]["columna"],
            "focos_iniciales": p["iniciales"],
            "p_base": cfg["p_base"],
            "num_iteraciones": pasos,
            "minutos_por_iteracion": MINUTOS_POR_ITERACION,
            "multiplicador_viento": 1.0, "delta_humedad": 0.0,
            "delta_temperatura_c": 0.0,
            "serie_viento": serie_vto,
            "inicio_utc": p["desde"],
        }
        opciones_base = {"constantes": constantes, "elevacion": elevacion,
                         "barreras_extra": barreras, "resistencia_extra": resistencia,
                         "serie_ambiental": serie_amb,
                         "solo_conteo": True}

        veces = Counter()
        for semilla in range(1, args.repeticiones + 1):
            r = mod.ejecutar_automata(GRID, {**parametros, "semilla": semilla},
                                      opciones_base)
            quemadas = set(r["metadatos"]["celdas_quemadas_ids"])
            veces.update(quemadas)
            filas_rep.append({"evento": p["evento"], "semilla": semilla,
                              "celdas_quemadas": len(quemadas),
                              "area_km2": round(len(quemadas) * AREA_CELDA_KM2, 3)})
        n_rep = args.repeticiones

        # Máscara simulada con el umbral congelado
        simulado = {cid for cid, n in veces.items() if n / n_rep >= umbral}

        # --- Referencia observada ------------------------------------------
        if args.referencia in ("dnbr", "mcd64"):
            obs_df = pd.read_csv(args.observado)
            obs_df = obs_df[obs_df["valido"] == 1]
            observado = set()
            for _, row in obs_df[obs_df["observado"] == 1].iterrows():
                c = por_fc.get(f"{int(row['fila'])},{int(row['columna'])}")
                if c:
                    observado.add(c["id"])
            dominio = set()
            for _, row in obs_df.iterrows():
                c = por_fc.get(f"{int(row['fila'])},{int(row['columna'])}")
                if c:
                    dominio.add(c["id"])
        else:
            observado = set(p["celdas"])
            dominio = {c["id"] for c in GRID}

        # Las celdas de ignición se excluyen de los dos conjuntos: si no, el
        # modelo se lleva el mérito de unas celdas que se le regalaron como
        # condición inicial. Es la misma decisión que toma calibracion.py.
        excluir = set(p["ids_iniciales"])
        sim = {i for i in simulado if i not in excluir and i in dominio}
        obs = {i for i in observado if i not in excluir and i in dominio}
        dom = {i for i in dominio if i not in excluir}

        tp = len(sim & obs)
        fp = len(sim - obs)
        fn = len(obs - sim)
        tn = len(dom) - tp - fp - fn

        m = metricas(tp, fp, fn, tn)
        a_obs = len(obs) * AREA_CELDA_KM2
        a_sim = len(sim) * AREA_CELDA_KM2
        dif = a_sim - a_obs
        m.update({
            "evento": p["evento"],
            "desde": str(p["desde"])[:10], "hasta": str(p["hasta"])[:10],
            "focos_iniciales": len(p["iniciales"]),
            "pasos": pasos,
            "usa_clima_horario": serie_amb is not None,
            "area_observada_km2": round(a_obs, 3),
            "area_simulada_km2": round(a_sim, 3),
            "diferencia_area_km2": round(dif, 3),
            "razon_area": round(a_sim / a_obs, 3) if a_obs else None,
            "error_area_pct": round(abs(dif) / a_obs * 100, 2) if a_obs else None,
            "comportamiento": "SOBREESTIMACIÓN" if dif > 0 else
                              ("SUBESTIMACIÓN" if dif < 0 else "EXACTA"),
        })
        filas_ev.append(m)
        print(f"  TP {tp:>6,} · FP {fp:>6,} · FN {fn:>6,}")
        print(f"  IoU {m['iou']} · Precision {m['precision']} · "
              f"Recall {m['recall']} · F1 {m['f1']}")
        print(f"  Área observada {a_obs:.1f} km² · simulada {a_sim:.1f} km² "
              f"· razón x{m['razon_area']}")

    # --- Consolidado --------------------------------------------------------
    ev_df = pd.DataFrame(filas_ev)
    ev_df.to_csv(res / "apolo_metricas_por_evento.csv", index=False)
    pd.DataFrame(filas_rep).to_csv(res / "apolo_repeticiones.csv", index=False)

    print("\n" + "=" * 74)
    print("RESUMEN — APOLO")
    print("=" * 74)
    print(f"{'EVENTO':>10}{'IoU':>8}{'Prec':>8}{'Recall':>8}{'F1':>8}"
          f"{'A.obs':>9}{'A.sim':>9}{'razón':>8}")
    print("-" * 68)
    for _, r in ev_df.iterrows():
        print(f"{r.evento:>10}{r.iou:>8.3f}{r.precision:>8.3f}{r.recall:>8.3f}"
              f"{r.f1:>8.3f}{r.area_observada_km2:>9.1f}{r.area_simulada_km2:>9.1f}"
              f"{r.razon_area:>8.2f}")
    print("-" * 68)
    print(f"{'MEDIA':>10}{ev_df.iou.mean():>8.3f}{ev_df.precision.mean():>8.3f}"
          f"{ev_df.recall.mean():>8.3f}{ev_df.f1.mean():>8.3f}"
          f"{ev_df.area_observada_km2.mean():>9.1f}"
          f"{ev_df.area_simulada_km2.mean():>9.1f}{ev_df.razon_area.mean():>8.2f}")

    salida = {
        "area": "Apolo",
        "metodo": "mismo que Rurrenabaque: ventana corta, multi-foco, "
                  f"{args.repeticiones} repeticiones",
        "referencia_observada": args.referencia,
        "_aviso_referencia": (
            "FIRMS es un muestreo disperso de anomalías térmicas, no un polígono "
            "de área quemada. Subestima la superficie real e infla la "
            "sobreestimación aparente. Para la memoria hace falta el dNBR."
            if args.referencia == "firms" else
            "Cicatriz satelital dNBR, misma referencia que Rurrenabaque."),
        "ventana_dias": args.ventana_dias,
        "n_repeticiones": args.repeticiones,
        "semillas": list(range(1, args.repeticiones + 1)),
        "umbral_prob_quemada": umbral,
        "p_base": cfg["p_base"],
        "vecindad": constantes.get("VECINDAD", "moore"),
        "vecinos": len(vec),
        "usa_dem": elevacion is not None,
        "usa_barreras_osm": barreras is not None,
        "horas_max": args.horas_max,
        "fecha_calculo": datetime.now(timezone.utc).isoformat(),
        "eventos": filas_ev,
        "medias": {
            "iou": round(float(ev_df.iou.mean()), 4),
            "precision": round(float(ev_df.precision.mean()), 4),
            "recall": round(float(ev_df.recall.mean()), 4),
            "f1": round(float(ev_df.f1.mean()), 4),
            "razon_area": round(float(ev_df.razon_area.mean()), 3),
            "error_area_pct": round(float(ev_df.error_area_pct.mean()), 2),
        },
    }
    (res / "apolo_validacion.json").write_text(
        json.dumps(salida, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nEscrito: {res / 'apolo_validacion.json'}")
    print(f"Escrito: {res / 'apolo_metricas_por_evento.csv'}")
    print(f"Escrito: {res / 'apolo_repeticiones.csv'}")
    print("\nPara comparar con Rurrenabaque, ejecuta la misma cadena allí y")
    print("pon las dos filas en la misma tabla. Solo son comparables si los")
    print("dos usaron el MISMO p_base y la MISMA referencia observada.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
