"""
============================================================================
FASES 3, 4 y 5 — PERIODO CRÍTICO, EVENTO E IDENTIFICACIÓN DE FOCOS INICIALES
============================================================================
Ruta: validacion_rurrenabaque/python/f3_f4_analisis_y_evento.py

QUÉ HACE
    Sobre el CSV que dejó la Fase 2:

      FASE 3  tabla y gráfico de focos por mes → periodo crítico
      FASE 4  agrupamiento espaciotemporal → eventos coherentes candidatos
      FASE 5  primeras detecciones de cada evento → focos iniciales del CA

POR QUÉ NO BASTA CON «EL MES CON MÁS FOCOS»
    El §9 del enunciado lo dice explícitamente: no se puede asumir que todos
    los focos de un mes pertenecen al mismo incendio. En la temporada seca
    del Beni hay decenas de quemas agrícolas simultáneas repartidas por todo
    el municipio. Tomar «septiembre entero» como un evento produciría una
    cicatriz observada que en realidad son veinte cicatrices distintas, y el
    autómata —que arranca de un foco— nunca podría reproducirla. Las métricas
    saldrían pésimas por un error de definición, no por un fallo del modelo.

CÓMO SE SEPARAN LOS EVENTOS
    Agrupamiento por densidad (DBSCAN) en un espacio de TRES dimensiones:
    dos espaciales y una temporal. La distancia temporal se convierte a
    metros con un factor de equivalencia explícito, para poder usar una sola
    métrica euclídea:

        eps_espacial   1.500 m   ~3 celdas del grid de 500 m
        eps_temporal      48 h   dos pasadas satelitales de margen
        min_muestras       5     descarta detecciones sueltas

    Los tres son ARGUMENTOS, no constantes escondidas: hay que poder
    justificarlos en la tesis y hay que poder enseñar cómo cambia el
    resultado si se mueven. El script incluye un análisis de sensibilidad
    (--sensibilidad) precisamente para eso.

    Por qué 1.500 m: un frente de copas avanza como mucho unos cientos de
    metros por hora en esta vegetación; dos detecciones a más de 1,5 km y
    dentro de la misma pasada son, casi con seguridad, focos distintos.

    Por qué 48 h: VIIRS pasa dos veces al día, pero la nubosidad del Beni en
    temporada de quemas deja huecos. 48 h tolera un día ciego sin partir un
    incendio en dos.

QUÉ CRITERIO ELIGE EL EVENTO
    NO se elige «el que mejor le venga al modelo» (§15 y §42). Se ordena por
    un criterio declarado antes de mirar nada:

        1º  número de detecciones del grupo
        2º  extensión espacial (envolvente convexa)
        3º  duración

    y se propone el primero. El script imprime los cinco mejores para que la
    elección quede documentada y sea discutible, no un dedazo.

QUÉ GENERA
    ../resultados/f3_focos_por_mes.csv
    ../resultados/f3_focos_por_mes.png
    ../resultados/f3_focos_por_dia_periodo_critico.png
    ../resultados/eventos_candidatos.csv
    ../resultados/evento_validacion.json     ← alimenta a las Fases 6 y 11
    ../resultados/focos_iniciales.csv          ← focos de arranque del CA
    ../resultados/f4_evento_mapa.png

CÓMO SE EJECUTA
    pip install pandas numpy scikit-learn matplotlib shapely
    python f3_f4_analisis_y_evento.py
    python f3_f4_analisis_y_evento.py --sensibilidad     # tabla de sensibilidad
============================================================================
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN

MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
         "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]

# Grados → metros a la latitud de Rurrenabaque (-14,69). Se fija aquí y no se
# recalcula por punto: en 78 km de extensión N-S el error del coseno es
# despreciable frente al eps de 1.500 m.
LAT_REF = -14.688621
M_POR_GRADO_LAT = 110_574.0
M_POR_GRADO_LON = 111_320.0 * math.cos(math.radians(LAT_REF))


# ---------------------------------------------------------------------------
# FASE 3
# ---------------------------------------------------------------------------
def fase3_mensual(df: pd.DataFrame, res: Path) -> pd.DataFrame:
    print("=" * 74)
    print("FASE 3 — ACTIVIDAD MENSUAL")
    print("=" * 74)

    tabla = (df.groupby("mes")
               .agg(focos=("latitude", "size"),
                    frp_medio=("frp", "mean"),
                    frp_max=("frp", "max"),
                    dias_con_actividad=("acq_date", lambda s: s.dt.date.nunique()))
               .reindex(range(1, 13), fill_value=0)
               .reset_index())
    tabla["mes_nombre"] = [MESES[m - 1] for m in tabla["mes"]]
    tabla = tabla[["mes", "mes_nombre", "focos", "dias_con_actividad",
                   "frp_medio", "frp_max"]]

    print(f"\n{'MES':<6}{'FOCOS':>8}{'DÍAS ACT.':>11}{'FRP MEDIO':>12}{'FRP MÁX':>10}")
    print("-" * 47)
    for _, r in tabla.iterrows():
        fm = f"{r.frp_medio:.1f}" if pd.notna(r.frp_medio) else "—"
        fx = f"{r.frp_max:.1f}" if pd.notna(r.frp_max) else "—"
        print(f"{r.mes_nombre:<6}{int(r.focos):>8}{int(r.dias_con_actividad):>11}"
              f"{fm:>12}{fx:>10}")
    print("-" * 47)
    print(f"{'TOTAL':<6}{int(tabla.focos.sum()):>8}")

    tabla.to_csv(res / "f3_focos_por_mes.csv", index=False)

    # Gráfico. El mes pico se resalta porque es la conclusión de la fase.
    fig, ax = plt.subplots(figsize=(9, 4.2))
    pico = int(tabla.focos.idxmax())
    colores = ["#B45309" if i == pico else "#94A3B8" for i in range(12)]
    ax.bar(tabla.mes_nombre, tabla.focos, color=colores)
    for i, v in enumerate(tabla.focos):
        if v:
            ax.text(i, v, f"{int(v)}", ha="center", va="bottom", fontsize=8)
    ax.set_ylabel("Focos FIRMS")
    ax.set_title("Focos FIRMS por mes — Municipio de Rurrenabaque, 2023\n"
                 "VIIRS SNPP/NOAA-20 y MODIS, recortados al límite municipal",
                 fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(res / "f3_focos_por_mes.png", dpi=150)
    plt.close(fig)

    criticos = tabla.nlargest(3, "focos")["mes_nombre"].tolist()
    print(f"\nMeses con más actividad: {', '.join(criticos)}")
    print("→ El periodo crítico se analiza a nivel diario a continuación.")
    return tabla


def fase3_diario(df: pd.DataFrame, meses: list[int], res: Path) -> None:
    sub = df[df["mes"].isin(meses)]
    if sub.empty:
        print("Sin actividad en los meses críticos.")
        return
    diario = sub.groupby(sub["acq_date"].dt.date).size()

    fig, ax = plt.subplots(figsize=(11, 3.6))
    ax.bar(diario.index, diario.values, color="#B45309", width=0.9)
    ax.set_ylabel("Focos / día")
    ax.set_title("Focos FIRMS por día — periodo crítico, Rurrenabaque 2023",
                 fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(res / "f3_focos_por_dia_periodo_critico.png", dpi=150)
    plt.close(fig)

    print(f"\nDía con más detecciones: {diario.idxmax()} ({diario.max()} focos)")


# ---------------------------------------------------------------------------
# FASE 4
# ---------------------------------------------------------------------------
def fase4_eventos(df: pd.DataFrame, res: Path, eps_m: float, eps_h: float,
                  min_muestras: int, guardar: bool = True) -> pd.DataFrame:
    """Agrupa las detecciones en eventos coherentes.

    La tercera dimensión (tiempo) se escala a metros con `eps_m/eps_h`, de
    modo que una hora «pesa» lo mismo en la métrica euclídea que
    eps_m/eps_h metros. Así un solo eps espacial gobierna las tres.
    """
    if guardar:
        print("\n" + "=" * 74)
        print("FASE 4 — IDENTIFICACIÓN DE EVENTOS")
        print("=" * 74)
        print(f"DBSCAN  eps_espacial={eps_m:.0f} m · eps_temporal={eps_h:.0f} h "
              f"· min_muestras={min_muestras}")

    d = df.dropna(subset=["fecha_hora_utc"]).copy()
    t0 = d["fecha_hora_utc"].min()
    horas = (d["fecha_hora_utc"] - t0).dt.total_seconds() / 3600.0

    X = np.column_stack([
        d["longitude"].astype(float) * M_POR_GRADO_LON,
        d["latitude"].astype(float) * M_POR_GRADO_LAT,
        horas * (eps_m / eps_h),
    ])

    d["evento"] = DBSCAN(eps=eps_m, min_samples=min_muestras).fit_predict(X)

    filas = []
    for eid, g in d[d["evento"] >= 0].groupby("evento"):
        lat, lon = g["latitude"].astype(float), g["longitude"].astype(float)
        # Extensión: diagonal de la envolvente del grupo, en km
        ancho = (lon.max() - lon.min()) * M_POR_GRADO_LON / 1000
        alto = (lat.max() - lat.min()) * M_POR_GRADO_LAT / 1000
        dur_h = (g["fecha_hora_utc"].max() - g["fecha_hora_utc"].min()).total_seconds() / 3600

        # --- CONTINUIDAD TEMPORAL ---------------------------------------
        # Fracción de días del intervalo con al menos una detección. Un valor
        # alto significa un incendio que arde de forma sostenida; uno bajo,
        # detecciones sueltas que el agrupamiento juntó por proximidad pero
        # que probablemente son quemas distintas en el mismo paraje.
        dias = g["fecha_hora_utc"].dt.date
        dias_span = max((g["fecha_hora_utc"].max().date()
                         - g["fecha_hora_utc"].min().date()).days + 1, 1)
        continuidad_temporal = dias.nunique() / dias_span

        # --- CONTINUIDAD ESPACIAL ---------------------------------------
        # Cuántas celdas de 500 m distintas tocó el evento frente al área de
        # su envolvente. Cerca de 1 = mancha compacta; cerca de 0 = puntos
        # dispersos por una zona grande, que el autómata no podrá reproducir
        # desde un foco.
        celdas = {(round(la / 0.0045), round(lo / 0.0045))
                  for la, lo in zip(lat, lon)}
        area_envolvente = max(ancho * alto, 0.25)
        continuidad_espacial = min(len(celdas) * 0.25 / area_envolvente, 1.0)

        filas.append({
            "evento": int(eid),
            "evento_id": f"E{int(eid) + 1:02d}",
            "focos": len(g),
            "fecha_inicio": g["fecha_hora_utc"].min(),
            "fecha_fin": g["fecha_hora_utc"].max(),
            "inicio_utc": g["fecha_hora_utc"].min(),
            "fin_utc": g["fecha_hora_utc"].max(),
            "duracion_h": round(dur_h, 1),
            "duracion_dias": round(dur_h / 24, 2),
            "lat_centro": round(lat.mean(), 6),
            "lon_centro": round(lon.mean(), 6),
            "extension_km": round(math.hypot(ancho, alto), 2),
            "celdas_500m_tocadas": len(celdas),
            "continuidad_temporal": round(continuidad_temporal, 3),
            "continuidad_espacial": round(continuidad_espacial, 3),
            "frp_promedio": round(float(g["frp"].mean()), 1) if g["frp"].notna().any() else None,
            "frp_maximo": round(float(g["frp"].max()), 1) if g["frp"].notna().any() else None,
            "frp_total": round(float(g["frp"].sum()), 1) if g["frp"].notna().any() else None,
            "sensores": ",".join(sorted(g["fuente_firms"].unique())),
            "sensor_principal": g["fuente_firms"].mode().iloc[0] if not g["fuente_firms"].mode().empty else None,
            "seleccionado": 0,
        })

    ev = pd.DataFrame(filas)
    if ev.empty:
        if guardar:
            print("\nNo se formó ningún grupo con estos parámetros.")
            print("Prueba a relajar --eps-m o a bajar --min-muestras, y documenta")
            print("el cambio: los parámetros del agrupamiento son parte del método.")
        return ev

    # ORDEN PROPUESTO, no elección automática. El §11 lo pide explícitamente:
    # el candidato final se decide DESPUÉS de mirar continuidad, concentración,
    # duración y si hay imágenes pre/post aprovechables. Este orden solo
    # propone; `--evento E0n` fija el que se elija.
    ev = ev.sort_values(["focos", "continuidad_espacial", "continuidad_temporal",
                         "extension_km"], ascending=False).reset_index(drop=True)

    if guardar:
        sueltos = int((d["evento"] < 0).sum())
        print(f"\nDetecciones agrupadas : {len(d) - sueltos:,}")
        print(f"Detecciones sueltas   : {sueltos:,} (ruido, se descartan)")
        print(f"Eventos identificados : {len(ev)}")
        print("\nCandidatos (los diez mayores):")
        print(f"{'ID':>5}{'FOCOS':>7}{'INICIO':>18}{'DUR.h':>7}"
              f"{'EXT.km':>8}{'C.TEMP':>8}{'C.ESP':>7}{'FRP med':>9}{'FRP máx':>9}")
        print("-" * 78)
        for _, r in ev.head(10).iterrows():
            fm = f"{r.frp_promedio:.0f}" if r.frp_promedio else "—"
            fx = f"{r.frp_maximo:.0f}" if r.frp_maximo else "—"
            print(f"{r.evento_id:>5}{int(r.focos):>7}"
                  f"{r.inicio_utc.strftime('%Y-%m-%d %H:%M'):>18}"
                  f"{r.duracion_h:>7.0f}{r.extension_km:>8.1f}"
                  f"{r.continuidad_temporal:>8.2f}{r.continuidad_espacial:>7.2f}"
                  f"{fm:>9}{fx:>9}")
        print("-" * 78)
        print("C.TEMP = fracción de días del intervalo con detección.")
        print("C.ESP  = compacidad: celdas de 500 m tocadas / área envolvente.")
        print("Cerca de 1 en ambas = un incendio sostenido y compacto, que es")
        print("lo que el autómata puede reproducir desde un foco. Valores bajos")
        print("indican quemas dispersas que el agrupamiento juntó por cercanía.")
        ev.to_csv(res / "eventos_candidatos.csv", index=False)
        # Cada detección con su evento: lo necesitan f6 (huella del evento
        # sobre la cicatriz) y f7 (curva de crecimiento de un evento concreto).
        et = d.copy()
        et["evento_id"] = np.where(et["evento"] >= 0,
                                   "E" + (et["evento"] + 1).astype(str).str.zfill(2), "")
        et["evento"] = et["evento_id"]
        et.drop(columns=["evento_id"]).to_csv(
            res / "firms_eventos_etiquetados.csv", index=False)
        print(f"Escrito: {res / 'firms_eventos_etiquetados.csv'} "
              f"(columna `evento` = E01, E02…; vacía = ruido)")

    return ev, d


def fase4_seleccionar(ev: pd.DataFrame, d: pd.DataFrame, res: Path,
                      eps_m: float, eps_h: float, min_muestras: int,
                      elegido: str | None = None) -> dict:
    # Si no se especifica, se PROPONE el primero del orden, pero el archivo
    # deja constancia de que la elección es revisable. Con --evento E03 se fija
    # otro después de mirar la tabla de candidatos y las imágenes disponibles.
    if elegido:
        fila = ev[ev["evento_id"] == elegido]
        if fila.empty:
            raise SystemExit(f"No existe el evento {elegido}. Mira "
                             f"eventos_candidatos.csv.")
        mejor = fila.iloc[0]
        criterio = (f"Elegido a mano ({elegido}) tras revisar continuidad, "
                    f"concentración, duración y disponibilidad de imágenes "
                    f"pre/post.")
    else:
        mejor = ev.iloc[0]
        criterio = ("Propuesto automáticamente por número de detecciones, con "
                    "desempate por continuidad espacial, continuidad temporal "
                    "y extensión. PENDIENTE de confirmar mirando las imágenes "
                    "pre/post disponibles (§11).")
    eid = int(mejor.evento)
    ev.loc[ev["evento"] == eid, "seleccionado"] = 1
    ev.to_csv(res / "eventos_candidatos.csv", index=False)
    focos = d[d["evento"] == eid].sort_values("fecha_hora_utc")

    sel = {
        "municipio": "Rurrenabaque",
        "anio": int(mejor.inicio_utc.year),
        "evento_id": mejor.evento_id,
        "fecha_inicio": mejor.inicio_utc.strftime("%Y-%m-%d"),
        "fecha_fin": mejor.fin_utc.strftime("%Y-%m-%d"),
        "numero_focos": int(mejor.focos),
        "sensor_principal": mejor.sensor_principal,
        "criterio_seleccion": criterio,
        "_fase": "FASE 4 — evento seleccionado",
        "continuidad_temporal": float(mejor.continuidad_temporal),
        "continuidad_espacial": float(mejor.continuidad_espacial),
        "frp_promedio_mw": mejor.frp_promedio,
        "frp_maximo_mw": mejor.frp_maximo,
        "parametros_agrupamiento": {
            "algoritmo": "DBSCAN espaciotemporal",
            "eps_espacial_m": eps_m,
            "eps_temporal_h": eps_h,
            "min_muestras": min_muestras,
        },
        "evento_indice_dbscan": eid,
        "focos": int(mejor.focos),
        "inicio_utc": mejor.inicio_utc.isoformat(),
        "fin_utc": mejor.fin_utc.isoformat(),
        "duracion_h": float(mejor.duracion_h),
        "centro": {"lat": float(mejor.lat_centro), "lon": float(mejor.lon_centro)},
        "extension_km": float(mejor.extension_km),
        "frp_total_mw": mejor.frp_total,
        "frp_max_mw": mejor.frp_maximo,
        "sensores": mejor.sensores,
        "ventana_imagenes_sugerida": {
            "_nota": "Puntos de partida para la Fase 6. La fecha definitiva "
                     "depende de qué escena esté libre de nubes; hay que "
                     "elegirla mirando las imágenes, no aquí.",
            "pre_hasta": (mejor.inicio_utc - pd.Timedelta(days=1)).date().isoformat(),
            "pre_desde": (mejor.inicio_utc - pd.Timedelta(days=45)).date().isoformat(),
            "post_desde": (mejor.fin_utc + pd.Timedelta(days=5)).date().isoformat(),
            "post_hasta": (mejor.fin_utc + pd.Timedelta(days=60)).date().isoformat(),
        },
    }
    (res / "evento_validacion.json").write_text(
        json.dumps(sel, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 74)
    print(f"EVENTO SELECCIONADO — {mejor.evento_id}  (índice DBSCAN {eid})")
    print("=" * 74)
    print(f"  Focos       : {int(mejor.focos)}")
    print(f"  Inicio (UTC): {mejor.inicio_utc}")
    print(f"  Fin    (UTC): {mejor.fin_utc}")
    print(f"  Duración    : {mejor.duracion_h:.1f} h ({mejor.duracion_h/24:.1f} días)")
    print(f"  Centro      : {mejor.lat_centro:.6f}, {mejor.lon_centro:.6f}")
    print(f"  Extensión   : {mejor.extension_km:.2f} km")
    print(f"  Sensores    : {mejor.sensores}")

    # --- FASE 5: focos iniciales ------------------------------------------
    # Se toman las detecciones de la PRIMERA pasada del evento, no un punto
    # elegido a mano (§15). Si esa primera pasada trae varios focos separados,
    # se documentan todos y el CA arranca con todos: encajar el arranque para
    # que la simulación quede bonita invalidaría la prueba.
    t_ini = focos["fecha_hora_utc"].min()
    iniciales = focos[focos["fecha_hora_utc"] <= t_ini + pd.Timedelta(hours=6)]
    def con_esquema(df_):
        """Columnas del §14. `grid_id`, `fila` y `columna` los rellena
        f11_ejecutar_ca_rbq.py al asignar cada foco a su celda: aquí todavía
        no se conoce el grid."""
        out = pd.DataFrame({
            "fecha": df_["fecha_hora_utc"].dt.strftime("%Y-%m-%d"),
            "hora": df_["fecha_hora_utc"].dt.strftime("%H:%M"),
            "lat": df_["latitude"].astype(float),
            "lon": df_["longitude"].astype(float),
            "frp": df_["frp"] if "frp" in df_.columns else None,
            "sensor": df_["fuente_firms"],
        })
        if "confidence" in df_.columns:
            out["confianza"] = df_["confidence"]
        out["grid_id"] = ""
        out["fila"] = ""
        out["columna"] = ""
        return out

    con_esquema(iniciales).to_csv(res / "focos_iniciales.csv", index=False)
    con_esquema(focos).to_csv(res / "focos_del_evento.csv", index=False)

    print("\n" + "=" * 74)
    print("FASE 5 — FOCOS INICIALES DEL AUTÓMATA")
    print("=" * 74)
    print(f"Primera detección: {t_ini}")
    print(f"Focos en las primeras 6 h: {len(iniciales)}")
    for _, r in iniciales.iterrows():
        frp = f" · FRP {r.frp} MW" if pd.notna(r.get("frp")) else ""
        print(f"  {r.latitude:.5f}, {r.longitude:.5f}  {r.fecha_hora_utc}{frp}")
    if len(iniciales) > 1:
        print("\nHay más de un foco inicial. Se documentan TODOS y el CA arranca")
        print("con todos ellos (`focos_iniciales`). Quedarse con uno porque da")
        print("mejor coincidencia invalidaría la validación externa.")

    # --- Mapa del evento ---------------------------------------------------
    fig, ax = plt.subplots(figsize=(7, 7))
    otros = d[(d["evento"] != eid)]
    ax.scatter(otros["longitude"], otros["latitude"], s=6, c="#CBD5E1",
               label="Otros focos 2023")
    sc = ax.scatter(focos["longitude"], focos["latitude"],
                    c=(focos["fecha_hora_utc"] - t_ini).dt.total_seconds() / 3600,
                    s=26, cmap="inferno", label="Evento seleccionado")
    ax.scatter(iniciales["longitude"], iniciales["latitude"], s=140,
               facecolors="none", edgecolors="#0F766E", linewidths=2,
               label="Focos iniciales")
    plt.colorbar(sc, ax=ax, label="Horas desde la primera detección")
    ax.set_xlabel("Longitud"); ax.set_ylabel("Latitud")
    ax.set_title(f"Evento {mejor.evento_id} — Rurrenabaque 2023\n"
                 f"{mejor.inicio_utc:%d/%m/%Y} a {mejor.fin_utc:%d/%m/%Y} · "
                 f"{int(mejor.focos)} detecciones", fontsize=10)
    ax.legend(fontsize=8); ax.set_aspect("equal", adjustable="datalim")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(res / "f4_evento_mapa.png", dpi=150)
    plt.close(fig)

    return sel


def sensibilidad(df: pd.DataFrame, res: Path) -> None:
    """Cómo cambia el evento si se mueven los parámetros del agrupamiento.

    Va en la tesis: enseña que la elección del evento no depende de haber
    afinado el DBSCAN hasta que saliera el grupo que convenía.
    """
    print("\n" + "=" * 74)
    print("ANÁLISIS DE SENSIBILIDAD DEL AGRUPAMIENTO")
    print("=" * 74)
    print(f"{'eps_m':>7}{'eps_h':>7}{'min':>5}{'eventos':>9}"
          f"{'focos mayor':>13}{'inicio mayor':>20}")
    print("-" * 61)
    filas = []
    for eps_m in (1000, 1500, 2000, 3000):
        for eps_h in (24, 48, 72):
            for ms in (3, 5, 8):
                out = fase4_eventos(df, res, eps_m, eps_h, ms, guardar=False)
                ev = out[0] if isinstance(out, tuple) else out
                if ev.empty:
                    print(f"{eps_m:>7}{eps_h:>7}{ms:>5}{0:>9}{'—':>13}{'—':>20}")
                    continue
                m = ev.iloc[0]
                print(f"{eps_m:>7}{eps_h:>7}{ms:>5}{len(ev):>9}{int(m.focos):>13}"
                      f"{m.inicio_utc.strftime('%Y-%m-%d %H:%M'):>20}")
                filas.append({"eps_m": eps_m, "eps_h": eps_h, "min_muestras": ms,
                              "n_eventos": len(ev), "focos_mayor": int(m.focos),
                              "inicio_mayor": m.inicio_utc.isoformat()})
    pd.DataFrame(filas).to_csv(res / "f4_sensibilidad.csv", index=False)
    print(f"\nEscrito: {res / 'f4_sensibilidad.csv'}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", default="../datos/firms_rbq_2023.csv")
    ap.add_argument("--resultados", default="../resultados")
    ap.add_argument("--eps-m", type=float, default=1500)
    ap.add_argument("--eps-h", type=float, default=48)
    ap.add_argument("--min-muestras", type=int, default=5)
    ap.add_argument("--sensibilidad", action="store_true")
    ap.add_argument("--evento", default=None,
                    help="Fija el evento a mano tras revisar "
                         "eventos_candidatos.csv, p. ej. --evento E03")
    args = ap.parse_args()

    ruta = Path(args.csv)
    if not ruta.exists():
        print(f"ERROR: no existe {ruta}. Ejecuta antes f2_descargar_firms_2023.py.")
        return 1

    df = pd.read_csv(ruta, parse_dates=["acq_date", "fecha_hora_utc"])
    res = Path(args.resultados)
    res.mkdir(parents=True, exist_ok=True)

    tabla = fase3_mensual(df, res)
    fase3_diario(df, tabla.nlargest(3, "focos")["mes"].tolist(), res)

    if args.sensibilidad:
        sensibilidad(df, res)

    out = fase4_eventos(df, res, args.eps_m, args.eps_h, args.min_muestras)
    if isinstance(out, tuple):
        ev, d = out
        if not ev.empty:
            fase4_seleccionar(ev, d, res, args.eps_m, args.eps_h,
                              args.min_muestras, args.evento)
            print("\nSiguiente: bloques 05, 06 y 07 de GEE (imágenes pre/post, "
                  "NBR/dNBR y máscara), usando las fechas de "
                  "evento_validacion.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
