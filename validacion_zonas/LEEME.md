# Validación multizona — cualquier municipio de Bolivia, cualquier año

> **Todo se hace desde el dashboard** (Validación → Municipios y eventos). El
> servidor descarga NASA FIRMS, verifica, agrupa, arma el paquete y corre la
> validación oficial; los resultados se guardan en Supabase (tablas
> `validacion_*`). Única preparación: ejecutar UNA vez
> [`sql/validacion_zonas.sql`](sql/validacion_zonas.sql) en Supabase → SQL Editor.
>
> | Botón | Lo que hace el servidor | Antes (terminal) |
> |---|---|---|
> | Procesar focos FIRMS | descarga el año (_SP), verifica, eventos | `p1_focos_anio.py` |
> | Subir cicatriz | agrega el ráster de MapBiomas a 500 m | — |
> | Preparar y validar | grid, cicatriz, insumos + 30 repeticiones | `p2` + `p3` |
>
> Código del servidor: `api/zonas/` y `api/views_zonas.py`. Prueba de extremo a
> extremo: `python pruebas/prueba_validacion_zonas.py`.
>
> **Cicatrices de MapBiomas:** las que hay en el proyecto (2019–2024) cubren solo
> la región Apolo–Rurrenabaque (lat −15,33 a −13,95; lon −69,12 a −67,07). Para
> otros municipios hay que subir el ráster anual de MapBiomas Fuego Bolivia de ese
> año desde la misma pantalla. Sin él, los focos se ven pero no se confirman ni se
> validan eventos.
>
> Los scripts `python/p0…p4` siguen sirviendo para trabajar sin conexión, pero ya
> no hacen falta. La carpeta `zonas/` del flujo antiguo se puede borrar.

---

## Flujo antiguo por terminal (opcional)

Generaliza lo que se hizo a mano con Rurrenabaque 2023 para que cualquier
municipio y año pase por la misma cadena, con focos de NASA FIRMS **verificados**
y una **partición fija** entre municipios que calibran el motor y municipios que
solo lo validan.

```
p0_catalogo.py         339 municipios (GeoBolivia vía geoBoundaries)     ← ya hecho
p1_focos_anio.py       focos FIRMS del año + verificación + eventos
p2_preparar_evento.py  paquete de un evento: grid, cicatriz, focos, insumos  (fija el ROL)
p3_validar_evento.py   validación oficial: 30 repeticiones, métricas
p4_calibrar_multizona.py  calibración SOLO con municipios de calibración → candidato
```

Todo se ve después en el dashboard: **Validación → Municipios y eventos** (focos y
eventos por año) y **Validación → Validar un evento** (correr y comparar en vivo).

## Instalación (una vez)

```powershell
cd WCS-backend\validacion_zonas\python
pip install -r requisitos.txt
$env:FIRMS_MAP_KEY = "TU_CLAVE"      # https://firms.modaps.eosdis.nasa.gov/api/map_key/
```

Los rásteres de MapBiomas Fuego 2019–2024 (toda Bolivia, 30 m) van en
`datos/mapbiomas/`. No se suben al repo (pesan ~100 MB); los scripts también los
buscan en `validacion_rurrenabaque/datos/cicatrices/`.

## Flujo

```powershell
# 1. Focos verificados y eventos del año (≈5 min por colección VIIRS)
python p1_focos_anio.py --municipio "San Buenaventura" --anio 2022

# 2. Elegir un evento de la tabla y empaquetarlo. --rol solo la primera vez.
python p2_preparar_evento.py --municipio san-buenaventura-la-paz --anio 2022 --evento E004 --rol calibracion

# 3. Validación oficial (30 repeticiones)
python p3_validar_evento.py --paquete san-buenaventura-la-paz-2022-E004

# 4. Con varios paquetes de calibración: afinar el motor
python p4_calibrar_multizona.py
```

Después: `git add validacion_zonas` y push. Render sirve los paquetes nuevos y la
pantalla los lista solos.

## Niveles de verificación de cada detección FIRMS

FIRMS no verifica incendios en terreno: detecta anomalías térmicas. Los criterios
que sí se aplican, todos sobre campos que NASA publica:

| Nivel | Regla |
|---|---|
| descartado | `type` ≠ 0: volcán, fuente estática (industria, gas) u offshore |
| baja | confianza baja (VIIRS `l`, MODIS < 30) o no es del archivo estándar |
| verificado | colección `_SP` (archivo estándar reprocesado) + `type` 0 + confianza nominal/alta |
| confirmado | verificado **y** píxel quemado de MapBiomas del mismo año bajo la huella del sensor (375 m VIIRS, 1 km MODIS) |

Los eventos se forman solo con detecciones verificadas o confirmadas. Un evento es
**confirmado** si al menos la mitad de sus detecciones lo están.

> Consecuencia medida: en Rurrenabaque E122 uno de los tres focos iniciales era de
> confianza baja. Con el filtro, el mismo incendio (aquí E117) arranca de dos celdas y
> da IoU 29,8 % en lugar de 31,4 %. Es la diferencia que introduce exigir verificación.

## Partición calibración / validación

`config/particion.json` dice a qué conjunto pertenece cada municipio. Se fija la
primera vez que se empaqueta un evento (`p2 --rol`) y **no se puede cambiar**:
mover un municipio de validación a calibración después de ver sus métricas
convertiría la validación externa en un ajuste disfrazado.

- `p4` rechaza los paquetes de validación aunque se pidan.
- `p4` no sobrescribe los parámetros congelados: escribe un **candidato** en
  `config/candidatos/` con su aptitud y la del juego congelado actual.
- Un candidato solo merece congelarse si mejora **en los municipios de validación**,
  que no vio. Al congelar hay que repetir `p3 --todos` y la cadena de Rurrenabaque.

## Estructura

```
datos/municipios_bolivia.geojson      catálogo (también en WCS-frontend/public/datos/municipios_bolivia.json)
zonas/<municipio>/limite.geojson
zonas/<municipio>/<año>/focos.csv           detecciones del municipio con nivel y evento
zonas/<municipio>/<año>/eventos.json        eventos con indicadores y verificación
zonas/<municipio>/<año>/resumen_anio.json
zonas/<municipio>/<año>/<evento>/            paquete (paquete.json, grid.csv, observado.csv,
                                             focos_iniciales.csv, evento.json, insumos.json,
                                             resultados/)
zonas/indice.json                           lo que lee el dashboard
config/particion.json
```

## Reglas heredadas de Rurrenabaque (no cambian)

- Grid de 500 m en la retícula global de 0,0045°; ventana = detecciones del evento ± 8 km.
- Observado = fracción quemada ≥ 0,5 dentro de la huella FIRMS ± 2 celdas; quemas de
  otros incendios del año → `valido = 0`.
- Focos iniciales = detecciones de las primeras 6 h. Horizonte = duración del evento.
- 30 repeticiones, umbral 0,5, solo celdas válidas en las métricas.
