"""
============================================================================
FASE 20 — EXPORTAR LOS RESULTADOS AL FRONTEND
============================================================================
Ruta: validacion_rurrenabaque/python/f14_exportar_frontend.py

    cd validacion_rurrenabaque/python
    python f14_exportar_frontend.py

QUÉ HACE
    Toma lo que produjeron f11 y f12 y deja dos archivos en
    `frontend/public/datos/` que la pestaña VALIDACIÓN de SIPRO FIRE lee
    directamente.

POR QUÉ ASÍ Y NO POR LA BASE DE DATOS
    La validación no necesita persistencia: es un resultado científico
    congelado, no un dato operativo que cambie. Servirlo como archivo estático
    evita crear tablas, migraciones y endpoints para algo que se calcula una
    vez. Si más adelante hay varias validaciones, entonces sí tendrá sentido
    una tabla.

QUÉ GENERA
    frontend/public/datos/validacion_rbq.json
        Métricas, matriz de confusión, áreas, datos del evento y de la
        configuración. Es el contrato que consume la pantalla.

    frontend/public/datos/validacion_rbq_celdas.json
        Solo las celdas que aportan algo al mapa: las observadas, las
        simuladas y las que tienen probabilidad de quema > 0. Las TN puras se
        omiten porque son decenas de miles de celdas que no se pintan y
        harían el archivo inmanejable en el navegador. El total de TN va en
        el JSON de métricas, que es donde hace falta.

SI NO HAY RESULTADOS TODAVÍA
    El script no inventa nada: avisa de qué falta y sale. La pantalla, por su
    parte, muestra un estado vacío explicando qué hay que ejecutar.
============================================================================
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[2]
DESTINO = RAIZ.parent / "WCS-frontend" / "public" / "datos"  # WCS-backend/../WCS-frontend


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--resultados", default="../resultados")
    ap.add_argument("--destino", default=str(DESTINO))
    ap.add_argument("--focos", default="../resultados/focos_del_evento.csv")
    ap.add_argument("--focos-firms", default=None,
                    help="CSV de focos FIRMS. Con esto y sin métricas todavía, "
                         "exporta solo la actividad térmica observada para que "
                         "la pantalla enseñe el dato real que ya existe.")
    ap.add_argument("--demo", action="store_true",
                    help="Marca la exportación como DATOS DE DEMOSTRACIÓN. La "
                         "pantalla muestra entonces un aviso bien visible y el "
                         "informe lo lleva escrito. Lo usa demo_completa.py.")
    args = ap.parse_args()

    res = Path(args.resultados)
    dst = Path(args.destino)
    dst.mkdir(parents=True, exist_ok=True)

    faltan = []
    metricas_p = res / "metricas_validacion.json"
    comparacion_p = res / "comparacion_espacial.csv"
    if not metricas_p.exists():
        faltan.append(f"{metricas_p}  (lo genera f12_metricas_validacion.py)")
    if not comparacion_p.exists():
        faltan.append(f"{comparacion_p}  (lo genera f12_metricas_validacion.py)")

    # --- Modo parcial -------------------------------------------------------
    # Si todavía no hay métricas pero SÍ hay focos FIRMS descargados, se
    # exportan solo esos. La pantalla enseña entonces la actividad térmica
    # observada —que es dato real y citable— y deja claro que la cicatriz y
    # las métricas están pendientes.
    #
    # Es mejor que un estado vacío: el trabajo hecho se ve, y lo que falta se
    # nombra en vez de parecer que no existe.
    if faltan and args.focos_firms:
        ruta_f = Path(args.focos_firms)
        if not ruta_f.exists():
            print(f"No existe {ruta_f}")
            return 1
        f = pd.read_csv(ruta_f)
        campos = [c for c in ("latitude", "longitude", "lat", "lon", "acq_date",
                              "fecha", "acq_time", "hora", "frp", "confidence",
                              "fuente_firms", "sensor") if c in f.columns]
        ff = f[campos].copy()
        ff = ff.rename(columns={"latitude": "lat", "longitude": "lon",
                                "acq_date": "fecha", "acq_time": "hora",
                                "fuente_firms": "sensor"})
        ff["lat"] = pd.to_numeric(ff["lat"], errors="coerce")
        ff["lon"] = pd.to_numeric(ff["lon"], errors="coerce")
        ff = ff.dropna(subset=["lat", "lon"])

        dst.mkdir(parents=True, exist_ok=True)
        (dst / "validacion_rbq_focos.json").write_text(
            json.dumps({"n": int(len(ff)),
                        "focos": ff.round(6).to_dict("records")},
                       ensure_ascii=False), encoding="utf-8")

        parcial = {
            "_parcial": True,
            "_parcial_aviso": (
                "Datos reales de NASA FIRMS. Todavía NO hay cicatriz observada "
                "ni métricas: hace falta descargar el área quemada (MCD64A1) y "
                "ejecutar el autómata."),
            "area": "Rurrenabaque", "departamento": "Beni",
            "anio": 2023,
            "evento": {"id": "actividad térmica observada",
                       "focos_firms": int(len(ff))},
            "modelo": {"resolucion_m": 500, "vecindad": "Moore",
                       "radio": 1, "vecinos": 8},
            "areas": {}, "metricas": {}, "confusion": {},
            "complementarias": {}, "cobertura": {},
            "comportamiento": None, "advertencias": [],
        }
        if "fecha" in ff.columns and len(ff):
            fechas = sorted(str(x)[:10] for x in ff["fecha"].dropna().unique())
            if fechas:
                parcial["evento"]["fecha_inicio"] = fechas[0]
                parcial["evento"]["fecha_fin"] = fechas[-1]
        if "sensor" in ff.columns and ff["sensor"].notna().any():
            parcial["evento"]["sensor_principal"] = str(ff["sensor"].mode().iloc[0])

        (dst / "validacion_rbq.json").write_text(
            json.dumps(parcial, indent=2, ensure_ascii=False), encoding="utf-8")
        for resto in ("validacion_rbq_celdas.json",):
            if (dst / resto).exists():
                (dst / resto).unlink()

        print("=" * 74)
        print("EXPORTACIÓN PARCIAL — solo focos FIRMS")
        print("=" * 74)
        print(f"  {len(ff):,} focos reales llevados a la pantalla")
        if "fecha" in parcial["evento"]:
            print(f"  periodo: {parcial['evento'].get('fecha_inicio')} a "
                  f"{parcial['evento'].get('fecha_fin')}")
        print()
        print("  La pestaña Validación mostrará la actividad térmica observada")
        print("  y dirá que la cicatriz y las métricas están pendientes.")
        print()
        print("  Para completarla:")
        print("    1. Descarga MCD64A1 de AppEEARS (ver f9_importar_area_quemada.py)")
        print("    2. python f9_importar_area_quemada.py --entrada ../datos/mcd64a1.csv")
        print("    3. python f11_ejecutar_ca_rbq.py")
        print("    4. python f12_metricas_validacion.py")
        print("    5. python f14_exportar_frontend.py")
        print("=" * 74)
        return 0

    if faltan:
        print("=" * 74)
        print("NO HAY RESULTADOS QUE EXPORTAR")
        print("=" * 74)
        for f in faltan:
            print(f"  falta: {f}")
        print("\nEjecuta primero la cadena completa:")
        print("  python f2_descargar_firms_2023.py")
        print("  python f3_f4_analisis_y_evento.py")
        print("  (bloques 05-08 de GEE)")
        print("  python f10_verificar_grid_rbq.py")
        print("  python f11_ejecutar_ca_rbq.py")
        print("  python f12_metricas_validacion.py")
        print("\nNo se ha escrito nada. La pantalla de Validación seguirá")
        print("mostrando su estado vacío, que es lo correcto mientras no")
        print("existan resultados reales.")
        print("=" * 74)
        return 1

    met = json.loads(metricas_p.read_text(encoding="utf-8"))
    comp = pd.read_csv(comparacion_p)

    ejec = {}
    p_ejec = res / "f11_resumen_ejecucion.json"
    if p_ejec.exists():
        ejec = json.loads(p_ejec.read_text(encoding="utf-8"))

    evento = {}
    p_ev = res / "evento_validacion.json"
    if p_ev.exists():
        evento = json.loads(p_ev.read_text(encoding="utf-8"))

    # --- Contrato para la pantalla -----------------------------------------
    contrato = {
        "_generado_por": "validacion_rurrenabaque/python/f14_exportar_frontend.py",
        "_advertencia": "Todos los valores proceden de una ejecución real. "
                        "Si algo falta, es que no se ha calculado.",
        "_demo": bool(args.demo),
        "_demo_aviso": ("DATOS DE DEMOSTRACIÓN. El terreno es sintético y la "
                        "cicatriz está dibujada a mano. Ningún valor de esta "
                        "pantalla sirve para la memoria.") if args.demo else None,
        "area": "Rurrenabaque",
        "departamento": "Beni",
        "anio": evento.get("anio", 2023),
        "fecha_calculo": met.get("fecha_calculo"),

        "evento": {
            "id": evento.get("evento_id") or met.get("evento"),
            "fecha_inicio": evento.get("fecha_inicio"),
            "fecha_fin": evento.get("fecha_fin"),
            "focos_firms": evento.get("numero_focos"),
            "sensor_principal": evento.get("sensor_principal"),
            "criterio_seleccion": evento.get("criterio_seleccion"),
            "duracion_h": evento.get("duracion_h"),
        },

        "modelo": {
            "resolucion_m": 500,
            "vecindad": "Moore",
            "radio": 1,
            "vecinos": ejec.get("vecinos_del_motor", 8),
            "corridas": ejec.get("n_repeticiones"),
            "umbral_prob_quemada": ejec.get("umbral_prob_quemada"),
            "p_base": ejec.get("p_base"),
            "parametros_congelados_el": ejec.get("fecha_congelacion_parametros"),
            "usa_dem": ejec.get("usa_dem"),
            "usa_barreras_osm": ejec.get("usa_barreras_osm"),
            "usa_clima_horario": ejec.get("usa_clima_horario"),
        },

        "areas": {
            "observada_km2": met.get("area_observada_km2"),
            "simulada_km2": met.get("area_simulada_km2"),
            "diferencia_km2": met.get("diferencia_area_km2"),
            "error_relativo_pct": met.get("error_area_pct"),
        },

        "metricas": {
            "iou": met.get("iou"),
            "precision": met.get("precision"),
            "recall": met.get("recall"),
            "f1": met.get("f1"),
            "accuracy": met.get("accuracy"),
        },

        "confusion": {
            "tp": met.get("tp"), "fp": met.get("fp"),
            "fn": met.get("fn"), "tn": met.get("tn"),
        },

        "complementarias": {
            "error_angular_grados": met.get("error_angular_grados"),
            "distancia_centroides_km": met.get("distancia_centroides_km"),
            "angulo_observado_grados": met.get("angulo_observado_grados"),
            "angulo_simulado_grados": met.get("angulo_simulado_grados"),
        },

        "cobertura": {
            "celdas_evaluadas": met.get("celdas_evaluadas"),
            "celdas_excluidas_sin_dato": met.get("celdas_excluidas_sin_dato"),
            "celdas_totales_cruzadas": met.get("celdas_totales_cruzadas"),
        },

        "comportamiento": met.get("comportamiento"),
        "advertencias": met.get("advertencias", []),
    }

    (dst / "validacion_rbq.json").write_text(
        json.dumps(contrato, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- Celdas para el mapa -----------------------------------------------
    # Solo lo que se pinta. Las TN puras se omiten: son decenas de miles de
    # celdas que la pantalla no dibuja y que harían el archivo enorme.
    interesantes = comp[
        (comp["observado"] == 1) | (comp["simulado"] == 1) |
        (comp.get("prob_quemada", pd.Series(0, index=comp.index)) > 0)
    ].copy()

    cols = ["fila", "columna", "lat", "lon", "observado", "simulado", "clase"]
    if "prob_quemada" in interesantes.columns:
        cols.append("prob_quemada")

    celdas = {
        "_nota": "Solo celdas con observación, simulación o probabilidad > 0. "
                 "Las TN puras se omiten; su total está en validacion_rbq.json.",
        "resolucion_m": 500,
        "n": int(len(interesantes)),
        "campos": cols,
        "celdas": interesantes[cols].round(6).to_dict("records"),
    }
    (dst / "validacion_rbq_celdas.json").write_text(
        json.dumps(celdas, ensure_ascii=False), encoding="utf-8")

    # --- Focos FIRMS del evento, para superponer ---------------------------
    p_focos = Path(args.focos)
    if p_focos.exists():
        f = pd.read_csv(p_focos)
        campos = [c for c in ("lat", "lon", "fecha", "hora", "frp", "sensor")
                  if c in f.columns]
        (dst / "validacion_rbq_focos.json").write_text(
            json.dumps({"n": int(len(f)),
                        "focos": f[campos].round(6).to_dict("records")},
                       ensure_ascii=False), encoding="utf-8")
        print(f"  focos FIRMS del evento: {len(f)}")

    print("=" * 74)
    print("EXPORTADO PARA EL FRONTEND")
    print("=" * 74)
    print(f"  {dst / 'validacion_rbq.json'}")
    print(f"  {dst / 'validacion_rbq_celdas.json'}   ({len(interesantes):,} celdas)")
    print(f"\n  IoU {contrato['metricas']['iou']} · "
          f"F1 {contrato['metricas']['f1']} · "
          f"área {contrato['areas']['simulada_km2']} vs "
          f"{contrato['areas']['observada_km2']} km²")
    print("\nAbre SIPRO FIRE → Simulación → pestaña VALIDACIÓN")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
