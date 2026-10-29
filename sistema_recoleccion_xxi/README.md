# Sistema de recolección y alertas de KPI

Sistema distribuido (procesos independientes que se comunican con **ZeroMQ PUB/SUB**) que:

1. **Nodos hijos** leen fuentes de datos (Excel, CSV, ...) y publican observaciones con un formato JSON común.
2. El **nodo padre** recibe, guarda todo el historial en **SQLite**, analiza si el cambio es significativo
   (reglas + estadística) y, si lo es, escribe `salida/alertas_powerautomate.json`.
3. **Power Automate** se dispara cuando ese archivo cambia y envía la notificación (p. ej. a Teams).
4. Un **healthchecker** vigila al padre por sus latidos y lo relanza si se cae.
5. Un **dashboard en Streamlit** lee la base SQLite (solo lectura) y muestra estado, historia y alertas.

```
 nodos_hijo/nodo_excel_cumplimiento ─┐ PUB "datos"
 nodos_hijo/_plantilla_nodo (CSV)  ──┼──────────────▶ SUB :5555  nodo_padre/nodo_padre/main.py
 (más nodos...)                     ─┘                    │  guardado.py ─▶ SQLite (historial)
                                                          │  pensamiento.py (¿es significativo?)
                                                          └─▶ salida/alertas_powerautomate.json ─▶ Power Automate
                                      PUB :5556 "latido"/"evento"
 nodo_padre/healthchecker.py  ◀───────────────────────────┘  (si no hay latidos: `python main.py`)
```

## Estructura

```
comun/                         Código compartido por todos los nodos
  contrato.py                  Formato JSON común (header + observaciones) y su validación
  plantilla_mensaje.json       Ejemplo completo de un mensaje
  extractor_base.py            Clase plantilla que todo extractor debe cumplir
  red.py / config_red.json     Sockets ZeroMQ, puertos y tópicos
  utilidades.py                Rutas, lectura de config y logging
nodos_hijo/
  nodo_excel_cumplimiento/     Lee el Excel matricial (Pospago, Tv + Voz)
    main.py                    Orquestación: config -> extractor -> PUB -> ciclo
    extraccion.py              TODA la lógica de lectura del Excel
    config.json
  _plantilla_nodo/             Plantilla para crear nodos nuevos (lee un CSV ordenado)
nodo_padre/
  healthchecker.py             Vigila latidos y relanza al padre
  config_healthchecker.json
  nodo_padre/
    main.py                    Orquestación: SUB -> guardado -> pensamiento -> JSON
    pensamiento.py             El "cerebro": reglas y estadística (funciones puras)
    guardado.py                SQLite + escritura atómica del JSON de salida
    config.json                Rutas y umbrales del análisis
dashboard/
  app.py                       Interfaz Streamlit (6 vistas); solo presentación
  consultas.py                 SQLite (solo lectura) -> DataFrames de pandas
  graficos.py                  Figuras Plotly (colores validados, modo claro y oscuro)
  simulacion_montecarlo.json   Frecuencias de referencia de la simulación del NPS
datos/entrada/Libro1.xlsx      Excel de entrada (no se versiona)
datos/ejemplo/                 Datos ficticios (NPS VIKI en CSV)
scripts/                       Generar Excel ficticio, simular cambios y poblar la base demo
tests/                         Pruebas automáticas (pytest)
salida/                        JSON para Power Automate (generado)
```

Generados al ejecutar: `datos/registro/nodo_padre.sqlite3`, `logs/*.log`, `run/nodo_padre.pid`.

## Instalación (una sola vez)

Desde la raíz del proyecto, en PowerShell:

```bash
python -m venv .venv
```
```bash
.venv\Scripts\Activate.ps1
```
```bash
pip install -r requirements.txt
```

Dependencias: `pyzmq` (mensajería), `openpyxl` (Excel), `psutil` (el healthchecker verifica/termina
un padre colgado por su PID), `streamlit` + `pandas` + `plotly` (dashboard) y `pytest` (pruebas).

## Ejecución

Cada proceso se inicia en **su propia consola**, desde **su propia carpeta**, con el entorno activado.

**Consola 1 — healthchecker** (lanza al padre en una consola nueva si no está corriendo):
```bash
cd nodo_padre; python healthchecker.py
```

**Consola 2 — nodo hijo del Excel** (lee cada 10 s; publica si hay cambios o cada 5 min):
```bash
cd nodos_hijo\nodo_excel_cumplimiento; python main.py
```

**Consola 3 (opcional) — nodo plantilla CSV** con datos ficticios de NPS VIKI:
```bash
cd nodos_hijo\_plantilla_nodo; python main.py
```

También puedes arrancar el padre a mano (`cd nodo_padre\nodo_padre; python main.py`); el
healthchecker lo detecta por sus latidos y no lo duplica.

Opciones útiles:

| Comando | Para qué |
|---|---|
| `python main.py --solo-mostrar` (hijo) | Ver lo que extrae sin publicar: ideal para depurar la lectura |
| `python main.py --una-vez` (hijo) | Leer y publicar una sola vez |
| `python healthchecker.py --misma-consola` | El padre comparte la consola del healthchecker |
| `python healthchecker.py --solo-monitorear` | Solo imprime el estado, nunca relanza |

