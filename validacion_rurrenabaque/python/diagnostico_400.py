"""Por qué NASA FIRMS devuelve 400. Se ejecuta en tu máquina.

    cd validacion_rurrenabaque/python
    python diagnostico_400.py --clave TU_CLAVE

Pregunta a FIRMS qué fuentes existen y qué fechas cubre cada una, y después
prueba variaciones de la petición para aislar qué parte la rechaza.
"""
from __future__ import annotations

import argparse
import sys

BASE = "https://firms.modaps.eosdis.nasa.gov/api"
BBOX = "-67.559663,-15.046462,-67.073719,-14.343298"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clave", required=True)
    ap.add_argument("--bbox", default=BBOX)
    ap.add_argument("--fecha", default="2023-11-15")
    args = ap.parse_args()

    try:
        import requests
    except ImportError:
        print("Falta requests:  pip install requests")
        return 2

    K = args.clave
    oculta = lambda u: u.replace(K, "<clave>")

    print("=" * 74)
    print("DIAGNÓSTICO DEL ERROR 400 DE NASA FIRMS")
    print("=" * 74)

    # --- 1. La clave ------------------------------------------------------
    print("\n1. ESTADO DE LA CLAVE")
    u = f"{BASE}/../mapserver/mapkey_status/?MAP_KEY={K}"
    u = f"https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/?MAP_KEY={K}"
    try:
        r = requests.get(u, timeout=30)
        print(f"   HTTP {r.status_code} · {r.text.strip()[:180]}")
    except Exception as e:
        print(f"   No se pudo consultar: {type(e).__name__}")

    # --- 2. Qué fuentes existen y qué fechas cubren -----------------------
    print("\n2. FUENTES DISPONIBLES Y SU COBERTURA TEMPORAL")
    print("   Esto es lo que decide si una petición es válida.")
    u = f"{BASE}/data_availability/csv/{K}/all"
    try:
        r = requests.get(u, timeout=60)
        if r.status_code != 200:
            print(f"   HTTP {r.status_code} · {r.text.strip()[:200]}")
        else:
            lineas = [l for l in r.text.strip().splitlines() if l.strip()]
            print(f"   {lineas[0]}")
            print("   " + "-" * 62)
            fecha = args.fecha
            validas = []
            for l in lineas[1:]:
                partes = [p.strip() for p in l.split(",")]
                if len(partes) < 3:
                    continue
                fuente, d0, d1 = partes[0], partes[1], partes[2]
                cubre = d0 <= fecha <= d1
                marca = "SÍ" if cubre else "no"
                print(f"   {fuente:<26} {d0} a {d1}   ¿cubre {fecha}? {marca}")
                if cubre:
                    validas.append(fuente)
            print("   " + "-" * 62)
            if validas:
                print(f"\n   Fuentes que SÍ cubren {fecha}:")
                for v in validas:
                    print(f"     {v}")
            else:
                print(f"\n   NINGUNA fuente cubre {fecha}. Ahí está el 400.")
    except Exception as e:
        print(f"   No se pudo consultar: {type(e).__name__}: {e}")
        validas = []

    # --- 3. Probar variaciones -------------------------------------------
    print("\n3. PRUEBAS DE LA PETICIÓN")
    print("   Se cambia una cosa cada vez para ver cuál la rechaza.\n")

    candidatas = ["VIIRS_SNPP_SP", "VIIRS_SNPP_NRT", "VIIRS_NOAA20_SP",
                  "VIIRS_NOAA20_NRT", "MODIS_SP", "MODIS_NRT"]
    pruebas = []
    for f in candidatas:
        pruebas.append((f"fuente {f}", f"{BASE}/area/csv/{K}/{f}/{args.bbox}/1/{args.fecha}"))
    pruebas += [
        ("bbox con 3 decimales",
         f"{BASE}/area/csv/{K}/VIIRS_SNPP_NRT/-67.560,-15.046,-67.074,-14.343/1/{args.fecha}"),
        ("sin fecha (últimas 24 h)",
         f"{BASE}/area/csv/{K}/VIIRS_SNPP_NRT/{args.bbox}/1"),
        ("rango de 10 días",
         f"{BASE}/area/csv/{K}/VIIRS_SNPP_NRT/{args.bbox}/10/{args.fecha}"),
        ("área por país (Bolivia)",
         f"{BASE}/country/csv/{K}/VIIRS_SNPP_NRT/BOL/1/{args.fecha}"),
    ]

    funcionan = []
    for nombre, url in pruebas:
        try:
            r = requests.get(url, timeout=45)
            n = len([l for l in r.text.strip().splitlines() if l.strip()]) - 1
            if r.status_code == 200 and not r.text.startswith("Invalid"):
                # HTTP 200 con 0 detecciones no significa que la fuente sirva:
                # las _NRT responden bien pero no tienen histórico.
                util = max(n, 0) > 0
                print(f"   {'OK  ' if util else 'vacía'} {nombre:<28} "
                      f"{max(n, 0)} detecciones")
                if util:
                    funcionan.append((nombre, url))
            else:
                cuerpo = r.text.strip()[:90].replace("\n", " ")
                print(f"   {r.status_code}  {nombre:<28} {cuerpo}")
        except Exception as e:
            print(f"   ---  {nombre:<28} {type(e).__name__}")

    # --- 4. Conclusión ----------------------------------------------------
    print("\n" + "=" * 74)
    print("CONCLUSIÓN")
    print("=" * 74)
    if not funcionan:
        print("  Ninguna variación funcionó. Pega esta salida completa y seguimos.")
    else:
        print(f"  {len(funcionan)} variación(es) funcionan:")
        for n, u in funcionan:
            print(f"    · {n}")
        fuentes_ok = [n.replace("fuente ", "") for n, _ in funcionan
                      if n.startswith("fuente ")]
        if fuentes_ok:
            print(f"\n  Fuentes con datos para esa fecha: {', '.join(fuentes_ok)}")
            print(f"\n  Relanza la descarga con:")
            print(f"    python f2_descargar_firms_2023.py --clave TU_CLAVE \\")
            print(f"        --bbox rurrenabaque --fuentes {fuentes_ok[0]} --anio 2023")
            if len(fuentes_ok) > 1:
                print(f"\n  Con una sola fuente el año son ~74 peticiones. Añadir")
                print(f"  las demás multiplica el tiempo y aporta detecciones")
                print(f"  redundantes del mismo incendio.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
