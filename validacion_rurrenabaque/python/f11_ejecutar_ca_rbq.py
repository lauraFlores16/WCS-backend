"""
============================================================================
FASE 11 — EJECUCIÓN DEL AUTÓMATA CELULAR EN RURRENABAQUE
============================================================================
Ruta: validacion_rurrenabaque/python/f11_ejecutar_ca_rbq.py

QUÉ HACE
    Ejecuta N repeticiones (30 por defecto) del autómata celular de SIPRO FIRE
    sobre el grid de Rurrenabaque, con los parámetros congelados y los focos
    iniciales de FIRMS, y construye la probabilidad de quema por celda.

REUTILIZA EL MOTOR, NO LO COPIA
    Importa `ejecutar_automata` de backend_django/api/motor/automata.py. Es
    literalmente la misma función que corre en producción para Apolo: mismas
    reglas R1-R4, mismos factores, mismo generador. No hay aquí ni una línea
    de lógica de propagación.

    Eso es lo que hace que esto sea una validación EXTERNA de verdad. Si el
    autómata se reimplementara aquí, se estaría validando una copia, y
    cualquier divergencia entre las dos —una constante distinta, un signo
    cambiado— pasaría desapercibida.

POR QUÉ 30 REPETICIONES Y NO UNA
    El autómata es estocástico. Medido sobre Apolo: la misma configuración con
    semilla 12345 quema 108 celdas y con semilla 999 quema 159. Un 47 % de
    diferencia por cambiar solo la semilla.

    Con una sola corrida no se sabe si un IoU bajo es del modelo o de la
    tirada. Con 30 sale una PROBABILIDAD DE QUEMA por celda, que es mucho más
    informativa: dice no solo dónde llega el fuego sino con qué consistencia.

    Las semillas son 1..N, consecutivas y registradas. Nada de elegir la mejor
    corrida: eso sería escoger el resultado que más conviene.

QUÉ GENERA
    ../resultados/ca_repeticiones.csv        una fila por repetición
    ../resultados/ca_probabilidad_quema.csv  P_quema por celda
    ../resultados/ca_resultado_final.csv     máscara simulada final
    ../resultados/ca_evolucion_temporal.csv  celdas activadas por paso
    ../resultados/f11_resumen_ejecucion.json trazabilidad completa

CÓMO SE EJECUTA
    pip install pandas numpy
    python f11_ejecutar_ca_rbq.py
    python f11_ejecutar_ca_rbq.py --repeticiones 50 --umbral 0.5

ANTES DE PODER EJECUTARLO
    1. f10_verificar_grid_rbq.py debe pasar sin fallos.
    2. config/parametros_ca_congelados.json debe tener `p_base` resuelto. Sale
       null a propósito y este script se niega a arrancar hasta que se elija
       un valor conscientemente. Ver el bloque `_p_base_BLOQUEA_LA_EJECUCION`
       de ese archivo.
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

RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
BACKEND = RAIZ_PROYECTO

AREA_CELDA_KM2 = 0.25       # 500 × 500 m
MINUTOS_POR_ITERACION = 15


def cargar_motor():
    """Importa el motor real. Sin trucos: el mismo módulo que usa el backend."""
    if not BACKEND.exists():
        raise SystemExit(
            f"ERROR: no se encuentra el backend en {BACKEND}.\n"
            "Este script tiene que vivir dentro del proyecto para poder "
            "reutilizar el motor. No se va a reimplementar el autómata aquí."
        )
    sys.path.insert(0, str(BACKEND))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("SIPRO_OMITIR_ARRANQUE", "1")
    os.environ.setdefault("PRECALENTAR", "false")
    os.environ.setdefault("SUPABASE_URL", "http://127.0.0.1:1")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "no-se-usa")
    import django
    django.setup()
    from api.motor.automata import ejecutar_automata  # noqa: E402
    from api.motor import automata as mod             # noqa: E402
    return ejecutar_automata, mod


def cargar_parametros_congelados(ruta: Path) -> dict:
    cfg = json.loads(ruta.read_text(encoding="utf-8"))

    if cfg.get("p_base") is None:
        bloque = cfg.get("_p_base_BLOQUEA_LA_EJECUCION", {})
        print("=" * 74)
        print("EJECUCIÓN BLOQUEADA — falta decidir `p_base`")
        print("=" * 74)
        print(bloque.get("problema", ""))
        print()
        for c in bloque.get("candidatos", []):
            print(f"  · p_base = {c['valor']}")
            print(f"      origen : {c['origen']}")
            print(f"      implica: {c['implica']}")
            if "constantes_asociadas" in c:
                print("      OJO: si tomas este valor, toma también sus cinco")
                print("      constantes asociadas. Son el mismo óptimo.")
            print()
        print(f"Recomendación: {bloque.get('recomendacion','')}")
        print()
        print("Escribe el valor elegido en el campo `p_base` de")
        print(f"  {ruta}")
        print("y rellena `fecha_congelacion`. Después vuelve a lanzar esto.")
        print("=" * 74)
        raise SystemExit(2)

    if cfg.get("fecha_congelacion") is None:
        print("AVISO: `fecha_congelacion` está en null. Rellénala: forma parte")
        print("de la trazabilidad de la validación.\n")

    return cfg


def asignar_focos_al_grid(focos: pd.DataFrame, grid: pd.DataFrame) -> pd.DataFrame:
    """Empareja cada foco FIRMS con su celda del grid.

    Se usa la celda cuyo centro queda más cerca, en metros y no en grados: a
    esta latitud un grado de longitud mide un 3 % menos que uno de latitud, y
    trabajar en grados sesgaría el emparejamiento hacia el eje este-oeste.
    """
    lat_ref = float(grid["lat"].mean())
    m_lat = 110574.0
    m_lon = 111320.0 * math.cos(math.radians(lat_ref))

    gl = grid["lat"].to_numpy(float)
    go = grid["lon"].to_numpy(float)

    filas = []
    for _, f in focos.iterrows():
        dy = (gl - float(f["lat"])) * m_lat
        dx = (go - float(f["lon"])) * m_lon
        d = (dy ** 2 + dx ** 2) ** 0.5
        i = int(d.argmin())
        celda = grid.iloc[i]
        filas.append({
            **{k: f[k] for k in f.index if k in
               ("fecha", "hora", "acq_date", "acq_time", "lat", "lon",
                "frp", "sensor", "fuente_firms", "confidence")},
            "grid_id": celda["id"],
            "fila": int(celda["fila"]),
            "columna": int(celda["columna"]),
            "distancia_m": round(float(d[i]), 1),
        })
    return pd.DataFrame(filas)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--grid", default="../datos/rurrenabaque_grid_500m.csv")
    ap.add_argument("--focos", default="../resultados/focos_iniciales.csv")
    ap.add_argument("--config", default="../config/parametros_ca_congelados.json")
    ap.add_argument("--evento", default="../resultados/evento_validacion.json")
    ap.add_argument("--resultados", default="../resultados")
    ap.add_argument("--repeticiones", type=int, default=None)
    ap.add_argument("--umbral", type=float, default=None,
                    help="Fracción de repeticiones para marcar la celda como "
                         "quemada. NO moverlo tras ver las métricas.")
    ap.add_argument("--horas", type=float, default=None,
                    help="Horizonte en horas. Por defecto, la duración del evento.")
    ap.add_argument("--sin-clima", action="store_true",
                    help="No traer el clima horario real del evento. Sin ciclo "
                         "diurno el frente no se frena de noche y sobreestima.")
    ap.add_argument("--sin-spotting", action="store_true",
                    help="Análisis de sensibilidad: SPOTTING_ACTIVO = false. Usar "
                         "con --resultados en OTRA carpeta para no pisar la corrida "
                         "principal. No sustituye al resultado congelado.")
    ap.add_argument("--instantaneas", default=None,
                    help="Horas separadas por comas, p. ej. 1,2,3,6,12,24,48,96. Guarda "
                         "el mapa del fuego simulado en esos momentos "
                         "(ca_instantaneas.csv) para comparar la secuencia con la "
                         "observada. Con las mismas semillas reproduce la corrida "
                         "principal: usar --resultados en otra carpeta.")
    ap.add_argument("--sin-terreno", action="store_true",
                    help="No traer barreras ni DEM de OpenStreetMap/SRTM.")
    args = ap.parse_args()

    res = Path(args.resultados)
    res.mkdir(parents=True, exist_ok=True)

    # --- Entradas -----------------------------------------------------------
    ruta_grid = Path(args.grid)
    if not ruta_grid.exists():
        print(f"ERROR: no existe {ruta_grid}. Ejecuta el bloque 08 de GEE.")
        return 1
    grid_df = pd.read_csv(ruta_grid)

    ruta_focos = Path(args.focos)
    if not ruta_focos.exists():
        print(f"ERROR: no existe {ruta_focos}.")
        print("Ejecuta antes f3_f4_analisis_y_evento.py.")
        return 1
    focos_df = pd.read_csv(ruta_focos)

    cfg = cargar_parametros_congelados(Path(args.config))

    evento = {}
    ruta_ev = Path(args.evento)
    if ruta_ev.exists():
        evento = json.loads(ruta_ev.read_text(encoding="utf-8"))

    n_rep = args.repeticiones or cfg["ejecucion"]["n_repeticiones"]
    umbral = args.umbral if args.umbral is not None else cfg["ejecucion"]["umbral_prob_quemada"]

    # Horizonte: la duración real del evento, no un número redondo.
    if args.horas is not None:
        horas = args.horas
    elif evento.get("duracion_h"):
        horas = float(evento["duracion_h"])
    else:
        horas = 24.0
        print("AVISO: sin duración en evento_validacion.json, se usan 24 h.")
    n_pasos = max(int(round(horas * 60 / MINUTOS_POR_ITERACION)), 1)

    print("=" * 74)
    print("FASE 11 — EJECUCIÓN DEL AUTÓMATA EN RURRENABAQUE")
    print("=" * 74)
    print(f"Grid              : {len(grid_df):,} celdas")
    print(f"Evento            : {evento.get('evento_id', '(sin identificar)')}")
    print(f"Horizonte         : {horas:.1f} h = {n_pasos} pasos de {MINUTOS_POR_ITERACION} min")
    print(f"Repeticiones      : {n_rep}  (semillas 1..{n_rep})")
    print(f"Umbral de quema   : {umbral}")
    print(f"p_base congelado  : {cfg['p_base']}")
    print(f"Congelado el      : {cfg.get('fecha_congelacion')}")

    # --- Focos iniciales ----------------------------------------------------
    # f3_f4 escribe `fila`/`columna` VACÍAS (aún no conoce el grid). Antes
    # bastaba con que existiera la columna para darlas por buenas y el
    # arranque fallaba al convertir NaN a entero.
    if ({"fila", "columna"} <= set(focos_df.columns)
            and focos_df[["fila", "columna"]].notna().all().all()):
        focos_grid = focos_df
    else:
        print("\nAsignando focos FIRMS a celdas del grid…")
        focos_grid = asignar_focos_al_grid(
            focos_df.drop(columns=[c for c in ("grid_id", "fila", "columna")
                                   if c in focos_df.columns]), grid_df)
        focos_grid.to_csv(res / "focos_iniciales.csv", index=False)

    iniciales = (focos_grid[["fila", "columna"]]
                 .drop_duplicates()
                 .astype(int)
                 .to_dict("records"))
    print(f"\nFocos iniciales   : {len(focos_df)} detecciones "
          f"→ {len(iniciales)} celdas distintas")
    for f in iniciales[:8]:
        print(f"    f{f['fila']} c{f['columna']}")
    if len(iniciales) > 8:
        print(f"    … y {len(iniciales) - 8} más")
    if len(iniciales) > 1:
        print("  Se arranca con TODAS. Quedarse con una porque dé mejor")
        print("  coincidencia invalidaría la validación externa.")

    # --- Motor --------------------------------------------------------------
    print("\nCargando el motor de SIPRO FIRE…")
    ejecutar_automata, mod = cargar_motor()

    constantes = dict(mod.CONSTANTES_POR_DEFECTO)
    constantes.update(cfg["constantes"])
    if args.sin_spotting:
        constantes["SPOTTING_ACTIVO"] = False
        print("  SENSIBILIDAD: spotting DESACTIVADO (SPOTTING_ACTIVO = false)")
        if Path(args.resultados).resolve() == Path("../resultados").resolve():
            print("  ERROR: usa --resultados ..\\resultados\\sens_sin_spotting para no")
            print("  sobrescribir la corrida principal.")
            return 2

    vec = mod._construir_vecindad(constantes["RADIO_VECINDAD"],
                                  constantes["EXP_DISTANCIA"])
    print(f"  motor    : api/motor/automata.py (el mismo que usa el backend)")
    print(f"  vecindad : {len(vec)} vecinos"
          f" ({constantes.get('VECINDAD', 'moore')})"
          + ("" if len(vec) == 8 else "  ← NO son los 8 de Moore, revisa VECINDAD"))

    # --- Clima horario REAL del evento --------------------------------------
    # El autómata no tiene mecanismo propio para apagarse: sin ciclo diurno el
    # frente crece indefinidamente. Medido sobre Apolo, un horizonte de 96 h
    # sin clima cubre el 91 % del municipio pase lo que pase. Con la serie
    # ERA5 del evento, de noche la humedad sube y la propagación se frena.
    serie_amb = None
    if not args.sin_clima:
        try:
            from api.servicios.meteo import obtener_serie_ambiental_historica
            lat0 = float(grid_df.loc[grid_df["fila"] == iniciales[0]["fila"], "lat"].iloc[0])
            lon0 = float(grid_df.loc[grid_df["columna"] == iniciales[0]["columna"], "lon"].iloc[0])
            serie_amb = obtener_serie_ambiental_historica(
                lat0, lon0, evento.get("fecha_inicio"), evento.get("fecha_fin"))
            print(f"  clima ERA5 del evento: {len(serie_amb)} horas")
        except Exception as e:  # noqa: BLE001
            print(f"  sin clima horario ({type(e).__name__}): el frente no tendrá")
            print("  ciclo diurno y tenderá a sobreestimar. Declárarlo.")
    # Viento horario del evento: el mismo insumo que recibe la calibración.
    serie_vto = None
    try:
        from api.servicios.meteo import obtener_serie_viento_historica
        lat0 = float(grid_df.loc[grid_df["fila"] == iniciales[0]["fila"], "lat"].iloc[0])
        lon0 = float(grid_df.loc[grid_df["columna"] == iniciales[0]["columna"], "lon"].iloc[0])
        serie_vto = obtener_serie_viento_historica(
            lat0, lon0, evento.get("inicio_utc") or evento.get("fecha_inicio"),
            evento.get("fin_utc") or evento.get("fecha_fin"))
        print(f"  viento ERA5 horario: {len(serie_vto)} horas")
    except Exception as e:  # noqa: BLE001
        print(f"  sin viento horario ({type(e).__name__}): se usa el viento medio del grid")

    grid_celdas = grid_df.to_dict("records")

    # --- Terreno: barreras duras y resistencia parcial -----------------------
    elevacion = barreras = resistencia = None
    if not args.sin_terreno:
        try:
            from api.servicios import terreno
            bbox = {"sur": float(grid_df["lat"].min()), "norte": float(grid_df["lat"].max()),
                    "oeste": float(grid_df["lon"].min()), "este": float(grid_df["lon"].max())}
            # La rejilla va como argumento: sin ella OSM rasterizaría
            # sobre el grid de Apolo y ninguna carretera caería en una
            # celda de Rurrenabaque.
            osm = terreno.obtener_terreno_osm(bbox, grid_celdas)
            barreras = set(osm["barreras"])
            resistencia = osm["resistencia"]
            print(f"  terreno OSM: {len(barreras)} barreras duras · "
                  f"{len(resistencia)} celdas con resistencia")
        except Exception as e:  # noqa: BLE001
            print(f"  sin capa OSM ({type(e).__name__}): sin ríos ni caminos que")
            print("  frenen el frente, el modelo tenderá a sobreestimar.")
    if "elevacion_m" in grid_df.columns:
        elevacion = {r["id"]: r["elevacion_m"] for _, r in grid_df.iterrows()
                     if pd.notna(r.get("elevacion_m"))}
        print(f"  elevación desde el grid: {len(elevacion)} celdas")

    base_parametros = {
        "focos_iniciales": iniciales,
        "foco_fila": iniciales[0]["fila"],
        "foco_columna": iniciales[0]["columna"],
        "p_base": cfg["p_base"],
        "num_iteraciones": n_pasos,
        "minutos_por_iteracion": MINUTOS_POR_ITERACION,
        "multiplicador_viento": 1.0,
        "delta_humedad": 0.0,
        "delta_temperatura_c": 0.0,
        "constantes": constantes,
        "inicio_utc": evento.get("inicio_utc"),
    }

    # --- Las N repeticiones -------------------------------------------------
    print(f"\nEjecutando {n_rep} repeticiones…")
    print(f"{'SEM':>4}{'QUEMADAS':>11}{'ARDIENDO':>11}{'km²':>9}{'PASOS':>7}{'SPOT':>6}")
    print("-" * 48)

    veces_quemada: Counter = Counter()
    snaps = sorted({float(x) for x in args.instantaneas.split(",")}) if args.instantaneas else []
    veces_snap = {h: Counter() for h in snaps}
    filas_rep = []
    evolucion = {}          # paso → lista de celdas activadas acumuladas
    total_spotting = 0

    for semilla in range(1, n_rep + 1):
        params = {**base_parametros, "semilla": semilla}
        r = ejecutar_automata(grid_celdas, params, {
            "elevacion": elevacion,
            "barreras_extra": barreras,
            "resistencia_extra": resistencia,
            "serie_ambiental": serie_amb,
            "serie_viento": serie_vto,
        })
        its = r["iteraciones"]
        ultima = its[-1]

        # Celdas que terminaron quemadas O ardiendo: la cicatriz observada por
        # satélite incluye lo que seguía ardiendo al final del evento.
        #
        # El motor identifica cada celda por `celda_id`, que es el `id` del
        # grid. Se usa esa clave y no (fila, columna) porque es la que emite
        # `construir_iteracion` y evita cualquier reconstrucción por nuestra
        # parte: menos sitios donde equivocarse.
        quemadas_ids = {c["celda_id"] for c in ultima["celdas"]
                        if c.get("estado") in ("quemada", "ardiendo")}
        veces_quemada.update(quemadas_ids)

        if semilla == 1:
            md = r["metadatos"]
            print(f"  modelo: {md.get('modelo', 'v2')} · evento empieza {evento.get('inicio_utc')} · "
                  f"serie ambiental desde {md.get('serie_ambiental_desde')} · "
                  f"viento desde {md.get('serie_viento_desde')}")
        n_spot = len(r["metadatos"].get("eventos_spotting", []))
        total_spotting += n_spot

        # Instantáneas: qué había quemado (o ardiendo) a las h horas. Si la
        # corrida se apagó antes, vale su último estado.
        if snaps:
            por_paso = {it["iteracion"]: it for it in its}
            for h in snaps:
                p_ = int(round(h * 60 / MINUTOS_POR_ITERACION))
                it_ = por_paso.get(p_) or (ultima if p_ >= ultima["iteracion"]
                                           else max((it for it in its if it["iteracion"] <= p_),
                                                    key=lambda it: it["iteracion"]))
                veces_snap[h].update(c["celda_id"] for c in it_["celdas"]
                                     if c.get("estado") in ("quemada", "ardiendo"))

        filas_rep.append({
            "semilla": semilla,
            "celdas_quemadas": len(quemadas_ids),
            "area_km2": round(len(quemadas_ids) * AREA_CELDA_KM2, 3),
            "duracion_pasos": ultima["iteracion"],
            "duracion_horas": round(ultima["iteracion"] * MINUTOS_POR_ITERACION / 60, 2),
            "eventos_spotting": n_spot,
            "interrumpido_por": r["metadatos"].get("interrumpido_por"),
        })

        # Evolución temporal: se acumula sobre la primera repetición para no
        # mezclar realizaciones distintas en una misma curva.
        if semilla == 1:
            for it in its:
                evolucion[it["iteracion"]] = {
                    "paso": it["iteracion"],
                    "minutos": it["iteracion"] * MINUTOS_POR_ITERACION,
                    "horas": round(it["iteracion"] * MINUTOS_POR_ITERACION / 60, 2),
                    "celdas_ardiendo": it["num_celdas_ardiendo"],
                    "celdas_quemadas": it["num_celdas_quemadas"],
                    "area_ca_km2": round(it["num_celdas_quemadas"] * AREA_CELDA_KM2, 3),
                }

        print(f"{semilla:>4}{len(quemadas_ids):>11}"
              f"{ultima['num_celdas_ardiendo']:>11}"
              f"{len(quemadas_ids) * AREA_CELDA_KM2:>9.1f}"
              f"{ultima['iteracion']:>7}{n_spot:>6}")

    rep_df = pd.DataFrame(filas_rep)
    rep_df.to_csv(res / "ca_repeticiones.csv", index=False)

    # --- Dispersión entre repeticiones -------------------------------------
    q = rep_df["celdas_quemadas"]
    print("-" * 48)
    print(f"\nDispersión entre las {n_rep} repeticiones")
    print(f"  mínimo {q.min():,} · mediana {q.median():,.0f} · máximo {q.max():,} celdas")
    print(f"  media  {q.mean():,.1f} ± {q.std():,.1f}  "
          f"(coef. de variación {q.std() / max(q.mean(), 1e-9) * 100:.1f} %)")
    print("  Esta dispersión es del modelo, no un error. Es la razón de no")
    print("  validar con una sola corrida.")

    if total_spotting > 0:
        print(f"\n  Saltos de pavesas: {total_spotting} en total "
              f"({total_spotting / max(n_rep, 1):.0f} por repetición).")
        print("  Para medir cuánto pesan: --sin-spotting en otra carpeta.")

    # --- Probabilidad de quema ---------------------------------------------
    print(f"\nConstruyendo la probabilidad de quema…")
    filas_prob = []
    for _, celda in grid_df.iterrows():
        n = veces_quemada.get(celda["id"], 0)
        filas_prob.append({
            "id": celda["id"],
            "fila": int(celda["fila"]), "columna": int(celda["columna"]),
            "lat": celda["lat"], "lon": celda["lon"],
            "veces_quemada": n,
            "n_repeticiones": n_rep,
            "prob_quemada": round(n / n_rep, 4),
        })
    prob_df = pd.DataFrame(filas_prob).sort_values(["fila", "columna"])
    prob_df.to_csv(res / "ca_probabilidad_quema.csv", index=False)

    # --- Máscara simulada final --------------------------------------------
    prob_df["simulado"] = (prob_df["prob_quemada"] >= umbral).astype(int)
    prob_df[["id", "fila", "columna", "lat", "lon", "prob_quemada", "simulado"]] \
        .to_csv(res / "ca_resultado_final.csv", index=False)

    n_sim = int(prob_df["simulado"].sum())
    print(f"\nCeldas con prob_quemada > 0   : {int((prob_df['prob_quemada'] > 0).sum()):,}")
    print(f"Celdas simuladas (≥ {umbral})   : {n_sim:,}")
    print(f"Área simulada                 : {n_sim * AREA_CELDA_KM2:.2f} km²")

    # Cuánto mueve el umbral. Se publica para que la elección quede declarada,
    # no para escoger el que dé mejores métricas.
    print("\nSensibilidad al umbral (informativa, NO para elegirlo):")
    for u in (0.10, 0.25, 0.50, 0.75, 0.90):
        n = int((prob_df["prob_quemada"] >= u).sum())
        marca = "  ← el usado" if abs(u - umbral) < 1e-9 else ""
        print(f"    ≥ {u:.2f} → {n:>7,} celdas · {n * AREA_CELDA_KM2:>8.2f} km²{marca}")

    # --- Instantáneas ------------------------------------------------------
    if snaps:
        pos = {r_["id"]: (int(r_["fila"]), int(r_["columna"]), r_["lat"], r_["lon"])
               for _, r_ in grid_df.iterrows()}
        filas_s = []
        for h in snaps:
            for cid, n in veces_snap[h].items():
                if cid in pos:
                    f_, c_, la_, lo_ = pos[cid]
                    filas_s.append({"horas": h, "id": cid, "fila": f_, "columna": c_,
                                    "lat": la_, "lon": lo_, "prob_quemada": round(n / n_rep, 4)})
        snap_df = pd.DataFrame(filas_s)
        snap_df.to_csv(res / "ca_instantaneas.csv", index=False)
        print("\nInstantáneas (celdas con prob ≥ umbral):")
        for h in snaps:
            n = int((snap_df[(snap_df.horas == h)]["prob_quemada"] >= umbral).sum()) if len(snap_df) else 0
            print(f"    {h:>5g} h → {n:>6,} celdas · {n * AREA_CELDA_KM2:>8.2f} km²")
        print(f"Escrito: {res / 'ca_instantaneas.csv'}")

    # --- Evolución temporal -------------------------------------------------
    pd.DataFrame(list(evolucion.values())).to_csv(
        res / "ca_evolucion_temporal.csv", index=False)

    # --- Trazabilidad -------------------------------------------------------
    resumen = {
        "_fase": "FASE 11",
        "fecha_ejecucion": datetime.now(timezone.utc).isoformat(),
        "motor": "backend_django/api/motor/automata.py (reutilizado, no copiado)",
        "grid": {"archivo": str(ruta_grid), "celdas": int(len(grid_df))},
        "evento": evento.get("evento_id"),
        "horizonte_horas": horas,
        "pasos": n_pasos,
        "minutos_por_iteracion": MINUTOS_POR_ITERACION,
        "focos_iniciales": iniciales,
        "n_repeticiones": n_rep,
        "semillas": list(range(1, n_rep + 1)),
        "umbral_prob_quemada": umbral,
        "p_base": cfg["p_base"],
        "fecha_congelacion_parametros": cfg.get("fecha_congelacion"),
        "constantes_usadas": constantes,
        "vecinos_del_motor": len(vec),
        "usa_clima_horario": serie_amb is not None,
        "usa_barreras_osm": barreras is not None,
        "usa_dem": elevacion is not None,
        "estadisticos_repeticiones": {
            "min": int(q.min()), "max": int(q.max()),
            "media": round(float(q.mean()), 2),
            "desviacion": round(float(q.std()), 2),
            "mediana": float(q.median()),
        },
        "celdas_simuladas": n_sim,
        "area_simulada_km2": round(n_sim * AREA_CELDA_KM2, 3),
        "eventos_spotting_totales": total_spotting,
        "sensibilidad_sin_spotting": bool(args.sin_spotting),
        "advertencias": (
            []
        ) + ([] if len(vec) == 8 else
             [f"El motor usa {len(vec)} vecinos, no los 8 de Moore"]),
    }
    (res / "f11_resumen_ejecucion.json").write_text(
        json.dumps(resumen, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nEscrito: {res / 'ca_repeticiones.csv'}")
    print(f"Escrito: {res / 'ca_probabilidad_quema.csv'}")
    print(f"Escrito: {res / 'ca_resultado_final.csv'}")
    print(f"Escrito: {res / 'ca_evolucion_temporal.csv'}")
    print(f"Escrito: {res / 'f11_resumen_ejecucion.json'}")
    print("\nSiguiente: python f12_metricas_validacion.py")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
