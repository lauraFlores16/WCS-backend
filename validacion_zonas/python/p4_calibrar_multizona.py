"""
P4 — Calibración con VARIOS municipios (solo los de rol 'calibracion').

    python p4_calibrar_multizona.py                       # todos los paquetes de calibración
    python p4_calibrar_multizona.py --poblacion 12 --generaciones 10 --semillas 5

Afila el motor con más incendios que los de Apolo, SIN tocar la validación:

  · Solo entra un paquete si su municipio es de 'calibracion' en
    config/particion.json. Los de 'validacion' se rechazan aunque se pidan.
  · El método es el de la calibración del backend (evolución diferencial,
    mismos genes y rangos), pero la aptitud se mide como en la validación:
    conjunto de N semillas, umbral 0,5 y F1 contra la cicatriz de MapBiomas,
    con el mismo castigo simétrico al error de área (×3 de más = ×3 de menos).
  · El punto de partida es el juego CONGELADO actual: así se ve si mejora.

NO sobrescribe los parámetros congelados. Escribe un CANDIDATO en
../config/candidatos/candidato_<fecha>.json con su aptitud por paquete y la
del juego congelado, para decidir con datos si vale la pena congelarlo.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone

from comun import RAIZ_ZONAS, escribir_json, rol_de
from p3_validar_evento import preparar_django


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paquetes", default=None, help="Ids separados por coma (por defecto, todos los de calibración)")
    ap.add_argument("--poblacion", type=int, default=12)
    ap.add_argument("--generaciones", type=int, default=10)
    ap.add_argument("--semillas", type=int, default=5)
    ap.add_argument("--f", type=float, default=0.7)
    ap.add_argument("--cr", type=float, default=0.9)
    ap.add_argument("--semilla-de", type=int, default=2026, help="Semilla de la evolución diferencial")
    args = ap.parse_args()

    V = preparar_django()
    from api.motor import automata
    from api.motor.calibracion import GENES, NOMBRES, _genes_a_parametros

    todos = {p["id"]: p for p in V.listar_paquetes()}
    pedidos = args.paquetes.split(",") if args.paquetes else [
        pid for pid, p in todos.items() if p["rol"] == "calibracion"]
    usados = []
    for pid in pedidos:
        p = todos.get(pid)
        if not p:
            print(f"  ✗ {pid}: no existe")
            continue
        rol = rol_de(p["municipio_id"]) or p["rol"]
        if rol != "calibracion":
            print(f"  ✗ {pid}: su municipio es de '{rol}'. Se excluye: usarlo para calibrar "
                  "invalidaría la validación externa.")
            continue
        usados.append(pid)
    if not usados:
        print("No hay paquetes de calibración. Prepara alguno con:")
        print("  python p2_preparar_evento.py --municipio <id> --anio <año> --evento <E…> --rol calibracion")
        return 2

    datos = {pid: V._cargar(pid) for pid in usados}
    cfg = next(iter(datos.values()))["cfg"]
    semillas = list(range(1, args.semillas + 1))
    umbral = cfg["ejecucion"]["umbral_prob_quemada"]
    random.seed(args.semilla_de)

    def opciones(d):
        ins = d["insumos"]
        return {"elevacion": {c["id"]: c["elevacion_m"] for c in d["grid"] if c.get("elevacion_m") is not None},
                "barreras_extra": set(ins.get("barreras_extra") or []),
                "resistencia_extra": ins.get("resistencia_extra") or {},
                "serie_ambiental": ins.get("serie_ambiental"), "serie_viento": ins.get("serie_viento")}
    OPC = {pid: opciones(d) for pid, d in datos.items()}

    def evaluar(genes: dict) -> dict:
        constantes = {**automata.CONSTANTES_POR_DEFECTO, **cfg["constantes"],
                      **{k: v for k, v in genes.items() if k != "p_base"}}
        detalle, suma = {}, 0.0
        for pid, d in datos.items():
            base = {**d["insumos"]["parametros_base"], "p_base": genes["p_base"], "constantes": constantes}
            veces = {}
            for s in semillas:
                r = automata.ejecutar_automata(d["grid"], {**base, "semilla": s}, OPC[pid])
                for c in r["iteraciones"][-1]["celdas"]:
                    if c.get("estado") in ("quemada", "ardiendo"):
                        veces[c["celda_id"]] = veces.get(c["celda_id"], 0) + 1
            sim = {k for k, v in veces.items() if v / len(semillas) >= umbral}
            m = V.calcular_metricas(sim, d["observado"], d["focos"])
            ao, as_ = m["area_observada_km2"] or 0.25, m["area_simulada_km2"] or 0.0
            razon = as_ / ao if ao else 0
            castigo = 3 / razon if razon > 3 else (razon * 3 if razon < 1 / 3 else 1)
            apt = (m["f1"] or 0) * castigo
            suma += apt
            detalle[pid] = {"aptitud": round(apt, 4), "f1": m["f1"], "iou": m["iou"],
                            "area_obs_km2": ao, "area_sim_km2": as_}
        return {"aptitud": suma / len(datos), "detalle": detalle, "genes": genes}

    def a_vector(g: dict) -> list[float]:
        return [min(max(float(g.get(n, (GENES[n][0] + GENES[n][1]) / 2)), GENES[n][0]), GENES[n][1])
                for n in NOMBRES]

    congelado = {"p_base": cfg["p_base"], **{n: cfg["constantes"].get(n) for n in NOMBRES if n != "p_base"}}
    congelado = {k: v for k, v in congelado.items() if v is not None}

    print("=" * 74)
    print(f"CALIBRACIÓN MULTIZONA · {len(usados)} paquete(s) · población {args.poblacion} · "
          f"{args.generaciones} generaciones · {len(semillas)} semillas")
    print("=" * 74)
    for pid in usados:
        print(f"  · {pid}")
    base_eval = evaluar(_genes_a_parametros(a_vector(congelado)))
    print(f"\nAptitud del juego CONGELADO actual: {base_eval['aptitud']:.4f}")

    dim = len(NOMBRES)
    pob = [[GENES[n][0] + random.random() * (GENES[n][1] - GENES[n][0]) for n in NOMBRES]
           for _ in range(args.poblacion)]
    pob[0] = a_vector(congelado)
    punt = [base_eval] + [evaluar(_genes_a_parametros(v)) for v in pob[1:]]
    historia = [{"generacion": 0, "mejor": max(p["aptitud"] for p in punt)}]
    for gen in range(1, args.generaciones + 1):
        for i in range(args.poblacion):
            a, b, c = random.sample([k for k in range(args.poblacion) if k != i], 3)
            jr = random.randrange(dim)
            prueba = [min(max(pob[a][j] + args.f * (pob[b][j] - pob[c][j]), GENES[NOMBRES[j]][0]), GENES[NOMBRES[j]][1])
                      if (random.random() < args.cr or j == jr) else pob[i][j] for j in range(dim)]
            res = evaluar(_genes_a_parametros(prueba))
            if res["aptitud"] > punt[i]["aptitud"]:
                pob[i], punt[i] = prueba, res
        mejor = max(punt, key=lambda p: p["aptitud"])
        historia.append({"generacion": gen, "mejor": round(mejor["aptitud"], 4)})
        print(f"  generación {gen:>2}: mejor aptitud {mejor['aptitud']:.4f}")

    mejor = max(punt, key=lambda p: p["aptitud"])
    ahora = datetime.now(timezone.utc)
    cand = {
        "_documento": "CANDIDATO de parámetros. No está congelado: revisar y decidir.",
        "fecha": ahora.isoformat(), "modelo": cfg.get("modelo"),
        "paquetes_calibracion": usados,
        "metodo": {"algoritmo": "evolución diferencial DE/rand/1/bin", "poblacion": args.poblacion,
                   "generaciones": args.generaciones, "F": args.f, "CR": args.cr,
                   "semillas_por_evaluacion": len(semillas), "umbral_conjunto": umbral,
                   "aptitud": "media de F1 × castigo simétrico de área, contra MapBiomas"},
        "p_base": round(mejor["genes"]["p_base"], 4),
        "genes": {k: round(v, 4) for k, v in mejor["genes"].items()},
        "aptitud": round(mejor["aptitud"], 4), "detalle": mejor["detalle"],
        "congelado_actual": {"aptitud": round(base_eval["aptitud"], 4), "detalle": base_eval["detalle"],
                             "fecha_congelacion": cfg.get("fecha_congelacion")},
        "mejora": round(mejor["aptitud"] - base_eval["aptitud"], 4),
        "historia": historia,
    }
    ruta = RAIZ_ZONAS / "config" / "candidatos" / f"candidato_{ahora:%Y%m%d_%H%M}.json"
    escribir_json(ruta, cand)
    print("-" * 74)
    print(f"Aptitud candidato {cand['aptitud']} frente a congelado {cand['congelado_actual']['aptitud']} "
          f"(mejora {cand['mejora']:+.4f})")
    for pid, x in mejor["detalle"].items():
        b = base_eval["detalle"][pid]
        print(f"  {pid:<50} F1 {b['f1']} → {x['f1']}")
    print(f"\nEscrito: {ruta}")
    print("Para comprobarlo de verdad, valida con los municipios de VALIDACIÓN (que el")
    print("candidato no vio). Solo si mejora ahí tiene sentido congelarlo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
