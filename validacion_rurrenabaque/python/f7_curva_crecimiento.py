"""Curva de crecimiento: observado frente a simulado, y dónde se separan.

    cd validacion_rurrenabaque/python
    python f7_curva_crecimiento.py --csv ../datos/firms_rbq_2023.csv --evento E41

Compara CÓMO crece el incendio, no solo cuánto quemó al final.

Por qué importa: el autómata modela propagación física. Un incendio real
también responde a la intervención humana —cortafuegos, brigadas, quema
controlada— que el modelo no conoce. Comparar solo el área final penaliza al
modelo por algo que no puede saber.

La curva separa las dos cosas. Si observado y simulado crecen con la misma
pendiente y después el observado se corta de golpe, eso apunta a intervención
o a un cambio de condiciones, no a un error del modelo. Si divergen desde el
primer paso, el problema sí está en el modelo.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parents[1]
sys.path.insert(0, str(RAIZ / "validacion"))
sys.path.insert(0, str(RAIZ))

PASO_MIN = 15
AREA_CELDA = 0.25


def leer_focos(ruta: Path, evento: str | None, desde=None, hasta=None):
    filas = []
    with open(ruta, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if evento and str(r.get("evento", "")).upper() != evento.upper():
                continue
            la = r.get("latitude") or r.get("lat")
            lo = r.get("longitude") or r.get("lon")
            f = r.get("acq_date") or r.get("fecha")
            h = str(r.get("acq_time") or r.get("hora") or "0000").replace(":", "").zfill(4)
            if not (la and lo and f):
                continue
            try:
                t = datetime.strptime(f"{str(f)[:10]} {h}", "%Y-%m-%d %H%M")
                if desde and t < desde:
                    continue
                if hasta and t >= hasta:
                    continue
                filas.append({"lat": float(la), "lon": float(lo), "t": t,
                              "frp": float(r.get("frp") or 0)})
            except ValueError:
                continue
    filas.sort(key=lambda x: x["t"])
    return filas


def curva_observada(focos, celda_de, paso_min=PASO_MIN):
    """Celdas tocadas acumuladas, paso a paso, desde la primera detección."""
    if not focos:
        return []
    t0 = focos[0]["t"]
    tocadas, curva, i = set(), [], 0
    fin = focos[-1]["t"]
    n_pasos = int((fin - t0).total_seconds() // (paso_min * 60)) + 1
    for k in range(n_pasos + 1):
        limite = t0 + timedelta(minutes=paso_min * k)
        while i < len(focos) and focos[i]["t"] <= limite:
            c = celda_de(focos[i]["lat"], focos[i]["lon"])
            if c:
                tocadas.add(c)
            i += 1
        curva.append({"paso": k, "minutos": k * paso_min,
                      "hora": limite.isoformat(sep=" ")[:16],
                      "celdas": len(tocadas), "km2": len(tocadas) * AREA_CELDA})
    return curva


def curva_simulada(automata, GRID, uniq, p_base, semilla, pasos, paso_min):
    r = automata.ejecutar_automata(GRID, {
        "foco_fila": uniq[0][0], "foco_columna": uniq[0][1],
        "focos_iniciales": [{"fila": f, "columna": c} for f, c in uniq],
        "p_base": p_base, "num_iteraciones": pasos,
        "minutos_por_iteracion": paso_min, "multiplicador_viento": 1.0,
        "delta_humedad": 0.0, "delta_temperatura_c": 0.0, "semilla": semilla,
    }, {})
    salida = []
    for it in r["iteraciones"]:
        n = sum(1 for c in it["celdas"] if c["estado"] in ("ardiendo", "quemada"))
        salida.append({"paso": it["iteracion"], "minutos": it["iteracion"] * paso_min,
                       "celdas": n, "km2": n * AREA_CELDA})
    return salida


def analizar(obs, sim):
    """Dónde se separan las curvas y qué forma tiene cada una."""
    n = min(len(obs), len(sim))
    if n < 3:
        return None
    o = [x["celdas"] for x in obs[:n]]
    s = [x["celdas"] for x in sim[:n]]

    dif = [s[i] - o[i] for i in range(n)]
    rel = [(s[i] - o[i]) / max(o[i], 1) for i in range(n)]

    # Punto de separación: primer paso donde la simulación supera el doble de
    # lo observado y ya no vuelve por debajo.
    sep = None
    for i in range(1, n):
        if s[i] > 2 * max(o[i], 1) and all(s[j] > 2 * max(o[j], 1) for j in range(i, n)):
            sep = i
            break

    # Pendiente media por tramo: cuánto crece cada uno por paso
    def pendiente(v, a, b):
        return (v[b] - v[a]) / max(b - a, 1)

    tercio = max(n // 3, 1)
    tramos = [("inicial", 0, tercio), ("medio", tercio, 2 * tercio),
              ("final", 2 * tercio, n - 1)]

    # Correlación de Pearson entre ambas curvas
    mo, ms = sum(o) / n, sum(s) / n
    num = sum((o[i] - mo) * (s[i] - ms) for i in range(n))
    den = math.sqrt(sum((o[i] - mo) ** 2 for i in range(n))
                    * sum((s[i] - ms) ** 2 for i in range(n)))
    corr = num / den if den else None

    # Meseta observada: paso a partir del cual el observado deja de crecer
    meseta = None
    for i in range(n - 1, 0, -1):
        if o[i] > o[i - 1]:
            meseta = i
            break

    return {
        "pasos_comparados": n,
        "observado_final": o[-1], "simulado_final": s[-1],
        "razon_final": s[-1] / max(o[-1], 1),
        "paso_separacion": sep,
        "minutos_separacion": sep * PASO_MIN if sep is not None else None,
        "correlacion": corr,
        "diferencia_maxima": max(dif), "paso_diferencia_maxima": dif.index(max(dif)),
        "sobreestimacion_relativa_final": rel[-1],
        "meseta_observada_paso": meseta,
        "pendientes": {
            nombre: {"observado": pendiente(o, a, b), "simulado": pendiente(s, a, b)}
            for nombre, a, b in tramos if b > a
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True, help="CSV de focos FIRMS")
    ap.add_argument("--evento", default=None,
                    help="Filtra por la columna `evento` que añade f3_f4")
    ap.add_argument("--desde", default=None, help="AAAA-MM-DD, incluido")
    ap.add_argument("--hasta", default=None, help="AAAA-MM-DD, incluido")
    ap.add_argument("--zona", default="rurrenabaque")
    ap.add_argument("--grid", default=None,
                    help="CSV del grid. Por defecto, el de la zona "
                         "(../datos/rurrenabaque_grid_500m.csv)")
    ap.add_argument("--p-base", type=float, default=None,
                    help="Por defecto, el p_base de config/parametros_ca_congelados.json")
    ap.add_argument("--semilla", type=int, default=12345)
    ap.add_argument("--pasos", type=int, default=None)
    ap.add_argument("--horas", type=float, default=None,
                    help="Horizonte en horas; por defecto, la duración observada")
    ap.add_argument("--sin-simular", action="store_true",
                    help="Solo la curva observada, sin ejecutar el autómata")
    ap.add_argument("--salida", default="../resultados")
    args = ap.parse_args()

    ruta = Path(args.csv)
    if not ruta.exists():
        print(f"No existe {ruta}")
        return 1

    if args.evento:
        with open(ruta, newline="", encoding="utf-8-sig") as fh:
            cab = next(csv.reader(fh), [])
        if "evento" not in cab:
            etq = Path(args.salida) / "firms_eventos_etiquetados.csv"
            if not etq.exists():
                print(f"{ruta.name} no trae la columna `evento` y no existe {etq}.")
                print("Ejecuta antes: python f3_f4_analisis_y_evento.py")
                return 1
            print(f"{ruta.name} no trae la columna `evento`: uso {etq.name}")
            ruta = etq

    d0 = datetime.strptime(args.desde, "%Y-%m-%d") if args.desde else None
    d1 = (datetime.strptime(args.hasta, "%Y-%m-%d") + timedelta(days=1)) if args.hasta else None
    focos = leer_focos(ruta, args.evento, d0, d1)
    if not focos:
        print(f"Sin detecciones utilizables en {ruta.name}"
              + (f" para el evento {args.evento}" if args.evento else ""))
        return 1

    print("=" * 78)
    print("CURVA DE CRECIMIENTO · observado frente a simulado")
    print("=" * 78)
    print(f"Archivo     : {ruta.name}")
    if args.evento:
        print(f"Evento      : {args.evento}")
    print(f"Detecciones : {len(focos):,}")
    print(f"Periodo     : {focos[0]['t']:%Y-%m-%d %H:%M} a {focos[-1]['t']:%Y-%m-%d %H:%M}")
    dur_h = (focos[-1]['t'] - focos[0]['t']).total_seconds() / 3600
    print(f"Duración    : {dur_h:.1f} h ({dur_h / 24:.1f} días)")

    # --- Grid y localizador de celdas --------------------------------------
    # Antes, sin --grid se cargaba el grid del backend (Apolo, 36.390 celdas)
    # y ninguna detección de Rurrenabaque caía dentro: curva a 0. Ahora el
    # grid sale de la zona y el localizador no necesita Django.
    from rejilla import Rejilla, GRID_POR_ZONA
    ruta_grid = Path(args.grid or GRID_POR_ZONA.get(args.zona, ""))
    if not ruta_grid.is_file():
        print(f"No existe el grid {ruta_grid}. Ejecuta antes f8_construir_grid.py "
              f"o pásalo con --grid.")
        return 1
    rej = Rejilla.desde_csv(ruta_grid)

    def _num(v):
        try:
            return float(v) if v not in ("", None) else None
        except ValueError:
            return v
    GRID = [{k: (_num(v) if k not in ("id",) else v) for k, v in r.items()}
            for r in rej.celdas.values()]
    for g in GRID:
        g["fila"], g["columna"] = int(g["fila"]), int(g["columna"])

    def celda_de(la, lo):
        k = rej.celda(la, lo)
        return f"{k[0]},{k[1]}" if k else None

    def uniq_de(f):
        return sorted({k for k in (rej.celda(x["lat"], x["lon"]) for x in f) if k})

    fuera = sum(1 for x in focos if rej.celda(x["lat"], x["lon"]) is None)
    print(f"Grid CSV    : {ruta_grid.name}")
    if fuera:
        print(f"Fuera grid  : {fuera:,} detecciones (fuera del municipio)")
    print(f"Grid        : {len(GRID):,} celdas")

    # --- Curva observada ----------------------------------------------------
    obs = curva_observada(focos, celda_de)
    if not obs:
        print("\nNinguna detección cae dentro del grid.")
        return 1
    print(f"\nCURVA OBSERVADA")
    print(f"  celdas tocadas al final: {obs[-1]['celdas']} = {obs[-1]['km2']:.2f} km²")
    print(f"  pasos de {PASO_MIN} min  : {len(obs)}")

    # Muestra abreviada
    print(f"\n  {'PASO':>6}{'MINUTOS':>9}{'HORAS':>7}{'CELDAS':>8}{'km²':>8}")
    print("  " + "-" * 38)
    salto = max(len(obs) // 12, 1)
    for x in obs[::salto] + ([obs[-1]] if len(obs) % salto else []):
        print(f"  {x['paso']:>6}{x['minutos']:>9}{x['minutos'] / 60:>7.1f}"
              f"{x['celdas']:>8}{x['km2']:>8.2f}")

    if args.sin_simular:
        _guardar(args, ruta, focos, obs, None, None)
        return 0

    # --- Curva simulada -----------------------------------------------------
    uniq = uniq_de([f for f in focos
                    if (f["t"] - focos[0]["t"]).total_seconds() <= 6 * 3600])
    if not uniq:
        print("\nNinguna detección inicial cae en el grid: no hay dónde arrancar.")
        return 1

    if args.p_base is None:
        cfg = json.loads((AQUI.parent / "config" / "parametros_ca_congelados.json")
                         .read_text(encoding="utf-8"))
        args.p_base = cfg.get("p_base")
        if args.p_base is None:
            print("\np_base sigue en null en parametros_ca_congelados.json.")
            print("Ejecuta congelar_parametros.py tras calibrar Apolo, o usa --sin-simular.")
            return 2
    pasos = args.pasos or (int(args.horas * 60 / PASO_MIN) if args.horas
                           else len(obs) - 1)
    print(f"\nCURVA SIMULADA")
    print(f"  focos iniciales (primeras 6 h): {len(uniq)} celdas")
    print(f"  p_base {args.p_base} · semilla {args.semilla} · {pasos} pasos")

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("SIPRO_OMITIR_ARRANQUE", "1")
    os.environ.setdefault("PRECALENTAR", "false")
    os.environ.setdefault("SUPABASE_URL", "http://127.0.0.1:1")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "x" * 40)
    import django
    django.setup()
    from api.motor import automata
    sim = curva_simulada(automata, GRID, uniq, args.p_base, args.semilla,
                         pasos, PASO_MIN)
    print(f"  celdas al final: {sim[-1]['celdas']} = {sim[-1]['km2']:.2f} km²")
    if len(sim) - 1 < pasos:
        print(f"  El autómata se detuvo en el paso {len(sim) - 1} de {pasos}:")
        print(f"  no quedaban celdas ardiendo. La simulación se extinguió sola.")

    # --- Comparación --------------------------------------------------------
    a = analizar(obs, sim)
    print("\n" + "=" * 78)
    print("COMPARACIÓN DE LAS CURVAS")
    print("=" * 78)
    n = a["pasos_comparados"]
    print(f"  {'PASO':>6}{'HORAS':>7}{'OBSERVADO':>11}{'SIMULADO':>10}"
          f"{'DIFERENCIA':>12}{'RAZÓN':>8}")
    print("  " + "-" * 56)
    salto = max(n // 14, 1)
    for i in list(range(0, n, salto)) + [n - 1]:
        o, s = obs[i]["celdas"], sim[i]["celdas"]
        print(f"  {i:>6}{i * PASO_MIN / 60:>7.1f}{o:>11}{s:>10}"
              f"{s - o:>12}{s / max(o, 1):>8.1f}×")
    print("  " + "-" * 56)

    print(f"\n  Correlación entre curvas : {a['correlacion']:.3f}"
          if a["correlacion"] is not None else "")
    print(f"  Razón final simulado/observado: {a['razon_final']:.1f}×")
    if a["paso_separacion"] is not None:
        print(f"  Se separan en el paso {a['paso_separacion']} "
              f"({a['minutos_separacion'] / 60:.1f} h)")
    else:
        print(f"  No hay separación sostenida: las curvas no divergen del todo")
    if a["meseta_observada_paso"] is not None:
        mp = a["meseta_observada_paso"]
        print(f"  El observado deja de crecer en el paso {mp} "
              f"({mp * PASO_MIN / 60:.1f} h)")

    print(f"\n  PENDIENTE MEDIA por tramo (celdas nuevas por paso)")
    print(f"  {'TRAMO':<10}{'OBSERVADO':>11}{'SIMULADO':>10}{'RAZÓN':>8}")
    print("  " + "-" * 39)
    for nombre, v in a["pendientes"].items():
        r = v["simulado"] / v["observado"] if v["observado"] else None
        print(f"  {nombre:<10}{v['observado']:>11.2f}{v['simulado']:>10.2f}"
              f"{(f'{r:.1f}×' if r else '—'):>8}")

    # --- Lectura ------------------------------------------------------------
    print("\n  CÓMO LEERLO")
    if n < 20:
        print(f"  ATENCIÓN: solo {n} pasos comparables ({n * PASO_MIN / 60:.1f} h).")
        print("  La ventana es demasiado corta para concluir nada sobre la forma")
        print("  de las curvas. Ocurre cuando la simulación se extingue pronto:")
        print("  sube p_base o revisa las condiciones ambientales del grid.")
        print()
    pi = a["pendientes"].get("inicial", {})
    if pi.get("observado"):
        r_ini = pi["simulado"] / pi["observado"]
        if r_ini < 1.5:
            print("  La pendiente inicial coincide: el modelo reproduce bien el")
            print("  arranque. Si divergen después, el motivo está fuera del modelo")
            print("  —intervención, cambio de viento, barrera no cartografiada—")
            print("  y no en las reglas de propagación.")
        else:
            print(f"  La pendiente inicial ya difiere {r_ini:.1f}×: la divergencia")
            print("  no se explica por intervención posterior. Apunta a los")
            print("  parámetros o a los factores de propagación.")
    if a["meseta_observada_paso"] is not None and a["paso_separacion"] is not None:
        if a["meseta_observada_paso"] <= a["paso_separacion"] + 4:
            print("\n  El observado se detiene justo donde las curvas se separan.")
            print("  Eso es compatible con una extinción: el fuego real paró y el")
            print("  simulado siguió. No es un error de propagación.")

    print("\n  AVISO: la curva observada se construye con detecciones térmicas.")
    print("  Un satélite pasa cada pocas horas, así que la curva tiene escalones")
    print("  artificiales y subestima el crecimiento entre pasadas. Sirve para")
    print("  comparar FORMA y PENDIENTE, no para medir superficie exacta.")

    _guardar(args, ruta, focos, obs, sim, a)
    print("=" * 78)
    return 0


def _guardar(args, ruta, focos, obs, sim, a):
    res = Path(args.salida)
    res.mkdir(parents=True, exist_ok=True)
    nombre = f"curva_crecimiento{'_' + args.evento if args.evento else ''}"
    (res / f"{nombre}.json").write_text(json.dumps({
        "archivo": ruta.name, "evento": args.evento,
        "detecciones": len(focos),
        "inicio": focos[0]["t"].isoformat(), "fin": focos[-1]["t"].isoformat(),
        "paso_minutos": PASO_MIN,
        "parametros": {"p_base": args.p_base, "semilla": args.semilla},
        "observado": obs, "simulado": sim, "comparacion": a,
        "_nota": "La curva observada procede de detecciones térmicas, con la "
                 "cadencia de paso del satélite. Compara forma y pendiente, no "
                 "superficie absoluta.",
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    with open(res / f"{nombre}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["paso", "minutos", "horas", "observado_celdas",
                    "observado_km2", "simulado_celdas", "simulado_km2"])
        n = len(obs) if sim is None else min(len(obs), len(sim))
        for i in range(n):
            w.writerow([i, i * PASO_MIN, round(i * PASO_MIN / 60, 2),
                        obs[i]["celdas"], obs[i]["km2"],
                        sim[i]["celdas"] if sim else "",
                        sim[i]["km2"] if sim else ""])
    print(f"\nEscrito: {res / (nombre + '.json')}")
    print(f"Escrito: {res / (nombre + '.csv')}")


if __name__ == "__main__":
    raise SystemExit(main())
