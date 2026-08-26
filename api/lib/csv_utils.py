"""Lector de CSV mínimo — puerto de backend/lib/csv.js.

No hace falta pandas para leer los dos CSV planos del proyecto (sin comillas
ni saltos de línea dentro de un campo): un `split(",")` por línea basta y es
más rápido de cargar en memoria para 36.390 filas.
"""


def leer_csv(texto: str) -> list[dict]:
    lineas = texto.strip().split("\n")
    lineas = [l.rstrip("\r") for l in lineas]
    cabeceras = lineas[0].split(",")
    filas = []
    for linea in lineas[1:]:
        partes = linea.split(",")
        fila = {}
        for j, cab in enumerate(cabeceras):
            v = partes[j] if j < len(partes) else None
            if v is None or v == "":
                fila[cab] = None
                continue
            try:
                n = float(v)
                fila[cab] = int(n) if n.is_integer() and "." not in v and "e" not in v.lower() else n
            except ValueError:
                fila[cab] = v
        filas.append(fila)
    return filas