## Cómo comprobar que funciona

1. Arranca el healthchecker: a los ~10 s escribe `Lanzando nodo padre` y luego `Padre OK | pid ...` cada 2 s.
2. Arranca el nodo plantilla CSV: el padre registra `ALERTA MEDIA | viki.nps_general_call_center` (3 caídas
   seguidas bajo la meta) y aparece `salida/alertas_powerautomate.json`.
3. Arranca el nodo Excel: guarda 76 observaciones; no hay alerta porque octubre mejoró.
4. Simula que llega el real de noviembre con una caída (crea `Libro1.xlsx.bak` antes de modificar):
   ```bash
   python scripts/simular_cambio.py datos/entrada/Libro1.xlsx --segmento Pospago --mes 11 --real 800
   ```
   En ≤10 s el JSON trae 3 alertas de Pospago (cumplimiento mensual `alta`/P1, etc.).
   Para deshacer: reemplaza `Libro1.xlsx` por `Libro1.xlsx.bak`.
5. Cierra la consola del padre: el healthchecker avisa `Sin latidos...` y lo relanza en ~8 s.
6. Pruebas automáticas (desde la raíz): `python -m pytest -q`

## Dashboard

Desde la raíz del proyecto (con el entorno activado). Con la base real del nodo padre:

```bash
streamlit run dashboard/app.py
```

Para verlo con datos de demostración (ficticios, no toca la base real ni `salida/`):

```bash
python scripts/poblar_demo.py
```
```bash
streamlit run dashboard/app.py -- --bd datos/registro/demo.sqlite3
```

Se abre en `http://localhost:8501`. Los filtros de la barra lateral (canal, criticidad, nivel, cumplimiento,
KPI) aplican a todas las vistas; los datos se releen cada 15 s o con el botón **Actualizar datos**.

| Vista | Qué muestra |
|---|---|
| Resumen | KPI monitoreados, cumplen / bajo la meta, alertas por nivel, última actualización, KPI por tendencia, actividad de los nodos |
| Monitor de KPI | Tabla del último periodo de cada KPI: valor, meta, variación, tendencia, nivel, prioridad y motivo |
| Evolución histórica | Valor vs meta con las alertas marcadas, variaciones entre periodos, frecuencias de la simulación Monte Carlo (y lo observado si el KPI está en puntos) |
| Comportamiento | Señales por regla (tendencia, estabilidad) separadas de las exploratorias (volatilidad, dato inusual, patrón) |
| Detalle de alertas | Por alerta: valores, meta, variación, criticidad, reglas activadas, evidencia, acción recomendada, fechas y estados |
| Historial de alertas | Todas las alertas detectadas y si se notificaron, quedaron bajo el umbral o eran históricas/repetidas |

El dashboard abre SQLite en **modo solo lectura**: puede funcionar mientras el padre escribe y nunca altera
el historial. Cada gráfico tiene debajo su tabla de datos ("Ver datos ..."). Los colores de nivel de alerta
son colores de estado y siempre van con icono y texto.

## Contrato JSON común

Todos los hijos envían `{"header": {...}, "payload": {"observaciones": [...]}}`. El header dice **qué nodo**
generó la información (`nodo_id`), de qué fuente, cuándo y con qué número de secuencia. Cada observación
distingue la **fecha de referencia** (a qué periodo corresponde) de la **fecha de captura** (cuándo se leyó).
Ver [comun/contrato.py](comun/contrato.py) y [comun/plantilla_mensaje.json](comun/plantilla_mensaje.json).
Un mensaje que no cumple el contrato se descarta con un aviso; el padre sigue funcionando.

## Lectura del Excel de cumplimiento

El archivo no es una tabla: tiene bloques de 4 filas por segmento y pares de columnas (meta, real) que
alternan meses y acumulados (M1, M2, Acum 1-2, M3, Acum 1-3, ...). [extraccion.py](nodos_hijo/nodo_excel_cumplimiento/extraccion.py)
lo convierte en registros ordenados en 6 pasos y valida la calidad:

- Un par es **acumulado** si su Meta Driver = suma de las metas mensuales anteriores (auto-detección).
- Se omiten meses **sin real** (noviembre y diciembre en el archivo actual).
- Se omiten **acumulados incompletos o inconsistentes**: en `Libro1.xlsx` el acumulado 1-11 usa el real de
  1-10 porque noviembre está vacío; tomarlo daría una falsa caída de 111 % a 103 %.
- Las fechas no vienen en el archivo: se deducen de `anio` y `mes_inicial` en `config.json` (supuesto: mes 1 = enero 2026).

Por cada segmento y periodo se emiten 2 KPI: `<segmento>.cumplimiento_<mensual|acumulado>` (%, meta 100) y
`<segmento>.real_<mensual|acumulado>` (unidades, meta = meta jugada).

## Reglas del cerebro (pensamiento.py)

Configurables en [nodo_padre/nodo_padre/config.json](nodo_padre/nodo_padre/config.json). Son **experimentales**.

