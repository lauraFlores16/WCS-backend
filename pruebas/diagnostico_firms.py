"""Qué focos de NASA FIRMS está usando SISPRO, campo por campo.

    cd backend_django
    python pruebas/diagnostico_firms.py

Consulta el mismo endpoint que la aplicación, con la misma clave, el mismo
recuadro y el mismo período, e imprime los registros crudos tal como los manda
NASA para poder cotejarlos contra el visor oficial.

    --crudo N     primeros N registros sin procesar (por defecto 10)
    --dias N      sobrescribe el período
    --csv ruta    guarda todo en un CSV
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("SIPRO_OMITIR_ARRANQUE", "1")
os.environ.setdefault("PRECALENTAR", "false")
os.environ.setdefault("SUPABASE_URL", "http://127.0.0.1:1")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "x" * 40)

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

from api.lib.csv_utils import leer_csv  # noqa: E402
from api.servicios import firms  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--crudo", type=int, default=10)
    ap.add_argument("--dias", type=int, default=None)
    ap.add_argument("--zona", default="apolo")
    ap.add_argument("--csv", default=None)
    args = ap.parse_args()

    if args.dias:
        settings.FIRMS["dias"] = args.dias

    f = settings.FIRMS
    clave = f["clave"]

    print("=" * 78)
    print("DIAGNÓSTICO DE NASA FIRMS")
    print("=" * 78)
    print(f"Sensor / fuente : {f['fuente']}")
    print(f"Período         : últimos {f['dias']} día(s)")
    print(f"Recuadro (bbox) : {f['bbox']}")
    print(f"  oeste,sur,este,norte — el mismo que usa la aplicación")
    print(f"Zona            : {args.zona}")
    print(f"Clave MAP_KEY   : {'configurada' if clave.strip() else 'NO CONFIGURADA'}")
    print(f"URL             : {firms.BASE}/<clave>/{f['fuente']}/{f['bbox']}/{f['dias']}")

    if not clave.strip():
        print("\nSin MAP_KEY no se puede consultar. Ponla en backend_django/.env:")
        print("  NASA_FIRMS_MAP_KEY=tu_clave")
        return 1

    # --- 1. Respuesta cruda, sin procesar ---------------------------------
    print("\n" + "-" * 78)
    print("1. RESPUESTA CRUDA DE NASA FIRMS")
    print("-" * 78)
    import requests
    url = f"{firms.BASE}/{clave}/{f['fuente']}/{f['bbox']}/{f['dias']}"
    try:
        r = requests.get(url, timeout=120)
    except Exception as e:  # noqa: BLE001
        print(f"No se pudo consultar: {type(e).__name__}: {e}")
        return 2

    print(f"HTTP {r.status_code} · {len(r.text)} caracteres")
    if r.text.startswith("Invalid"):
        print(f"NASA rechazó la petición: {r.text.strip()[:200]}")
        return 3

    lineas = r.text.strip().splitlines()
    print(f"Cabecera: {lineas[0] if lineas else '(vacía)'}")

    crudos = leer_csv(r.text)
    print(f"\nTOTAL RECIBIDO DESDE NASA FIRMS = {len(crudos)}")

    if not crudos:
        print("\nNASA no devolvió ninguna detección en este período y recuadro.")
        print("Es un resultado válido: significa que no hubo focos activos.")
        print("SISPRO mostrará 0 marcadores. NO se usan datos históricos.")
        return 0

    # --- 2. Los primeros N, campo por campo -------------------------------
    print("\n" + "-" * 78)
    print(f"2. PRIMEROS {min(args.crudo, len(crudos))} REGISTROS, TAL COMO LOS MANDA NASA")
    print("-" * 78)
    for i, c in enumerate(crudos[:args.crudo], 1):
        print(f"\n[{i}]")
        for k in ("latitude", "longitude", "acq_date", "acq_time", "satellite",
                  "instrument", "confidence", "version", "bright_ti4",
                  "bright_ti5", "frp", "daynight", "scan", "track"):
            if k in c:
                print(f"  {k:<12} {c[k]}")
        otros = [k for k in c if k not in
                 ("latitude", "longitude", "acq_date", "acq_time", "satellite",
                  "instrument", "confidence", "version", "bright_ti4",
                  "bright_ti5", "frp", "daynight", "scan", "track")]
        for k in otros:
            print(f"  {k:<12} {c[k]}")

    # --- 3. El filtro municipal -------------------------------------------
    print("\n" + "-" * 78)
    print("3. FILTRO PUNTO-EN-POLÍGONO")
    print("-" * 78)
    poli = firms._poligono(args.zona)
    if poli is None:
        print(f"No se pudo cargar el polígono de {args.zona}. "
              f"Sin él no hay recorte y se mostraría todo el recuadro.")
        dentro = crudos
        fuera = []
    else:
        from shapely.geometry import Point
        dentro, fuera = [], []
        for c in crudos:
            try:
                p = Point(float(c["longitude"]), float(c["latitude"]))
                (dentro if poli.contains(p) else fuera).append(c)
            except (TypeError, ValueError, KeyError):
                fuera.append(c)

    print(f"TOTAL RECIBIDO DESDE NASA FIRMS   = {len(crudos)}")
    print(f"TOTAL DENTRO DEL POLÍGONO DE APOLO = {len(dentro)}")
    print(f"  descartados fuera del municipio  = {len(fuera)}")

    if fuera:
        print(f"\n  Los descartados caen en el recuadro pero fuera del límite:")
        for c in fuera[:5]:
            print(f"    {c.get('latitude')}, {c.get('longitude')}  "
                  f"{c.get('acq_date')} {c.get('acq_time')}")
        if len(fuera) > 5:
            print(f"    … y {len(fuera) - 5} más")

    # --- 4. Lo que la aplicación entrega al mapa --------------------------
    print("\n" + "-" * 78)
    print("4. LO QUE SISPRO ENTREGA AL MAPA")
    print("-" * 78)
    res = firms.obtener_focos_activos(args.zona)
    print(f"estado          : {res['estado']}")
    print(f"activos         : {res['activos']}")
    print(f"TOTAL RENDERIZADO EN EL MAPA = "
          f"{res['activos'] if res['estado'] == 'correcto' else 0}")
    print(f"\nLos tres números deben encadenarse así:")
    print(f"  recibidos {len(crudos)} → dentro del polígono {len(dentro)} "
          f"→ renderizados {res['activos'] if res['estado'] == 'correcto' else 0}")
    coinciden = (len(dentro) == res["activos"])
    print(f"  ¿dentro == renderizados? {'SÍ' if coinciden else 'NO — revisar'}")

    print("\nFocos tal como los recibe el mapa:")
    print(f"{'#':>3} {'LATITUDE':>11} {'LONGITUDE':>11} {'FECHA':>11} {'HORA':>6}"
          f" {'SAT':>5} {'INSTR':>6} {'CONF':>5} {'FRP (MW)':>9}")
    print("-" * 78)
    for i, x in enumerate(res["focos"], 1):
        print(f"{i:>3} {x['lat']:>11} {x['lon']:>11} {x['fecha']:>11} "
              f"{x['hora']:>6} {str(x.get('satelite') or '')[:5]:>5} "
              f"{str(x.get('instrumento') or '')[:6]:>6} "
              f"{str(x.get('confianza') or '')[:5]:>5} {str(x.get('frp')):>9}")

    print("\nEl valor que el mapa muestra como «XX MW» es el campo `frp`,")
    print("tomado literalmente de la columna `frp` del CSV de NASA FIRMS.")
    print("Fire Radiative Power, en megavatios. No se calcula ni se escala.")
    print("La etiqueta solo aparece cuando frp > 20.")

    # --- 5. Qué NO interviene ---------------------------------------------
    print("\n" + "-" * 78)
    print("5. FUENTES QUE NO INTERVIENEN")
    print("-" * 78)
    hist = any(x.get("historico") for x in res["focos"])
    print(f"  focos.csv              : {'INTERVIENE — revisar' if hist else 'no interviene'}")
    print(f"  marcados como histórico: {sum(1 for x in res['focos'] if x.get('historico'))}")
    print(f"  eventos 2021/2023/2024 : otra capa, apagada por defecto")
    print(f"  datos de simulación    : otra capa")
    print(f"  datos mock             : ninguno")
    print(f"  la función de respaldo : {'existe' if hasattr(firms, '_focos_historicos_de_respaldo') else 'eliminada'}")

    # --- 6. Rango temporal -------------------------------------------------
    print("\n" + "-" * 78)
    print("6. RANGO TEMPORAL CONSULTADO")
    print("-" * 78)
    fechas = sorted({str(c.get("acq_date")) for c in crudos if c.get("acq_date")})
    horas = sorted({str(c.get("acq_time")).zfill(4) for c in crudos if c.get("acq_time")})
    print(f"  parámetro de la API : {f['dias']} día(s)")
    print(f"  fechas recibidas    : {fechas[0]} a {fechas[-1]}" if fechas else "  sin fechas")
    print(f"  horas (UTC)         : {horas[0]} a {horas[-1]}" if horas else "")
    print(f"\n  El endpoint de área devuelve los últimos N días COMPLETOS")
    print(f"  disponibles, no una ventana móvil de N × 24 h exactas.")

    if args.csv:
        ruta = Path(args.csv)
        with open(ruta, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(crudos[0]))
            w.writeheader()
            w.writerows(crudos)
        print(f"\nCrudos guardados en {ruta}")

    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
