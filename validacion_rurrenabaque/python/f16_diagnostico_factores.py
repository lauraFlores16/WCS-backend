"""
============================================================================
DIAGNÓSTICO — ¿LAS VARIABLES AMBIENTALES CAMBIAN ALGO EN EL AUTÓMATA?
============================================================================
    cd validacion_rurrenabaque/python
    python f16_diagnostico_factores.py                # 10 semillas, 24 h
    python f16_diagnostico_factores.py --semillas 5 --horas 12   # más rápido

QUÉ RESPONDE
    La validación dice que el autómata quema de más. No dice POR QUÉ. Esto
    ejecuta el MISMO motor, el MISMO evento (E122), los MISMOS parámetros
    congelados y los MISMOS datos, cambiando UNA sola cosa cada vez:

      · apagar un factor ambiental (viento, humedad, pendiente, clima horario,
        vegetación, saltos de pavesas), y
      · escalar p_base (×0,5 … ×0,02) para localizar el umbral de propagación.

    Si al apagar un factor el área NO cambia, ese factor no está influyendo en
    la simulación, y eso es un hallazgo sobre el motor, no sobre los datos.

    Si el área pasa de «casi nada» a «todo el municipio» en un rango estrecho
    de p_base, el autómata se comporta como un proceso de percolación: por
    encima de un umbral el fuego se propaga sin límite, por debajo se apaga.

QUÉ NO ES
    No es una recalibración. Ningún valor de aquí se usa para cambiar los
    parámetros congelados de la validación. Es un análisis de sensibilidad.

COMPRUEBA TAMBIÉN
    Que el motor es el del proyecto: imprime la huella SHA-256 de
    backend_django/api/motor/automata.py y su estado en git.

SALIDA
    ../resultados/diagnostico/diagnostico_factores.csv
    ../resultados/diagnostico/diagnostico_factores.json
============================================================================
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import time
from pathlib import Path
from statistics import median

import pandas as pd

import f11_ejecutar_ca_rbq as F11

AREA = F11.AREA_CELDA_KM2
MIN = F11.MINUTOS_POR_ITERACION


def huella_motor() -> dict:
    ruta = F11.BACKEND / "api" / "motor" / "automata.py"
    info = {"ruta": str(ruta)}
    if ruta.exists():
        info["sha256"] = hashlib.sha256(ruta.read_bytes()).hexdigest()
        info["modificado"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(ruta.stat().st_mtime))
        try:
            st = subprocess.run(["git", "status", "--porcelain", str(ruta)], cwd=F11.RAIZ_PROYECTO,
                                capture_output=True, text=True, timeout=20)
            lg = subprocess.run(["git", "log", "-1", "--format=%h %ad %s", "--date=short", "--", str(ruta)],
                                cwd=F11.RAIZ_PROYECTO, capture_output=True, text=True, timeout=20)
            info["git_cambios_sin_commit"] = st.stdout.strip() or "ninguno"
            info["git_ultimo_commit"] = lg.stdout.strip() or "(sin historial)"
        except Exception as e:  # noqa: BLE001
            info["git"] = f"no disponible ({type(e).__name__})"
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grid", default="../datos/rurrenabaque_grid_500m.csv")
    ap.add_argument("--focos", default="../resultados/focos_iniciales.csv")
    ap.add_argument("--config", default="../config/parametros_ca_congelados.json")
    ap.add_argument("--evento", default="../resultados/evento_validacion.json")
    ap.add_argument("--resultados", default="../resultados/diagnostico")
    ap.add_argument("--semillas", type=int, default=10)
    ap.add_argument("--horas", type=float, default=24.0)
    ap.add_argument("--solo", default=None, help="Ejecutar solo estos experimentos (ids separados por comas)")
    args = ap.parse_args()

    res = Path(args.resultados)
    res.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print("DIAGNÓSTICO DE FACTORES DEL AUTÓMATA · Rurrenabaque E122")
    print("=" * 78)
    hm = huella_motor()
    print("Motor:")
    for k, v in hm.items():
        print(f"  {k:<24}: {v}")

    grid_df = pd.read_csv(args.grid)
    focos_df = pd.read_csv(args.focos)
    cfg = F11.cargar_parametros_congelados(Path(args.config))
    evento = json.loads(Path(args.evento).read_text(encoding="utf-8"))
    if not ({"fila", "columna"} <= set(focos_df.columns)
            and focos_df[["fila", "columna"]].notna().all().all()):
        focos_df = F11.asignar_focos_al_grid(
            focos_df.drop(columns=[c for c in ("grid_id", "fila", "columna") if c in focos_df.columns]),
            grid_df)
    iniciales = focos_df[["fila", "columna"]].drop_duplicates().astype(int).to_dict("records")
    n_pasos = int(round(args.horas * 60 / MIN))

    ejecutar_automata, mod = F11.cargar_motor()
    base_const = dict(mod.CONSTANTES_POR_DEFECTO)
    base_const.update(cfg["constantes"])

    # --- Entorno idéntico al de f11 --------------------------------------
    serie = None
    try:
        from api.servicios.meteo import obtener_serie_ambiental_historica
        la0 = float(grid_df.loc[grid_df["fila"] == iniciales[0]["fila"], "lat"].iloc[0])
        lo0 = float(grid_df.loc[grid_df["columna"] == iniciales[0]["columna"], "lon"].iloc[0])
        serie = obtener_serie_ambiental_historica(la0, lo0, evento.get("fecha_inicio"),
                                                  evento.get("fecha_fin"))
    except Exception as e:  # noqa: BLE001
        print(f"  AVISO: sin clima horario ({type(e).__name__})")
    serie_vto = None
    try:
        from api.servicios.meteo import obtener_serie_viento_historica
        serie_vto = obtener_serie_viento_historica(la0, lo0, evento.get("inicio_utc") or evento.get("fecha_inicio"),
                                                   evento.get("fin_utc") or evento.get("fecha_fin"))
    except Exception as e:  # noqa: BLE001
        print(f"  AVISO: sin viento horario ({type(e).__name__})")
    grid_celdas = grid_df.to_dict("records")
    barreras = resistencia = None
    try:
        from api.servicios import terreno
        bbox = {"sur": float(grid_df.lat.min()), "norte": float(grid_df.lat.max()),
                "oeste": float(grid_df.lon.min()), "este": float(grid_df.lon.max())}
        osm = terreno.obtener_terreno_osm(bbox, grid_celdas)
        barreras, resistencia = set(osm["barreras"]), osm["resistencia"]
    except Exception as e:  # noqa: BLE001
        print(f"  AVISO: sin capa OSM ({type(e).__name__})")
    elevacion = {r["id"]: r["elevacion_m"] for r in grid_celdas
                 if pd.notna(r.get("elevacion_m"))}

    print(f"\nEvento {evento.get('evento_id')} · {len(iniciales)} focos · {args.horas:g} h "
          f"({n_pasos} pasos) · {args.semillas} semillas por experimento")
    print(f"p_base congelado {cfg['p_base']} · clima {'sí' if serie else 'NO'} · "
          f"OSM {'sí' if barreras is not None else 'NO'}")

    # Datos del grid, para interpretar
    for col in ("ndvi", "humedad", "pendiente_grados"):
        s = grid_df[col]
        print(f"  {col:<17} p5 {s.quantile(.05):.3f} · mediana {s.median():.3f} · p95 {s.quantile(.95):.3f}")
    vel = (grid_df.viento_u ** 2 + grid_df.viento_v ** 2) ** .5
    print(f"  {'viento (m/s)':<17} p5 {vel.quantile(.05):.3f} · mediana {vel.median():.3f} · p95 {vel.quantile(.95):.3f}")

    # --- Experimentos ----------------------------------------------------
    def ndvi_fijo(v):
        g = copy.deepcopy(grid_celdas)
        for c in g:
            c["ndvi"] = v
        return g

    def humedad_fija(v):
        g = copy.deepcopy(grid_celdas)
        for c in g:
            c["humedad"] = v
        return g

    pb = cfg["p_base"]
    E = [  # id, descripción, cambios
        ("base", "Tal cual la validación", {}),
        ("sin_viento", "Viento × 0", {"multiplicador_viento": 0.0}),
        ("viento_x3", "Viento × 3", {"multiplicador_viento": 3.0}),
        ("sin_clima", "Sin clima horario ERA5 (sin ciclo día/noche)", {"_serie": None}),
        ("k_hum_0", "K_HUMEDAD = 0 (sin efecto de la humedad del suelo)", {"_const": {"K_HUMEDAD": 0.0}}),
        ("humedad_alta", "Humedad del suelo = 0,45 en todo el grid", {"_grid": humedad_fija(0.45)}),
        ("humedad_baja", "Humedad del suelo = 0,15 en todo el grid", {"_grid": humedad_fija(0.15)}),
        ("sin_pendiente", "K_PENDIENTE arriba y abajo = 0", {"_const": {"K_PENDIENTE_ARRIBA": 0.0, "K_PENDIENTE_ABAJO": 0.0}}),
        ("ndvi_03", "NDVI = 0,30 en todo el grid (rango de Apolo)", {"_grid": ndvi_fijo(0.30)}),
        ("sin_spotting", "Sin saltos de pavesas", {"_const": {"SPOTTING_ACTIVO": False}}),
        ("sin_ambiente", "Sin viento, sin humedad, sin pendiente, sin clima, sin saltos",
         {"multiplicador_viento": 0.0, "_serie": None,
          "_const": {"K_HUMEDAD": 0.0, "K_PENDIENTE_ARRIBA": 0.0, "K_PENDIENTE_ABAJO": 0.0,
                     "SPOTTING_ACTIVO": False, "HR_REFERENCIA": 0.60}}),
    ] + [(f"pbase_x{k:g}".replace(".", "_"), f"p_base × {k:g} = {pb * k:.4f}", {"p_base": pb * k})
         for k in (2.0, 1.5, 0.5, 0.25)]
    if args.solo:
        quiero = set(args.solo.split(","))
        E = [e for e in E if e[0] in quiero or e[0] == "base"]

    filas = []
    print(f"\n{'EXPERIMENTO':<16}{'MEDIANA km²':>12}{'MÍN':>9}{'MÁX':>9}{'vs BASE':>9}{'APAGADAS':>10}{'s':>6}")
    print("-" * 71)
    area_base = None
    for eid, desc, cambios in E:
        t0 = time.time()
        const = dict(base_const)
        const.update(cambios.get("_const", {}))
        grid = cambios.get("_grid", grid_celdas)
        ser = cambios["_serie"] if "_serie" in cambios else serie
        areas, apag = [], 0
        for s in range(1, args.semillas + 1):
            params = {"focos_iniciales": iniciales, "foco_fila": iniciales[0]["fila"],
                      "foco_columna": iniciales[0]["columna"], "p_base": cambios.get("p_base", pb),
                      "num_iteraciones": n_pasos, "minutos_por_iteracion": MIN,
                      "multiplicador_viento": cambios.get("multiplicador_viento", 1.0),
                      "delta_humedad": 0.0, "delta_temperatura_c": 0.0,
                      "constantes": const, "semilla": s, "inicio_utc": evento.get("inicio_utc")}
            r = ejecutar_automata(grid, params, {"elevacion": elevacion, "barreras_extra": barreras,
                                                 "resistencia_extra": resistencia, "serie_ambiental": ser,
                                                 "serie_viento": serie_vto})
            ult = r["iteraciones"][-1]
            n = sum(1 for c in ult["celdas"] if c.get("estado") in ("quemada", "ardiendo"))
            areas.append(n * AREA)
            if ult["iteracion"] < n_pasos:
                apag += 1
        med = median(areas)
        if eid == "base":
            area_base = med
        cambio = (med / area_base - 1) * 100 if area_base else 0.0
        filas.append({"experimento": eid, "descripcion": desc, "mediana_km2": round(med, 2),
                      "min_km2": round(min(areas), 2), "max_km2": round(max(areas), 2),
                      "cambio_vs_base_pct": round(cambio, 1), "apagadas_solas": apag,
                      "semillas": args.semillas, "areas_km2": [round(a, 2) for a in areas]})
        print(f"{eid:<16}{med:>12.1f}{min(areas):>9.1f}{max(areas):>9.1f}{cambio:>+8.0f}%"
              f"{apag:>6}/{args.semillas:<3}{time.time() - t0:>6.0f}")
    print("-" * 71)

    # --- Lectura ---------------------------------------------------------
    df = pd.DataFrame(filas)
    print("\nCÓMO LEERLO")
    print("  · Un factor que cambia el área menos de ±15 % casi no influye.")
    print("  · 'sin_ambiente' frente a 'base' dice cuánto frenan, en conjunto,")
    print("    todas las variables ambientales. Si la diferencia es pequeña, el")
    print("    fuego simulado depende casi solo de p_base y de la vecindad.")
    print("  · En la serie p_base, busca dónde el área cae de golpe: ese es el")
    print("    umbral de propagación del autómata. La cicatriz real del evento")
    print(f"    son 7,25 km²; la validación está muy por encima del umbral.")
    débiles = df[(df.experimento.isin(["sin_viento", "sin_clima", "k_hum_0", "sin_pendiente",
                                       "ndvi_03", "humedad_alta"])) & (df.cambio_vs_base_pct.abs() < 15)]
    if len(débiles):
        print("\n  Factores que casi NO cambian el resultado:")
        for _, x in débiles.iterrows():
            print(f"    - {x.descripcion} ({x.cambio_vs_base_pct:+.0f} %)")

    df.drop(columns=["areas_km2"]).to_csv(res / "diagnostico_factores.csv", index=False)
    (res / "diagnostico_factores.json").write_text(json.dumps({
        "motor": hm, "evento": evento.get("evento_id"), "horas": args.horas,
        "semillas": args.semillas, "p_base_congelado": pb,
        "experimentos": filas,
        "_nota": "Análisis de sensibilidad. No modifica los parámetros congelados.",
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nEscrito: {res / 'diagnostico_factores.csv'}")
    print(f"Escrito: {res / 'diagnostico_factores.json'}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
