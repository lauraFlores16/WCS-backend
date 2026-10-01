"""
============================================================================
FASE 2 — DESCARGA DE FOCOS NASA FIRMS · RURRENABAQUE · 2023
============================================================================
Ruta: validacion_rurrenabaque/python/f2_descargar_firms_2023.py

QUÉ HACE
    Descarga el año 2023 completo de detecciones FIRMS dentro del bbox de
    Rurrenabaque, recorta al polígono municipal real y deja un CSV limpio
    con fecha, hora, coordenadas, sensor, FRP y confianza.

POR QUÉ ESTE CAMINO Y NO GOOGLE EARTH ENGINE
    En GEE, `ee.ImageCollection("FIRMS")` es un ráster diario de 1 km con la
    banda de temperatura de brillo. Sirve para mapear y para contar píxeles,
    pero NO trae los atributos que esta validación necesita:

        · hora exacta de adquisición  → sin ella no hay secuencia temporal
        · FRP (potencia radiativa)    → es lo que pide el §9 del enunciado
        · sensor y confianza          → filtrado de falsos positivos

    Esos atributos solo están en el archivo tabular de FIRMS. Por eso la
    Fase 2 se hace aquí y los bloques 02/03 de GEE se reservan para la
    cartografía (mapas 2 y 3) y para el contraste con MCD64A1.

LA CLAVE (MAP_KEY)
    Se pide gratis en https://firms.modaps.eosdis.nasa.gov/api/map_key/
    El backend del proyecto ya usa una en `api/servicios/firms.py`, pero la
    usa contra el endpoint de ÚLTIMOS DÍAS. Para 2023 hace falta el archivo
    histórico, que es el mismo endpoint con una fecha de inicio añadida:

        /api/area/csv/{KEY}/{FUENTE}/{BBOX}/{DIAS}/{FECHA_INICIO}

    con DIAS ≤ 10. De ahí que el año se recorra en tramos de 10 días.

FUENTES PARA 2023
    Para fechas pasadas hay que usar las colecciones de procesamiento
    estándar (sufijo _SP), no las de tiempo casi real:

        VIIRS_SNPP_SP    375 m — la principal para este trabajo
        VIIRS_NOAA20_SP  375 m — segunda plataforma VIIRS
        MODIS_SP        1000 m — serie larga, resolución más gruesa

    Se descargan las tres y se marca el sensor de cada detección, porque
    después hay que poder decir en la tesis con qué sensor se identificó el
    evento. VIIRS a 375 m es lo que más se acerca a la celda de 500 m del
    autómata; MODIS a 1 km ya es más grueso que la celda y por eso se usa
    solo como apoyo.

RECORTE AL MUNICIPIO
    FIRMS solo acepta un rectángulo. El bbox de Rurrenabaque contiene
    superficie que NO pertenece al municipio, así que después de descargar
    se hace el recorte punto-en-polígono contra Mun_RBQ. Sin este paso, los
    conteos mensuales incluirían focos de municipios vecinos y el periodo
    crítico podría salir desplazado.

QUÉ GENERA
    ../datos/firms_rbq_2023_bruto.csv      todo lo que devolvió el bbox
    ../datos/firms_rbq_2023.csv            solo lo que cae dentro del municipio
    ../resultados/f2_resumen_descarga.json trazabilidad de la descarga

CÓMO SE EJECUTA
    pip install requests pandas shapely

    # Lo más simple, la clave como argumento:
    python f2_descargar_firms_2023.py --clave TU_CLAVE

    # O por variable de entorno, con la sintaxis de tu consola:
    #   PowerShell:  $env:FIRMS_MAP_KEY = "TU_CLAVE"
    #   CMD:         set FIRMS_MAP_KEY=TU_CLAVE
    #   bash/zsh:    export FIRMS_MAP_KEY=TU_CLAVE
    python f2_descargar_firms_2023.py

ADVERTENCIA DE RIGOR (§42 del enunciado)
    Este script NO inventa datos. Si la clave falta o la API responde vacío,
    aborta y lo dice. Ningún número de la tesis debe salir de otro sitio que
    no sea el CSV que produce esta ejecución.
============================================================================
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
from shapely.geometry import Point, shape
from shapely.prepared import prep

BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

# Del ROI verificado en la Fase 1. Formato que pide FIRMS: oeste,sur,este,norte
BBOX_RBQ = "-67.559663,-15.046462,-67.073719,-14.343298"

# Solo fuentes con sufijo _SP (procesamiento estándar). Las _NRT solo cubren
# los últimos ~90 días, así que para un año pasado devuelven 0 sin dar error.
FUENTES = ["VIIRS_SNPP_SP", "VIIRS_NOAA20_SP", "MODIS_SP"]

# Si las primeras peticiones de una fuente fallan todas con el mismo código, no
# tiene sentido seguir: el problema es la petición, no la red. Repetirlo 37
# veces solo gasta cuota y tiempo.
FALLOS_SEGUIDOS = 3

# Bbox alternativos, por si el evento que interesa cae fuera del municipio.
# El de Rurrenabaque sale del shapefile; los demás son rectángulos amplios
# para explorar antes de decidir.
BBOX_CONOCIDOS = {
    "rurrenabaque": BBOX_RBQ,
    # Rurrenabaque más un margen de ~40 km en cada lado. Útil cuando la
    # actividad térmica que interesa está justo fuera del límite municipal:
    # el fuego no respeta fronteras administrativas.
    "rurrenabaque_ampliado": "-67.95,-15.40,-66.70,-13.95",
    "apolo": "-69.00,-15.00,-68.00,-14.00",
}

# Máximo de días por petición. NASA lo bajó de 10 a 5 en algún momento de 2026:
# pedir 10 devuelve «Invalid day range. Expects [1..5]» con HTTP 400.
# Se deja como variable para no tener que tocar el código si vuelve a cambiar:
#   --tramo N
TRAMO_DIAS = 5
PAUSA_S = 4.0            # entre peticiones. Ver la nota de abajo sobre el límite.
REINTENTOS = 4           # por tramo, con espera creciente
ESPERA_FRENO_S = 70      # cuando FIRMS responde 429 o dice que agotaste la cuota

# URL para consultar cuántas transacciones te quedan. FIRMS limita por clave y
# por ventana de tiempo, y cuando te pasas NO devuelve un error claro: a veces
# responde 429 y a veces un texto plano que parece una respuesta vacía. Mirar
# esto antes de empezar evita descubrirlo 111 peticiones después.
URL_ESTADO = "https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/?MAP_KEY={clave}"


def estado_clave(clave: str) -> dict | None:
    """Transacciones disponibles de la clave. None si no se puede consultar."""
    try:
        r = requests.get(URL_ESTADO.format(clave=clave), timeout=30)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:  # noqa: BLE001
        return None


class FrenoFirms(Exception):
    """FIRMS está limitando: hay que esperar, no darse por vencido."""


def descargar_tramo(clave: str, fuente: str, inicio: date, dias: int,
                    bbox: str = BBOX_RBQ) -> pd.DataFrame:
    """Un tramo. Distingue TRES situaciones que antes se confundían.

    EL FALLO QUE ESTO CORRIGE
        La versión anterior devolvía un DataFrame vacío tanto si no hubo fuego
        como si FIRMS no contestó, y quien llamaba lo apuntaba como «0 focos»
        y seguía. Resultado: una descarga del año 2023 en la que fallaron 108
        de 111 peticiones terminó sin una sola queja y produjo una tabla
        mensual con CEROS en todos los meses. Un año que ardió parecía un año
        sin incendios, y solo se notó al mirar la tabla.

        Ahora hay tres desenlaces distintos y cada uno se trata como debe:

          DataFrame con filas  → hubo detecciones
          DataFrame vacío      → FIRMS contestó y no hubo fuego (dato válido)
          FrenoFirms           → FIRMS está limitando: esperar y reintentar
          RuntimeError         → clave inválida o petición mal formada: parar
    """
    url = f"{BASE}/{clave}/{fuente}/{bbox}/{dias}/{inicio.isoformat()}"
    r = requests.get(url, timeout=120)

    # 429 es el freno explícito. 5xx suele ser sobrecarga pasajera.
    if r.status_code == 429 or r.status_code >= 500:
        raise FrenoFirms(f"HTTP {r.status_code}")
    r.raise_for_status()

    texto = r.text.strip()
    bajo = texto.lower()

    # FIRMS responde 200 con texto plano cuando algo va mal. Hay que leer el
    # CONTENIDO, no fiarse del código de estado.
    if "transaction limit" in bajo or "exceed" in bajo or "too many" in bajo:
        raise FrenoFirms(texto[:160])
    if "invalid" in bajo or "unauthorized" in bajo or "not authorized" in bajo:
        raise RuntimeError(f"FIRMS rechazó la clave: {texto[:200]}")

    # Respuesta legítima sin datos: la cabecera del CSV sola, o cuerpo vacío.
    if not texto:
        raise FrenoFirms("respuesta vacía sin cabecera CSV")
    if "latitude" not in bajo:
        raise FrenoFirms(f"respuesta inesperada: {texto[:160]}")

    df = pd.read_csv(io.StringIO(texto))
    if df.empty:
        return df          # contestó bien: ese tramo no tuvo fuego
    df["fuente_firms"] = fuente
    return df


def descargar_tramo_con_reintentos(clave, fuente, inicio, dias, bbox):
    """Reintenta con espera creciente cuando FIRMS frena."""
    ultimo = None
    for intento in range(1, REINTENTOS + 1):
        try:
            return descargar_tramo(clave, fuente, inicio, dias, bbox)
        except FrenoFirms as e:
            ultimo = e
            espera = ESPERA_FRENO_S * intento
            print(f"      FIRMS frena ({e}). Espera {espera} s "
                  f"[intento {intento}/{REINTENTOS}]")
            time.sleep(espera)
    raise FrenoFirms(f"sigue frenando tras {REINTENTOS} intentos: {ultimo}")


def descargar_rango(clave: str, inicio: date, fin: date,
                    bbox: str = BBOX_RBQ) -> tuple[pd.DataFrame, list[dict]]:
    """Descarga un rango de fechas cualquiera, en tramos de 10 días.

    Sirve tanto para el año completo como para un único día. Ese segundo caso
    es el que permite reproducir como DATO REAL lo que se ve en el visor web de
    FIRMS: se anota la fecha y el recuadro de la pantalla, se piden aquí, y
    salen las coordenadas exactas con su FRP y su sensor.

    Digitalizar a mano los puntos rojos de una captura de pantalla daría
    posiciones aproximadas y sin FRP, y eso ya no serían observaciones: serían
    focos inventados. La API los da exactos y gratis.
    """
    partes, bitacora = [], []
    for fuente in FUENTES:
        print(f"\n--- {fuente} ---")
        cursor = inicio
        n_fuente = 0
        seguidos = 0
        while cursor <= fin:
            dias = min(TRAMO_DIAS, (fin - cursor).days + 1)
            try:
                df = descargar_tramo_con_reintentos(clave, fuente, cursor, dias, bbox)
                n = len(df)
                n_fuente += n
                if n:
                    partes.append(df)
                print(f"  {cursor} +{dias}d → {n:>5} focos")
                seguidos = 0
                bitacora.append({"fuente": fuente, "inicio": cursor.isoformat(),
                                 "dias": dias, "focos": n, "ok": True})
            except RuntimeError as e:
                # Clave inválida: reintentar no arregla nada.
                print(f"\n  {cursor} → {e}")
                print("  Se detiene: con la clave rechazada, seguir solo gasta tiempo.")
                bitacora.append({"fuente": fuente, "inicio": cursor.isoformat(),
                                 "dias": dias, "focos": 0, "ok": False,
                                 "error": str(e), "fatal": True})
                return (pd.concat(partes, ignore_index=True) if partes
                        else pd.DataFrame()), bitacora
            except Exception as e:
                print(f"  {cursor} +{dias}d → FALLO: {e}")
                bitacora.append({"fuente": fuente, "inicio": cursor.isoformat(),
                                 "dias": dias, "focos": 0, "ok": False,
                                 "error": str(e)})
                seguidos += 1
                if seguidos >= FALLOS_SEGUIDOS:
                    print(f"\n  {seguidos} fallos seguidos con {fuente}. Se abandona")
                    print("  esta fuente: el problema es la petición, no la red.")
                    if "400" in str(e):
                        print("\n  El 400 suele significar que la fuente no existe con ese")
                        print("  nombre o que no cubre esas fechas. Para averiguarlo:")
                        print("    python diagnostico_400.py --clave TU_CLAVE")
                    break
            cursor += timedelta(days=dias)
            time.sleep(PAUSA_S)
        print(f"  TOTAL {fuente}: {n_fuente} focos en el bbox")

    if not partes:
        return pd.DataFrame(), bitacora
    return pd.concat(partes, ignore_index=True), bitacora


def recortar_al_municipio(df: pd.DataFrame, geojson: Path) -> pd.DataFrame:
    fc = json.loads(geojson.read_text(encoding="utf-8"))
    geom = shape(fc["features"][0]["geometry"])
    pg = prep(geom)
    dentro = df.apply(
        lambda r: pg.contains(Point(float(r["longitude"]), float(r["latitude"]))),
        axis=1,
    )
    return df[dentro].copy()


def normalizar(df: pd.DataFrame) -> pd.DataFrame:
    """Deja las columnas con nombres estables entre sensores.

    MODIS y VIIRS no traen exactamente los mismos campos: el brillo se llama
    `brightness` en MODIS y `bright_ti4` en VIIRS, y la confianza es numérica
    en MODIS pero categórica (l/n/h) en VIIRS. Se unifica aquí para que el
    análisis de la Fase 3 no tenga que saber de qué sensor viene cada fila.
    """
    df = df.copy()
    if "bright_ti4" in df.columns:
        df["brillo_k"] = df.get("brightness").fillna(df["bright_ti4"]) \
            if "brightness" in df.columns else df["bright_ti4"]
    elif "brightness" in df.columns:
        df["brillo_k"] = df["brightness"]
    else:
        df["brillo_k"] = pd.NA

    df["acq_date"] = pd.to_datetime(df["acq_date"], errors="coerce")
    # acq_time viene como HHMM sin separador (p. ej. 1738 = 17:38 UTC)
    df["acq_time"] = df["acq_time"].astype(str).str.zfill(4)
    df["fecha_hora_utc"] = pd.to_datetime(
        df["acq_date"].dt.strftime("%Y-%m-%d") + " " +
        df["acq_time"].str[:2] + ":" + df["acq_time"].str[2:],
        errors="coerce",
    )
    df["mes"] = df["acq_date"].dt.month
    df["dia_del_anio"] = df["acq_date"].dt.dayofyear
    if "frp" not in df.columns:
        df["frp"] = pd.NA
    return df


def main() -> int:
    global PAUSA_S, FUENTES, TRAMO_DIAS
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clave", default=None,
                    help="Clave MAP_KEY de NASA FIRMS. Alternativa a la "
                         "variable de entorno FIRMS_MAP_KEY, que en PowerShell "
                         "se declara distinto que en Linux y es fuente "
                         "habitual de tropiezos.")
    ap.add_argument("--anio", type=int, default=2023)
    ap.add_argument("--tramo", type=int, default=None,
                    help=f"Días por petición. Por defecto {TRAMO_DIAS}, que es "
                         "el máximo que acepta FIRMS. Si cambia el límite y "
                         "aparece «Invalid day range», ajústalo aquí.")
    ap.add_argument("--fuentes", default=None,
                    help="Sensores separados por coma. Por defecto los tres. "
                         "Con uno solo (p. ej. VIIRS_SNPP_SP) el año entero son "
                         "37 peticiones en vez de 111, y es mucho menos "
                         "probable que FIRMS te frene.")
    ap.add_argument("--pausa", type=float, default=None,
                    help=f"Segundos entre peticiones (por defecto {PAUSA_S}). "
                         "Súbela si te frenan.")
    ap.add_argument("--desde", default=None,
                    help="Fecha inicial AAAA-MM-DD. Con --hasta descarga solo "
                         "ese rango en vez del año entero. Sirve para "
                         "reproducir como dato real lo que se ve en el visor "
                         "web de FIRMS: se anota la fecha de la pantalla y se "
                         "pide aquí.")
    ap.add_argument("--hasta", default=None, help="Fecha final AAAA-MM-DD")
    ap.add_argument("--bbox", default="rurrenabaque",
                    help="Recuadro: 'rurrenabaque' (del shapefile), "
                         "'rurrenabaque_ampliado' (+40 km de margen), 'apolo', "
                         "o cuatro números oeste,sur,este,norte")
    ap.add_argument("--sin-recorte", action="store_true",
                    help="No recortar al polígono municipal. Úsalo cuando el "
                         "evento cae fuera del límite administrativo.")
    ap.add_argument("--sufijo", default=None,
                    help="Sufijo para los archivos de salida, para no "
                         "sobrescribir la descarga del año completo")
    ap.add_argument("--geojson", default="../datos/Mun_RBQ_wgs84.geojson")
    ap.add_argument("--salida", default="../datos")
    ap.add_argument("--resultados", default="../resultados")
    args = ap.parse_args()

    if args.pausa:
        PAUSA_S = args.pausa
    if args.fuentes:
        FUENTES = [f.strip() for f in args.fuentes.split(",") if f.strip()]
    if args.tramo:
        TRAMO_DIAS = max(1, min(args.tramo, 10))

    clave = args.clave or os.environ.get("FIRMS_MAP_KEY")
    if not clave:
        import platform
        print("=" * 74)
        print("FALTA LA CLAVE DE NASA FIRMS")
        print("=" * 74)
        print("Se pide gratis en:")
        print("  https://firms.modaps.eosdis.nasa.gov/api/map_key/")
        print()
        print("Hay dos formas de pasarla. La más simple, como argumento:")
        print()
        print(f"  python {Path(__file__).name} --clave TU_CLAVE "
              f"--desde 2023-11-18 --bbox rurrenabaque_ampliado --sin-recorte")
        print()
        print("O como variable de entorno. OJO: la sintaxis cambia según la")
        print("consola, y es donde más se tropieza:")
        print()
        print("  PowerShell (Windows, la consola azul):")
        print('    $env:FIRMS_MAP_KEY = "TU_CLAVE"')
        print()
        print("  CMD (Windows, la consola negra):")
        print("    set FIRMS_MAP_KEY=TU_CLAVE")
        print()
        print("  bash / zsh (Linux, macOS, Git Bash):")
        print("    export FIRMS_MAP_KEY=TU_CLAVE")
        print()
        if platform.system() == "Windows":
            print("  → Estás en Windows: usa una de las dos primeras.")
            print("    `export` es de Linux y PowerShell no lo reconoce.")
        print()
        print("Sin la clave este script no continúa, y no va a inventarse los")
        print("datos: la validación depende de que estos focos sean los reales.")
        print("=" * 74)
        return 1

    # --- Recuadro ----------------------------------------------------------
    bbox = BBOX_CONOCIDOS.get(args.bbox, args.bbox)
    if bbox.count(",") != 3:
        print(f"ERROR: --bbox debe ser un nombre conocido "
              f"({', '.join(BBOX_CONOCIDOS)}) o cuatro números "
              f"oeste,sur,este,norte. Recibido: {args.bbox}")
        return 1

    # --- Rango de fechas ---------------------------------------------------
    if args.desde:
        inicio = date.fromisoformat(args.desde)
        fin = date.fromisoformat(args.hasta or args.desde)
        etiqueta = f"{inicio} a {fin}"
    else:
        inicio, fin = date(args.anio, 1, 1), date(args.anio, 12, 31)
        etiqueta = str(args.anio)

    # Estado de la clave ANTES de empezar. Si quedan pocas transacciones, más
    # vale saberlo ahora que descubrirlo 111 peticiones después.
    n_peticiones = ((fin - inicio).days // TRAMO_DIAS + 1) * len(FUENTES)
    est = estado_clave(clave)
    if est:
        print(f"Clave FIRMS: {est.get('current_transactions', '?')} de "
              f"{est.get('transaction_limit', '?')} transacciones usadas "
              f"en los últimos {est.get('transaction_interval', '?')}")
        try:
            libres = int(est["transaction_limit"]) - int(est["current_transactions"])
            if libres < n_peticiones:
                print(f"\n  AVISO: esta descarga necesita ~{n_peticiones} "
                      f"peticiones y solo quedan {libres} libres.")
                print("  Opciones: esperar a que se renueve la ventana, o bajar")
                print("  el número de peticiones con --fuentes VIIRS_SNPP_SP")
                print(f"  (serían ~{n_peticiones // max(len(FUENTES),1)} en vez "
                      f"de {n_peticiones}).\n")
        except (KeyError, ValueError, TypeError):
            pass
    else:
        print("Clave FIRMS: no se pudo consultar su estado (se continúa igual)")

    print(f"Descargando FIRMS · {etiqueta}")
    print(f"Sensores: {', '.join(FUENTES)}  ·  ~{n_peticiones} peticiones "
          f"con {PAUSA_S} s de pausa")
    print(f"Recuadro ({args.bbox}): {bbox}")
    if args.desde and inicio == fin:
        print("  Un solo día. Es el modo para reproducir exactamente lo que")
        print("  muestra el visor web de FIRMS, pero con coordenadas y FRP")
        print("  reales en vez de puntos leídos de una imagen.")

    bruto, bitacora = descargar_rango(clave, inicio, fin, bbox)

    # ------------------------------------------------------------------
    # CONTROL DE INTEGRIDAD — lo que faltaba y dejó pasar un año en blanco
    # ------------------------------------------------------------------
    # Una descarga con la mayoría de las peticiones fallidas NO es una
    # descarga: es un archivo incompleto con aspecto de completo. Si se deja
    # pasar, `f3_f4` construye una tabla mensual de ceros y el análisis
    # concluye que no hubo incendios en un año que sí ardió.
    fallidas = [b for b in bitacora if not b["ok"]]
    pct_fallo = len(fallidas) / max(len(bitacora), 1) * 100
    print("\n" + "-" * 62)
    print(f"Peticiones: {len(bitacora)}  ·  correctas: "
          f"{len(bitacora) - len(fallidas)}  ·  fallidas: {len(fallidas)} "
          f"({pct_fallo:.0f} %)")

    if fallidas:
        from collections import Counter
        motivos = Counter(b.get("error", "?")[:70] for b in fallidas)
        print("\nMotivos de los fallos:")
        for m, n in motivos.most_common(5):
            print(f"  {n:>4} × {m}")

    if pct_fallo > 10:
        print("\n" + "=" * 62)
        print("DESCARGA INCOMPLETA — NO SE GUARDA COMO BUENA")
        print("=" * 62)
        print(f"Falló el {pct_fallo:.0f} % de las peticiones. Lo descargado")
        print("NO representa el periodo pedido y usarlo produciría una tabla")
        print("mensual falsa: meses que ardieron aparecerían con cero focos.")
        print()
        print("Qué hacer, por orden:")
        print()
        print("  1. Bajar el número de peticiones usando un solo sensor:")
        print(f"     python {Path(__file__).name} --clave TU_CLAVE "
              f"--fuentes VIIRS_SNPP_SP")
        print("     VIIRS S-NPP tiene 375 m de resolución y cubre todo 2023.")
        print()
        print("  2. Aumentar la pausa entre peticiones:")
        print(f"     python {Path(__file__).name} --clave TU_CLAVE --pausa 10")
        print()
        print("  3. Esperar a que se renueve la ventana de transacciones de")
        print("     FIRMS (suele ser de 10 minutos) y reintentar.")
        print()
        print("  4. Bajar el año por trozos, un mes cada vez:")
        print(f"     python {Path(__file__).name} --clave TU_CLAVE "
              f"--desde 2023-08-01 --hasta 2023-08-31 --sufijo ago")
        print()
        print("El detalle de cada petición está en")
        print("  ../resultados/f2_resumen_descarga.json")
        print("=" * 62)
        res = Path(args.resultados)
        res.mkdir(parents=True, exist_ok=True)
        (res / "f2_resumen_descarga.json").write_text(json.dumps({
            "estado": "INCOMPLETA", "rango": etiqueta, "bbox": bbox,
            "peticiones": bitacora, "pct_fallo": round(pct_fallo, 1),
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        return 3

    if bruto.empty:
        print("\nFIRMS contestó a todo pero no hubo ninguna detección en el")
        print("periodo y el recuadro pedidos. Es un resultado válido, no un error.")
        return 2

    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    suf = args.sufijo or (f"{inicio}_{fin}" if args.desde else str(args.anio))
    ruta_bruto = salida / f"firms_rbq_{suf}_bruto.csv"
    bruto.to_csv(ruta_bruto, index=False)

    print(f"\nFocos en el recuadro    : {len(bruto):,}")
    if args.sin_recorte:
        dentro = bruto.copy()
        print("Sin recortar al municipio (--sin-recorte): se conservan todos.")
        print("  Ojo: si estos focos caen fuera de Rurrenabaque, NO sirven")
        print("  para su validación. El grid, la cicatriz dNBR y las métricas")
        print("  están definidos sobre el municipio.")
    else:
        dentro = recortar_al_municipio(bruto, Path(args.geojson))
        pct = len(dentro)/len(bruto)*100 if len(bruto) else 0
        print(f"Focos dentro del municipio: {len(dentro):,} ({pct:.1f} % del recuadro)")
        if len(bruto) and len(dentro) == 0:
            print("\n  ATENCIÓN: ninguno de los focos cae dentro de Rurrenabaque.")
            print("  Lo que estás mirando ocurrió en otro municipio. Comprueba")
            print("  las coordenadas contra datos/rbq_roi.json antes de seguir.")

    dentro = normalizar(dentro)
    ruta_final = salida / f"firms_rbq_{suf}.csv"
    dentro.to_csv(ruta_final, index=False)

    res = Path(args.resultados)
    res.mkdir(parents=True, exist_ok=True)
    (res / "f2_resumen_descarga.json").write_text(json.dumps({
        "rango": etiqueta,
        "bbox_nombre": args.bbox,
        "bbox": bbox,
        "recortado_al_municipio": not args.sin_recorte,
        "fuentes": FUENTES,
        "focos_en_bbox": int(len(bruto)),
        "focos_en_municipio": int(len(dentro)),
        "por_sensor": dentro["fuente_firms"].value_counts().to_dict(),
        "peticiones": bitacora,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nEscrito: {ruta_bruto}")
    print(f"Escrito: {ruta_final}")
    print(f"Escrito: {res / 'f2_resumen_descarga.json'}")
    print("\nSiguiente: python f3_f4_analisis_y_evento.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
