"""
Exporta a JSON los insumos EXACTOS de la corrida f11 de Rurrenabaque
(clima ERA5 horario, terreno OSM, elevación, focos, parámetros congelados),
para que el backend pueda repetir la validación en el dashboard sin internet
y sin pandas.

    cd validacion_rurrenabaque/python
    python exportar_insumos_rbq.py

Lee ../resultados/insumos_grabados.pkl (generado por grabar_insumos.py),
reproduce f11 con UNA repetición sustituyendo las llamadas externas por las
grabadas, captura lo que recibe el motor y lo escribe en
../datos/insumos_validacion_rbq.json. No toca resultados oficiales.
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import f11_ejecutar_ca_rbq as F11
from grabar_insumos import clave

PKL = Path("../resultados/insumos_grabados.pkl")
SALIDA = Path("../datos/insumos_validacion_rbq.json")


class _Capturado(Exception):
    pass


def main() -> int:
    grab = pickle.loads(PKL.read_bytes())
    F11.cargar_motor()
    from api.servicios import meteo, terreno
    from api.motor import automata

    for mod in (meteo, terreno):
        for n in dir(mod):
            if n.startswith("obtener") and callable(getattr(mod, n)):
                def falso(*a, _n=f"{mod.__name__}.{n}", **k):
                    c = clave(_n, a, k)
                    if c not in grab:
                        raise RuntimeError(f"no grabado: {_n}")
                    return grab[c]
                setattr(mod, n, falso)

    captura = {}

    def espia(grid, params, opciones=None):
        captura["params"] = params
        captura["opciones"] = opciones or {}
        raise _Capturado()

    automata.ejecutar_automata = espia
    sys.argv = ["f11", "--repeticiones", "1", "--resultados", "../resultados/_grabacion"]
    try:
        F11.main()
    except _Capturado:
        pass
    if not captura:
        print("ERROR: f11 no llegó a llamar al motor.")
        return 1

    p, o = captura["params"], captura["opciones"]
    salida = {
        "_documento": "Insumos exactos de f11 (Rurrenabaque E122) para repetir la validación desde el backend.",
        "_generado_por": "validacion_rurrenabaque/python/exportar_insumos_rbq.py",
        "parametros_base": {k: v for k, v in p.items() if k != "semilla"},
        "serie_ambiental": o.get("serie_ambiental"),
        "serie_viento": o.get("serie_viento"),
        "barreras_extra": sorted(o.get("barreras_extra") or []),
        "resistencia_extra": o.get("resistencia_extra") or {},
    }
    SALIDA.write_text(json.dumps(salida, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\nEscrito {SALIDA} ({SALIDA.stat().st_size / 1e3:.0f} kB)")
    print(f"  serie ambiental : {len(salida['serie_ambiental'] or [])} h")
    print(f"  serie viento    : {len(salida['serie_viento'] or [])} h")
    print(f"  barreras        : {len(salida['barreras_extra'])}")
    print(f"  resistencia     : {len(salida['resistencia_extra'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
