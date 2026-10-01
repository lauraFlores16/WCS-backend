"""
============================================================================
RECOGER EVIDENCIA PARA LA MEMORIA
============================================================================
Ruta: backend_django/pruebas/recoger_evidencia.py

    cd backend_django
    python pruebas/recoger_evidencia.py

QUÉ HACE
    Ejecuta todo lo que se puede ejecutar sin internet y guarda la salida de
    cada cosa en un archivo de texto, dentro de `evidencia/`. Al final escribe
    un índice con qué es cada archivo y qué pregunta responde.

POR QUÉ ASÍ Y NO COPIANDO DE LA PANTALLA
    Para la memoria hace falta poder citar de dónde sale cada número. Si se
    copia a mano de la consola:

      · se pierde el comando exacto que lo produjo, y sin él nadie puede
        reproducirlo;
      · se pierde la fecha, así que no se sabe con qué versión del código se
        obtuvo;
      · es fácil copiar un número de una corrida y otro de otra, y que no se
        correspondan entre sí.

    Aquí cada archivo lleva su comando, su fecha y su código de salida en la
    cabecera. Es la diferencia entre un dato citable y un número suelto.

QUÉ NO INCLUYE
    Nada que necesite red: la calibración, la descarga de NASA FIRMS, Google
    Earth Engine y las métricas reales de validación. Esas se ejecutan aparte
    y el índice lo dice, para que no parezca que faltan por olvido.
============================================================================
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent          # backend_django
PROYECTO = RAIZ.parent
SALIDA = PROYECTO / "evidencia"

# (archivo, carpeta de trabajo, comando, qué responde)
TAREAS = [
    ("01_integracion_ml_ca.txt", RAIZ,
     [sys.executable, "pruebas/demo_integracion_ml_ca.py"],
     "Integración XGBoost → autómata celular: el punto exacto de entrega, la "
     "comprobación de que p_base no depende de la probabilidad de la celda, "
     "los ocho vecinos de Moore con sus pesos y una corrida completa con su "
     "verificación de determinismo."),

    ("02_calibracion_xgboost.txt", RAIZ / "validacion_rurrenabaque" / "python",
     [sys.executable, "auditar_xgboost.py", "--resultados", "../resultados"],
     "¿Están bien calibradas las probabilidades de XGBoost? AUC-ROC, Brier "
     "score, diagrama de fiabilidad y recalibración isotónica. Contrastado "
     "con los 18.345 focos históricos reales del proyecto."),

    ("03_motor_escenarios.txt", RAIZ,
     [sys.executable, "pruebas/prueba_escenarios.py"],
     "Determinismo del autómata, exactitud del checkpoint, catálogo de los 12 "
     "sucesos meteorológicos y efecto medido de cada escenario sobre el "
     "incendio."),

    ("04_consola_interactiva.txt", RAIZ,
     [sys.executable, "pruebas/prueba_consola.py"],
     "Ciclo completo de una simulación por HTTP: abrir, avanzar por tandas, "
     "pausar, inyectar una tormenta, seguir y guardar como escenario. "
     "Incluye el control de acceso por rol."),

    ("05_rol_brigadista.txt", RAIZ,
     [sys.executable, "pruebas/prueba_brigadista.py"],
     "Rol brigadista y reportes de campo: quién puede crear, quién puede ver, "
     "y que el resto de pantallas le quedan cerradas EN EL SERVIDOR, no solo "
     "ocultas en el menú."),

    ("06_control_peticiones.txt", RAIZ,
     [sys.executable, "pruebas/prueba_cola.py"],
     "Control de las peticiones a servicios externos: reintentos, "
     "cortacircuitos y presupuesto separado por servicio."),

    ("07_almacen_supabase.txt", RAIZ,
     [sys.executable, "pruebas/prueba_supabase.py"],
     "Contrato completo del almacén: usuarios, sesiones, escenarios, alertas, "
     "calibraciones, bitácora, informes y permisos."),

    ("08_config_supabase.txt", RAIZ,
     [sys.executable, "pruebas/prueba_url_supabase.py"],
     "Normalización de SUPABASE_URL y diagnóstico de los errores de "
     "configuración más habituales."),

    ("09_maquinaria_validacion.txt", RAIZ / "validacion_rurrenabaque" / "python",
     [sys.executable, "pruebas_validacion.py"],
     "Maquinaria de la validación externa: grid compatible, 8 vecinos de "
     "Moore, NoData excluido de las métricas, aritmética de IoU/Precision/"
     "Recall/F1 contra un caso calculado a mano, y reproducibilidad de las "
     "semillas."),

    ("10_limite_rurrenabaque.txt", RAIZ / "validacion_rurrenabaque" / "python",
     [sys.executable, "verificar_shapefile.py", "--shp", "../datos/Mun_RBQ.shp",
      "--geojson", "../datos/Mun_RBQ_wgs84.geojson"],
     "Verificación del límite municipal de Rurrenabaque: CRS, validez "
     "topológica, área por dos vías independientes y dimensionado del grid "
     "de 500 m."),
]

PENDIENTES = [
    ("Calibración del autómata", "Monitoreo → Capa 4 → Calibrar constantes K",
     "Necesita internet: descarga el DEM (SRTM), las barreras de "
     "OpenStreetMap y el viento ERA5 histórico de cada evento."),
    ("Validación del autómata en Apolo", "python f13_validar_apolo.py --repeticiones 30",
     "Necesita la calibración hecha y congelada con congelar_parametros.py."),
    ("Focos FIRMS de Rurrenabaque 2023", "python f2_descargar_firms_2023.py --clave TU_CLAVE",
     "Necesita una clave MAP_KEY de NASA FIRMS."),
    ("Cicatriz observada (dNBR)", "Bloques 05, 06 y 07 de gee/",
     "Necesita Google Earth Engine."),
    ("Métricas de la validación externa", "python f12_metricas_validacion.py",
     "Necesita todo lo anterior."),
]


def main() -> int:
    SALIDA.mkdir(exist_ok=True)
    inicio = datetime.now()

    print("=" * 74)
    print("RECOGIENDO EVIDENCIA PARA LA MEMORIA")
    print("=" * 74)
    print(f"Destino: {SALIDA}")
    print(f"{len(TAREAS)} comandos. Tarda unos minutos.\n")

    resultados = []
    for i, (archivo, cwd, cmd, descripcion) in enumerate(TAREAS, 1):
        etiqueta = archivo.replace(".txt", "")
        print(f"[{i}/{len(TAREAS)}] {etiqueta} … ", end="", flush=True)
        try:
            r = subprocess.run(cmd, cwd=str(cwd), capture_output=True,
                               text=True, timeout=1200)
            salida = r.stdout + (("\n--- errores ---\n" + r.stderr)
                                 if r.stderr.strip() else "")
            codigo = r.returncode
        except subprocess.TimeoutExpired:
            salida, codigo = "El comando excedió el tiempo límite.", -1
        except Exception as e:  # noqa: BLE001
            salida, codigo = f"No se pudo ejecutar: {e}", -2

        # Cabecera: sin esto el archivo es un número sin procedencia.
        cabecera = (
            "=" * 74 + "\n"
            f"SIPRO FIRE — evidencia para la memoria\n"
            f"{descripcion}\n"
            + "=" * 74 + "\n"
            f"Comando  : {' '.join(Path(c).name if i == 0 else c for i, c in enumerate(cmd))}\n"
            f"Carpeta  : {cwd.relative_to(PROYECTO) if cwd != PROYECTO else '.'}\n"
            f"Ejecutado: {datetime.now().isoformat(timespec='seconds')}\n"
            f"Resultado: {'correcto' if codigo == 0 else f'código {codigo}'}\n"
            + "=" * 74 + "\n\n")

        (SALIDA / archivo).write_text(cabecera + salida, encoding="utf-8")
        print("correcto" if codigo == 0 else f"código {codigo}")
        resultados.append((archivo, descripcion, codigo,
                           len(salida.splitlines())))

    # --- Índice -------------------------------------------------------------
    fallidos = [r for r in resultados if r[2] != 0]
    lineas = [
        "# Evidencia para la memoria — SIPRO FIRE",
        "",
        f"Recogida el {inicio.strftime('%d/%m/%Y a las %H:%M')}.",
        "",
        "Cada archivo lleva en su cabecera el comando exacto que lo produjo, la",
        "carpeta desde la que se ejecutó, la fecha y el código de salida. Sin eso",
        "un número no es citable: no se puede reproducir ni se sabe con qué",
        "versión del código se obtuvo.",
        "",
        "## Archivos",
        "",
    ]
    for archivo, descripcion, codigo, n in resultados:
        estado = "correcto" if codigo == 0 else f"**código {codigo}**"
        lineas += [f"### `{archivo}`", "", descripcion, "",
                   f"{n} líneas · {estado}", ""]

    lineas += [
        "## Lo que NO está aquí, y por qué",
        "",
        "Todo lo que necesita red o servicios externos. No falta por olvido:",
        "no se puede ejecutar sin conexión.",
        "",
        "| Qué | Cómo se obtiene | Qué necesita |",
        "|---|---|---|",
    ]
    for titulo, comando, necesita in PENDIENTES:
        lineas.append(f"| {titulo} | `{comando}` | {necesita} |")

    lineas += [
        "",
        "## Orden de lectura sugerido",
        "",
        "1. `01_integracion_ml_ca.txt` — cómo se conectan los dos modelos.",
        "   Es el punto de partida: sin entender que XGBoost elige DÓNDE y el",
        "   autómata calcula CÓMO se propaga, el resto no se sostiene.",
        "2. `02_calibracion_xgboost.txt` — si las probabilidades del modelo",
        "   predictivo significan lo que dicen.",
        "3. `03_motor_escenarios.txt` — el autómata y los escenarios.",
        "4. `09_maquinaria_validacion.txt` — que las métricas se calculan bien,",
        "   comprobado contra un caso resuelto a mano.",
        "5. `10_limite_rurrenabaque.txt` — el área de validación externa.",
        "6. El resto son pruebas de integración del sistema.",
        "",
    ]
    if fallidos:
        lineas += ["## Atención", "",
                   f"{len(fallidos)} comando(s) no terminaron correctamente. "
                   "Revisa su archivo antes de citar nada de ellos:", ""]
        for a, _, c, _ in fallidos:
            lineas.append(f"- `{a}` (código {c})")
        lineas.append("")

    (SALIDA / "INDICE.md").write_text("\n".join(lineas), encoding="utf-8")

    print("\n" + "=" * 74)
    print(f"  {len(resultados) - len(fallidos)} de {len(resultados)} comandos correctos")
    print(f"  Evidencia en: {SALIDA}")
    print(f"  Empieza por: {SALIDA / 'INDICE.md'}")
    if fallidos:
        print(f"\n  {len(fallidos)} con problemas: " +
              ", ".join(a for a, _, _, _ in fallidos))
    print("=" * 74)
    return 1 if fallidos else 0


if __name__ == "__main__":
    raise SystemExit(main())
