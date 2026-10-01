"""
============================================================================
CONGELAR LOS PARÁMETROS DEL AUTÓMATA DESDE LA CALIBRACIÓN
============================================================================
Ruta: validacion_rurrenabaque/python/congelar_parametros.py

    cd validacion_rurrenabaque/python
    python congelar_parametros.py

QUÉ HACE
    Lee la calibración vigente que guardó el backend y rellena
    config/parametros_ca_congelados.json: `p_base`, los nueve genes, la fecha
    y las métricas con las que se obtuvo.

POR QUÉ NO SE COPIA A MANO
    Son nueve valores con cuatro decimales. Copiarlos de la pantalla al JSON
    invita a tres errores, y los tres son silenciosos:

      · olvidar uno, y quedarse con el valor por defecto sin darse cuenta;
      · mezclar el p_base nuevo con constantes de una calibración anterior
        —y eso da una combinación que nunca se evaluó, así que no es ni la
        vieja ni la nueva;
      · equivocarse en un decimal.

    Ninguno da error. Los tres producen una validación que mide un modelo
    distinto del que se calibró, y no hay forma de notarlo mirando los
    resultados. De ahí que esto esté automatizado.

QUÉ NECESITA
    Que la calibración se haya ejecutado y guardado. Se lanza desde la
    interfaz: Monitoreo → Capa 4 → Calibrar constantes K. Necesita internet,
    porque descarga el DEM, las barreras de OpenStreetMap y el viento ERA5
    histórico de las fechas de cada evento.

QUÉ NO HACE
    No calibra. Solo traslada un resultado ya obtenido. Y respeta la regla:
    una vez congelados, esos valores NO se tocan con los resultados de
    Rurrenabaque.
============================================================================
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
BACKEND = RAIZ

# Los nueve genes que optimiza la calibración. Van juntos o no van: son un
# mismo óptimo, y mezclar unos con los valores por defecto de otros da una
# combinación que el optimizador nunca evaluó.
GENES = [
    "p_base", "K_VIENTO", "K_PENDIENTE_ARRIBA", "K_PENDIENTE_ABAJO",
    "HUMEDAD_EXTINCION", "K_HUMEDAD", "NDVI_BARRERA", "K_BARRERA",
    "SPOTTING_PROB",
]


PLANTILLA_V3 = json.loads(r'''{"_documento": "PARÁMETROS DEL AUTÓMATA CELULAR (MOTOR v3) CONGELADOS PARA LA VALIDACIÓN EXTERNA", "_regla": "Estos valores se fijan ANTES de ejecutar Rurrenabaque y NO se modifican con sus resultados. Si el modelo produce IoU bajo, F1 bajo, sobreestimación o subestimación, SE REPORTA. Reajustarlos mirando las métricas de Rurrenabaque convertiría la validación externa en un ajuste disfrazado y la dejaría sin valor.", "modelo": "velocidad_v3", "origen_parametros": "backend_django/api/motor/automata.py v3 · CONSTANTES_POR_DEFECTO. Se sustituyen los genes por los de la calibración de Apolo con congelar_parametros.py.", "fecha_congelacion": null, "version_modelo": "Autómata celular v3 — propagación por velocidad de avance (ROS)", "area_calibracion": "Apolo, provincia Franz Tamayo, La Paz", "area_validacion": "Rurrenabaque, provincia General José Ballivián, Beni", "p_base": null, "_p_base_nota": "En v3 p_base es la escala de la velocidad de avance: ROS_base = p_base × 100 m/h. Queda en null hasta recalibrar Apolo con el motor v3; f11 y f13 se niegan a arrancar mientras tanto.", "_p_base_BLOQUEA_LA_EJECUCION": {"problema": "El motor cambió a v3: la calibración anterior (v2, p_base 0,1659) no es válida. Recalibrar Apolo y ejecutar congelar_parametros.py.", "candidatos": [], "recomendacion": "Monitoreo → Capa 4 → Calibrar constantes K, y después python congelar_parametros.py"}, "constantes": {"ROS_REFERENCIA_M_H": 100.0, "K_VIENTO": 0.15, "VIENTO_MAX": 6.0, "K_PENDIENTE_ARRIBA": 3.0, "K_PENDIENTE_ABAJO": 1.2, "PENDIENTE_MAX": 4.0, "NDVI_BARRERA": 0.1, "NDVI_SATURACION": 0.45, "HUMEDAD_EXTINCION": 0.25, "K_HUMEDAD": 0.5, "HUMEDAD_SUELO_REF": 0.3, "HR_REFERENCIA": 0.6, "T_REFERENCIA_C": 28.0, "K_TEMPERATURA": 0.01, "K_VPD": 0.04, "ROS_MIN_M_H": 1.0, "T_ESTANCAMIENTO_H": 6.0, "T_MAX_ARDIENDO_H": 72.0, "RUIDO_CELDA": 0.35, "RUIDO_PASO": 0.25, "K_LLUVIA_PROP": 0.2, "K_LLUVIA_EXT": 0.02, "K_BARRERA": 1.0, "SPOTTING_ACTIVO": true, "SPOTTING_PROB": 0.015, "SPOTTING_VIENTO_MIN": 1.0, "SPOTTING_DIST_MIN": 2, "SPOTTING_DIST_MAX": 6, "SPOTTING_DISPERSION": 0.35, "VECINDAD": "moore", "RADIO_VECINDAD": 1, "EXP_DISTANCIA": 1.0}, "genes_calibrables": ["p_base", "K_VIENTO", "K_PENDIENTE_ARRIBA", "K_PENDIENTE_ABAJO", "HUMEDAD_EXTINCION", "K_HUMEDAD", "NDVI_BARRERA", "K_BARRERA", "SPOTTING_PROB"], "ejecucion": {"n_repeticiones": 30, "semillas": "1 a n_repeticiones, consecutivas", "minutos_por_iteracion": 15, "umbral_prob_quemada": 0.5, "_umbral_nota": "Fracción de repeticiones en las que la celda debe arder para contarla como quemada. 0,50 es mayoría simple: neutral entre Recall y Precision. NO moverlo después de ver las métricas."}, "historial": {"v2": "Primera validación con el motor v2 (probabilístico). Detectó comportamiento de umbral (todo o nada), ciclo día/noche inoperante, viento que no frena y tiempos de combustión de 45–75 min. Parámetros v2 (p_base 0,1659, calibración del 17-08-2026) archivados en parametros_ca_congelados_v2.json."}}''')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="../config/parametros_ca_congelados.json")
    ap.add_argument("--desde-json", default=None,
                    help="Leer de un archivo en vez de Supabase, por si se "
                         "guardó el resultado a mano")
    ap.add_argument("--forzar", action="store_true",
                    help="Sobrescribir unos parámetros ya congelados. Pide "
                         "confirmación: rehacerlo a mitad de la validación "
                         "invalida las métricas ya calculadas.")
    ap.add_argument("--completar-con-defecto", action="store_true",
                    help="Si a la calibración le falta algún gen (p. ej. K_BARRERA, "
                         "que no era calibrable cuando se ejecutó), congelarlo con "
                         "el valor FIJO que tenía el motor durante la calibración. "
                         "Queda documentado en el JSON.")
    args = ap.parse_args()

    ruta_cfg = Path(args.config)
    if not ruta_cfg.exists():
        # Sin archivo de parámetros: se crea la plantilla del motor v3 (p_base
        # en null). Se rellena más abajo con la calibración.
        ruta_cfg.parent.mkdir(parents=True, exist_ok=True)
        ruta_cfg.write_text(json.dumps(PLANTILLA_V3, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"(No existía {ruta_cfg}: creada la plantilla del motor v3)")
    cfg = json.loads(ruta_cfg.read_text(encoding="utf-8"))

    if cfg.get("p_base") is not None and not args.forzar:
        print("=" * 74)
        print("YA HAY PARÁMETROS CONGELADOS")
        print("=" * 74)
        print(f"  p_base            : {cfg['p_base']}")
        print(f"  congelados el     : {cfg.get('fecha_congelacion')}")
        print()
        print("Sobrescribirlos a mitad de la validación invalida las métricas")
        print("que ya se hayan calculado con ellos: Apolo y Rurrenabaque")
        print("tienen que compararse con el MISMO conjunto.")
        print()
        print("Si de verdad quieres rehacerlo, añade --forzar y vuelve a")
        print("ejecutar TODA la cadena de validación después.")
        print("=" * 74)
        return 1

    # --- Leer la calibración ------------------------------------------------
    if args.desde_json:
        try:
            cal = json.loads(Path(args.desde_json).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"No se pudo leer {args.desde_json}: {e}")
            print("Tiene que ser un JSON con `p_base` y `constantes`.")
            return 2
    else:
        sys.path.insert(0, str(BACKEND))
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        os.environ.setdefault("SIPRO_OMITIR_ARRANQUE", "1")
        os.environ.setdefault("PRECALENTAR", "false")
        try:
            import django
            django.setup()
            from api.almacen.db import leer_calibracion
            cal = leer_calibracion()
        except Exception as e:  # noqa: BLE001
            print("No se pudo leer la calibración del almacén:")
            print(f"  {type(e).__name__}: {e}")
            print()
            print("Comprueba que backend_django/.env tiene las credenciales de")
            print("Supabase, o pásame el resultado en un archivo con")
            print("  --desde-json ruta.json")
            return 2

    if not cal:
        print("=" * 74)
        print("NO HAY NINGUNA CALIBRACIÓN GUARDADA")
        print("=" * 74)
        print("Ejecútala primero desde la interfaz:")
        print()
        print("  1. Levanta el backend y el frontend")
        print("  2. Entra como analista (el administrador no tiene el permiso)")
        print("  3. Monitoreo → Capa 4 → Calibrar constantes K")
        print()
        print("Mira la consola del backend mientras corre. Tienen que salir")
        print("estas dos líneas:")
        print()
        print("  [calibracion] capa OSM: N barreras duras, M celdas con resistencia")
        print("  [calibracion] DEM: N celdas con altura")
        print()
        print("Si dice «sin capa OSM» o «sin DEM», PARA y reintenta en unos")
        print("minutos: estaría calibrando en un municipio plano y sin ríos,")
        print("que es justo el problema que se corrigió.")
        print("=" * 74)
        return 3

    # --- Trasladar ----------------------------------------------------------
    constantes_cal = cal.get("constantes") or {}
    p_base = cal.get("p_base")

    if p_base is None:
        print("La calibración guardada no trae `p_base`. Revisa su contenido:")
        print(json.dumps(cal, indent=2, ensure_ascii=False)[:600])
        return 4

    print("=" * 74)
    print("CALIBRACIÓN ENCONTRADA")
    print("=" * 74)
    faltan = []
    print(f"{'GEN':>22}{'ANTES':>12}{'CALIBRADO':>14}")
    print("-" * 48)
    for g in GENES:
        antes = cfg["constantes"].get(g) if g != "p_base" else cfg.get("p_base")
        nuevo = p_base if g == "p_base" else constantes_cal.get(g)
        if nuevo is None:
            faltan.append(g)
            print(f"{g:>22}{str(antes):>12}{'— falta':>14}")
        else:
            print(f"{g:>22}{str(antes):>12}{nuevo:>14.4f}"
                  if isinstance(nuevo, float) else
                  f"{g:>22}{str(antes):>12}{str(nuevo):>14}")

    # Metadatos de la calibración: sirven para comprobar que se hizo DESPUÉS
    # de corregir la vecindad a Moore (8 vecinos).
    meta_cal = {k: cal.get(k) for k in ("fecha", "creado", "f1", "iou", "eventos",
                                        "generaciones", "poblacion", "vecindad")
                if cal.get(k) is not None}
    if not meta_cal.get("vecindad"):
        v = (cal.get("constantes") or {}).get("VECINDAD")
        if v:
            meta_cal["vecindad"] = v
    if meta_cal:
        print("-" * 48)
        for k, v in meta_cal.items():
            print(f"{k:>22}  {v}")

    fijados = {}
    modelo_cal = cal.get("modelo") or (cal.get("constantes") or {}).get("MODELO")
    if modelo_cal != "velocidad_v3":
        print("=" * 74)
        print("LA CALIBRACIÓN GUARDADA ES DEL MOTOR ANTERIOR (v2)")
        print("=" * 74)
        print(f"  modelo de la calibración: {modelo_cal or 'v2 (sin marca)'}")
        print("  El motor instalado es v3 (propagación por velocidad). Los genes")
        print("  cambiaron de significado: hay que volver a calibrar Apolo.")
        print("  Monitoreo → Capa 4 → Calibrar constantes K")
        print("=" * 74)
        return 6

    if faltan and args.completar_con_defecto and "p_base" not in faltan:
        # El gen que falta no se optimizó: el optimizador evaluó todas sus
        # combinaciones con el valor por defecto del motor. Congelarlo con ese
        # mismo valor reproduce exactamente lo que se evaluó.
        defecto = {}
        try:
            sys.path.insert(0, str(BACKEND))
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
            os.environ.setdefault("SIPRO_OMITIR_ARRANQUE", "1")
            import django
            django.setup()
            from api.motor.automata import CONSTANTES_POR_DEFECTO as defecto
        except Exception:  # noqa: BLE001
            defecto = {}
        for g in faltan:
            v = defecto.get(g, cfg["constantes"].get(g))
            if v is None:
                break
            constantes_cal[g] = v
            fijados[g] = v
        if len(fijados) == len(faltan):
            print("-" * 48)
            print("\nGenes que la calibración NO optimizó; se congelan con el valor")
            print("fijo del motor durante la calibración (--completar-con-defecto):")
            for g, v in fijados.items():
                print(f"  {g} = {v}")
            faltan = []

    if faltan:
        print("-" * 48)
        print(f"\nFaltan {len(faltan)} gen(es) en la calibración guardada:")
        print(f"  {', '.join(faltan)}")
        print()
        print("Eso pasa si la calibración se ejecutó con una versión anterior,")
        print("antes de que NDVI_BARRERA y K_BARRERA fueran calibrables.")
        print("Vuelve a lanzarla para que los incluya: mezclar el p_base nuevo")
        print("con los valores por defecto de esos dos daría una combinación")
        print("que el optimizador nunca evaluó.")
        print()
        print("Si el gen que falta NO era calibrable cuando se ejecutó, el")
        print("optimizador lo usó fijo en su valor por defecto. Congelarlo con")
        print("ese valor reproduce lo evaluado:")
        print("  python congelar_parametros.py --completar-con-defecto")
        return 5

    # --- Escribir -----------------------------------------------------------
    cfg["p_base"] = round(float(p_base), 4)
    for g in GENES:
        if g == "p_base":
            continue
        v = constantes_cal[g]
        cfg["constantes"][g] = round(float(v), 4)

    cfg["fecha_congelacion"] = date.today().isoformat()
    cfg["origen_parametros"] = (
        "Calibración por evolución diferencial contra perímetros reales de "
        "Apolo (api/motor/calibracion.py), trasladada por "
        "congelar_parametros.py")
    if fijados:
        cfg["genes_no_calibrados"] = {
            "valores": fijados,
            "_nota": "Estos genes no estaban en la calibración guardada: el "
                     "optimizador los mantuvo FIJOS en el valor por defecto del "
                     "motor. Se congelan con ese mismo valor, que es el que se "
                     "evaluó. Declararlo en la memoria.",
        }
    cfg["metadatos_calibracion"] = meta_cal
    cfg["metricas_calibracion_apolo"] = {
        "f1": cal.get("f1"),
        "iou": cal.get("iou"),
        "precision": cal.get("precision"),
        "recall": cal.get("recall"),
        "eventos": cal.get("eventos"),
        "generaciones": cal.get("generaciones"),
        "poblacion": cal.get("poblacion"),
        "fecha_calibracion": cal.get("fecha") or cal.get("creado"),
        "_nota": "Métricas con las que se obtuvieron estos parámetros. Son la "
                 "referencia contra la que hay que leer las de Rurrenabaque: "
                 "si allí salen mucho peores, la diferencia es lo que mide la "
                 "capacidad de generalización.",
    }

    # El bloque que bloqueaba la ejecución ya no aplica.
    if "_p_base_BLOQUEA_LA_EJECUCION" in cfg:
        cfg["_p_base_resuelto"] = {
            "resuelto_el": cfg["fecha_congelacion"],
            "como": "trasladado desde la calibración vigente del backend",
            "_historico": cfg.pop("_p_base_BLOQUEA_LA_EJECUCION"),
        }

    ruta_cfg.write_text(json.dumps(cfg, indent=2, ensure_ascii=False),
                        encoding="utf-8")

    print("-" * 48)
    print(f"\nEscrito: {ruta_cfg}")
    print(f"  p_base            : {cfg['p_base']}")
    print(f"  congelados el     : {cfg['fecha_congelacion']}")
    if cal.get("f1") is not None:
        print(f"  F1 en Apolo       : {cal['f1']}")
    print()
    print("A partir de aquí estos valores NO se tocan. Ni para Apolo ni para")
    print("Rurrenabaque: si se reajustan mirando las métricas de Rurrenabaque,")
    print("deja de ser una validación externa.")
    print()
    print("Siguiente:")
    print("  python f13_validar_apolo.py --repeticiones 30")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
