"""
Graba los insumos externos (ERA5, OpenStreetMap, DEM) que usan f11 y f13, para
poder reproducir exactamente las mismas corridas sin internet.

    cd validacion_rurrenabaque/python
    python grabar_insumos.py

Qué hace
    1. Envuelve las funciones `obtener_*` de api.servicios.meteo y
       api.servicios.terreno para que guarden (argumentos → resultado).
    2. Ejecuta f13 (Apolo) y f11 (Rurrenabaque) con UNA repetición cada uno,
       solo para que hagan todas sus llamadas externas.
    3. Guarda todo en ../resultados/insumos_grabados.pkl

No modifica nada del backend ni de los resultados de la validación: las
corridas de prueba se escriben en ../resultados/_grabacion/.
"""
from __future__ import annotations

import functools
import hashlib
import json
import pickle
import sys
from pathlib import Path

import f11_ejecutar_ca_rbq as F11

SALIDA = Path("../resultados/insumos_grabados.pkl")
GRAB: dict = {}


def clave(nombre, args, kwargs):
    txt = json.dumps([args, kwargs], default=str, sort_keys=True)
    return f"{nombre}:{hashlib.sha256(txt.encode()).hexdigest()}"


def envolver(mod, nombre):
    orig = getattr(mod, nombre)

    @functools.wraps(orig)
    def f(*a, **k):
        r = orig(*a, **k)
        GRAB[clave(f"{mod.__name__}.{nombre}", a, k)] = r
        GRAB.setdefault("_llamadas", []).append(f"{mod.__name__}.{nombre}")
        return r
    setattr(mod, nombre, f)


def main() -> int:
    F11.cargar_motor()                       # django.setup()
    from api.servicios import meteo, terreno
    for mod in (meteo, terreno):
        for n in dir(mod):
            if n.startswith("obtener") and callable(getattr(mod, n)):
                envolver(mod, n)
                print(f"  grabando {mod.__name__}.{n}")

    tmp = "../resultados/_grabacion"
    import f13_validar_apolo as F13
    print("\n--- f13 (Apolo), 1 repetición ---")
    sys.argv = ["f13", "--repeticiones", "1", "--resultados", tmp]
    F13.main()
    print("\n--- f11 (Rurrenabaque), 1 repetición ---")
    sys.argv = ["f11", "--repeticiones", "1", "--resultados", tmp]
    F11.main()

    SALIDA.write_bytes(pickle.dumps(GRAB))
    print(f"\nLlamadas grabadas: {len(GRAB) - 1}")
    for n in sorted(set(GRAB["_llamadas"])):
        print(f"  {n}: {GRAB['_llamadas'].count(n)}")
    print(f"Escrito: {SALIDA}  ({SALIDA.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