| Regla | Qué hace | Tipo |
|---|---|---|
| Magnitud | variación actual − anterior (pp, o % relativo para unidades) en intervalos `<1, [1,2) ... >=5` sin solapamiento | regla |
| Meta + matriz | el deterioro y si se cumple la meta definen el nivel (`sin_alerta`, `baja`, `media`, `alta`) | regla |
| Criticidad | ajusta la **prioridad** P1–P4, no el nivel | regla |
| Tendencia | 3 variaciones consecutivas en contra ⇒ al menos `media`; distingue caída aislada | regla experimental |
| Estabilidad | 3 variaciones \|v\| < 1; estable bajo la meta **no** es buen desempeño | regla experimental |
| Volatilidad | desviación estándar reciente / histórica | exploratoria |
| Dato inusual | \|z\| > 2 frente a ≥ 6 variaciones previas; pide verificar el dato, no afirma error | exploratoria |
| Patrón repetitivo | deterioros seguidos de recuperación; nunca se afirma como confirmado | exploratoria |

Se escribe en el JSON solo si el nivel ≥ `nivel_minimo_notificacion` (por defecto `media`), si es el
**periodo más reciente** del KPI (en la carga inicial no se notifica el historial) y si esa misma condición
**no se notificó antes** (clave en la tabla `notificaciones`). Si el JSON no se puede escribir (p. ej.
OneDrive lo bloquea), las alertas quedan pendientes y se reintenta en cada latido.

Perfiles actuales: NPS con intervalos de 1 punto (simulación Monte Carlo: caídas ≥3 pts ≈ 8 % de escenarios,
≥5 pts ≈ 1,3 %); cumplimiento mensual con intervalos de 5 pp porque en este Excel varía 10-20 pp entre meses;
real en % relativo. **Recalibrar con datos reales autorizados.**

## Crear un nodo hijo nuevo

1. Copia `nodos_hijo/_plantilla_nodo` a `nodos_hijo/nodo_mi_fuente`.
2. En `config.json` cambia `nodo_id` (único) y la ruta de la fuente.
3. En `extraccion.py` implementa `extraer()` para que devuelva `list[Observacion]`. `main.py` no se toca.
4. Si el KPI necesita umbrales distintos, añade un perfil con su `patron` en el config del padre.

## Power Automate (pendiente de configurar)

Disparador sugerido: *OneDrive para la Empresa → Cuando se modifica un archivo* sobre
`salida/alertas_powerautomate.json`, luego *Obtener contenido de archivo*, *Analizar JSON* y publicar en Teams
con el conector aprobado. El archivo se reemplaza de forma atómica (se escribe un `.tmp` y se renombra);
si el disparador vigila la carpeta, filtra para ignorar los `.tmp`. Antes de activarlo hace falta: cuenta y
carpeta de OneDrive/SharePoint corporativa autorizada, permiso para usar el conector de Teams y el canal destino.
No hay URLs de webhook ni credenciales en el código.

## Pruebas

`python -m pytest -q` → **104 pruebas** que cubren: contrato (mensajes inválidos/malformados), extracción
(bloques, columnas, acumulados, meses sin real, datos inconsistentes, archivo corrupto), reglas (caídas,
subidas, cero, límites exactos de intervalos, meta, criticidad, tendencias, estabilidad, volatilidad, dato
inusual, patrones), almacenamiento (duplicados, revisiones, JSON atómico), flujo del padre (no duplicar
notificaciones, error de escritura y reintento), la plantilla CSV (datos faltantes y duplicados) y el
dashboard (consultas, conexión de solo lectura, gráficos en ambos modos y la app completa con `AppTest`).

Además se probó de punta a punta con procesos reales (22-oct-2026): healthchecker lanza al padre, ambos hijos
publican, se genera el JSON, se simuló la caída de noviembre (3 alertas), se mató al padre y fue relanzado en
8 s, y el reenvío tras el reinicio no duplicó la notificación.

## Limitaciones conocidas

- PUB/SUB pierde mensajes si el padre está caído; se compensa con el reenvío periódico de los hijos (5 min).
- El JSON contiene solo el **último lote** de alertas; el historial completo está en SQLite.
- Las fechas del Excel se deducen por posición; si cambia el año o el mes inicial hay que ajustar `config.json`.
- Los umbrales son provisionales y no son límites de control validados.
- El healthchecker solo vigila al padre (no a los hijos).
- El dashboard es de solo lectura: el estado de revisión de una alerta todavía no se puede cambiar desde él.
- La regla de tendencia (3 caídas seguidas ⇒ al menos `media`) no exige una magnitud mínima: en KPI
  **acumulados**, que se mueven poco y de forma encadenada, marca caídas de 0,1 pp como alerta media.

## Próximos pasos

- Flujo de Power Automate + Teams en entorno autorizado.
- Recalibrar umbrales con datos reales; perfiles por KPI para los ~4 canales × 5 KPI.
- Exigir una magnitud mínima a la regla de tendencia (o desactivarla en acumulados).
- Gestión del estado de revisión de alertas (pendiente → revisada) desde el dashboard.
