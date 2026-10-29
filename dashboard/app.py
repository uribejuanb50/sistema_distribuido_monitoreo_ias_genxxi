"""Dashboard de monitoreo de KPI (Streamlit).

Solo presentación: los datos vienen de consultas.py (SQLite en solo lectura) y los
gráficos de graficos.py. No modifica la base del nodo padre.

Uso (desde la raíz del proyecto):
    streamlit run dashboard/app.py
    streamlit run dashboard/app.py -- --bd datos/registro/demo.sqlite3
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

DIR_APP = Path(__file__).resolve().parent
RAIZ_PROYECTO = DIR_APP.parent
for _ruta in (DIR_APP, RAIZ_PROYECTO / "nodo_padre" / "nodo_padre", RAIZ_PROYECTO):
    if str(_ruta) not in sys.path:
        sys.path.insert(0, str(_ruta))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

import consultas  # noqa: E402
import graficos  # noqa: E402
from comun.utilidades import cargar_json, resolver_ruta  # noqa: E402
from graficos import TENDENCIAS, etiqueta_nivel  # noqa: E402

RUTA_SIMULACION = DIR_APP / "simulacion_montecarlo.json"
SEGUNDOS_CACHE = 15
ORDEN_NIVEL = {"alta": 0, "media": 1, "baja": 2, "sin_alerta": 3}
SUFIJO_UNIDAD = {"porcentaje": " %", "pp": " pp", "%": " %", "puntos": " pts", "unidades": ""}


# ---------------------------------------------------------------- datos

def ruta_base_datos() -> Path:
    """--bd (tras '--'), variable DASHBOARD_BD o la ruta del config del nodo padre."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--bd")
    argumentos, _ = parser.parse_known_args()
    ruta = (argumentos.bd or os.environ.get("DASHBOARD_BD")
            or cargar_json(RAIZ_PROYECTO / "nodo_padre" / "nodo_padre" / "config.json")["ruta_base_datos"])
    return resolver_ruta(ruta)


@st.cache_data(ttl=SEGUNDOS_CACHE, show_spinner=False)
def cargar_datos(ruta: str) -> dict[str, pd.DataFrame]:
    with consultas.abrir(Path(ruta)) as conexion:
        return {
            "estado": consultas.estado_actual(conexion),
            "alertas": consultas.alertas(conexion),
            "nodos": consultas.actividad_nodos(conexion),
        }


@st.cache_data(ttl=SEGUNDOS_CACHE, show_spinner=False)
def cargar_historial(ruta: str, kpi_id: str) -> pd.DataFrame:
    with consultas.abrir(Path(ruta)) as conexion:
        return consultas.historial_kpi(conexion, kpi_id)


def modo_color() -> str:
    try:
        return "oscuro" if st.context.theme.type == "dark" else "claro"
    except AttributeError:  # versiones de Streamlit sin st.context.theme
        return "claro"


# ---------------------------------------------------------------- formato

def fmt(valor: Any, unidad: str | None = None, signo: bool = False) -> str:
    if valor is None or pd.isna(valor):
        return "—"
    texto = f"{valor:+,.2f}" if signo else f"{valor:,.2f}"
    return texto + SUFIJO_UNIDAD.get(unidad or "", f" {unidad}")


def fmt_fecha(valor: Any) -> str:
    if valor is None or pd.isna(valor):
        return "—"
    return pd.to_datetime(valor).strftime("%Y-%m-%d %H:%M")


def fmt_fecha_corta(valor: Any) -> str:
    """'Hoy 10:23' si es de hoy; si no, solo la fecha (cabe en una tarjeta)."""
    if valor is None or pd.isna(valor):
        return "—"
    momento = pd.to_datetime(valor)
    if momento.date() == pd.Timestamp.now(tz=momento.tz).date():
        return f"Hoy {momento:%H:%M}"
    return f"{momento:%Y-%m-%d}"


def si_no(valor: Any, insuficiente: str = "Datos insuficientes") -> str:
    if valor is None or pd.isna(valor):
        return insuficiente
    return "Sí" if valor else "No"


def ordenar_por_nivel(tabla: pd.DataFrame) -> pd.DataFrame:
    return tabla.assign(_orden=tabla["nivel_alerta"].map(ORDEN_NIVEL).fillna(9)).sort_values(
        ["_orden", "canal", "kpi_id"]).drop(columns="_orden")


def dibujar(figura) -> None:
    # theme=None: graficos.py ya define colores para modo claro y oscuro, y el tema de
    # Streamlit movería títulos y leyendas.
    st.plotly_chart(figura, width="stretch", theme=None,
                    config={"displaylogo": False, "locale": "es"})


# ---------------------------------------------------------------- filtros

def aplicar_filtros(estado: pd.DataFrame) -> pd.DataFrame:
    """Filtros de la barra lateral; afectan a todas las vistas."""
    st.sidebar.header("Filtros")
    canales = st.sidebar.multiselect("Canal", sorted(estado["canal"].unique()), placeholder="Todos")
    criticidades = st.sidebar.multiselect("Criticidad", sorted(estado["criticidad"].dropna().unique()),
                                          placeholder="Todas")
    niveles = st.sidebar.multiselect("Nivel de alerta", list(ORDEN_NIVEL), format_func=etiqueta_nivel,
                                     placeholder="Todos")
    cumplimiento = st.sidebar.radio("Cumplimiento de meta", ["Todos", "Cumple", "No cumple"], horizontal=True)
    filtrado = estado
    if canales:
        filtrado = filtrado[filtrado["canal"].isin(canales)]
    if criticidades:
        filtrado = filtrado[filtrado["criticidad"].isin(criticidades)]
    if niveles:
        filtrado = filtrado[filtrado["nivel_alerta"].isin(niveles)]
    if cumplimiento != "Todos":
        filtrado = filtrado[filtrado["cumple_meta"] == (cumplimiento == "Cumple")]
    kpis = st.sidebar.multiselect("KPI", sorted(filtrado["kpi_id"]), placeholder="Todos")
    if kpis:
        filtrado = filtrado[filtrado["kpi_id"].isin(kpis)]
    return filtrado


# ---------------------------------------------------------------- vistas

def vista_resumen(estado: pd.DataFrame, alertas: pd.DataFrame, nodos: pd.DataFrame, modo: str) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("KPI monitoreados", len(estado))
    c2.metric("Cumplen la meta", int((estado["cumple_meta"] == True).sum()))  # noqa: E712
    c3.metric("Por debajo de la meta", int((estado["cumple_meta"] == False).sum()))  # noqa: E712
    ultimo = nodos["ultimo_mensaje"].max() if not nodos.empty else None
    c4.metric("Última actualización", fmt_fecha_corta(ultimo), help=fmt_fecha(ultimo))

    st.subheader("Alertas por nivel (periodo más reciente de cada KPI)")
    columnas = st.columns(4)
    for columna, nivel in zip(columnas, ORDEN_NIVEL):
        columna.metric(etiqueta_nivel(nivel), int((estado["nivel_alerta"] == nivel).sum()))
    notificadas = int((alertas["estado_notificacion"] == "notificada").sum()) if not alertas.empty else 0
    st.caption(f"Alertas enviadas al JSON de Power Automate desde el inicio: **{notificadas}**.")

    st.subheader("Estados de comportamiento")
    izquierda, derecha = st.columns([3, 2])
    with izquierda:
        dibujar(graficos.figura_tendencias(estado, modo))
    with derecha:
        st.metric("Estables (variaciones pequeñas)", int((estado["estable"] == True).sum()))  # noqa: E712
        st.metric("Volatilidad alta (exploratoria)", int((estado["volatilidad"] == "alta").sum()))
        st.metric("Posible dato inusual (exploratoria)", int((estado["dato_inusual"] == True).sum()))  # noqa: E712
        st.metric("Posible patrón repetitivo", int((estado["patron_repetitivo"] == True).sum()))  # noqa: E712

    st.subheader("Fuentes de datos (nodos hijos)")
    st.dataframe(
        nodos.rename(columns={"nodo_id": "Nodo", "tipo_fuente": "Tipo", "fuente": "Fuente", "mensajes": "Mensajes",
                              "observaciones_recibidas": "Observaciones recibidas",
                              "ultimo_mensaje": "Último mensaje"}),
        hide_index=True, width="stretch",
    )


def vista_monitor(estado: pd.DataFrame) -> None:
    st.caption("Último periodo de cada KPI. Ordenado por nivel de alerta. Usa los filtros de la barra lateral.")
    tabla = ordenar_por_nivel(estado)
    st.dataframe(
        pd.DataFrame({
            "Nivel": tabla["nivel_alerta"].map(etiqueta_nivel),
            "Prioridad": tabla["prioridad_revision"].fillna("—"),
            "Canal": tabla["canal"],
            "KPI": tabla["nombre_kpi"],
            "Periodo": tabla["fecha_referencia"],
            "Valor": [fmt(v, u) for v, u in zip(tabla["valor"], tabla["unidad"])],
            "Meta": [fmt(v, u) for v, u in zip(tabla["meta"], tabla["unidad"])],
            "Cumple meta": tabla["cumple_meta"].map(lambda v: si_no(v, "Sin meta")),
            "Variación": [fmt(v, u, signo=True) for v, u in zip(tabla["variacion"], tabla["unidad_variacion"])],
            "Tendencia": tabla["tendencia"].map(TENDENCIAS),
            "Criticidad": tabla["criticidad"],
            "Motivo": tabla["motivos"],
            "ID": tabla["kpi_id"],
        }),
        hide_index=True, width="stretch",
        column_config={"Motivo": st.column_config.TextColumn(width="large")},
    )


def selector_kpi(estado: pd.DataFrame, clave: str) -> pd.Series | None:
    if estado.empty:
        st.info("No hay KPI que coincidan con los filtros.")
        return None
    opciones = list(estado["kpi_id"])
    nombres = {fila.kpi_id: f"{fila.canal} · {fila.nombre_kpi} ({fila.kpi_id})" for fila in estado.itertuples()}
    kpi_id = st.selectbox("KPI", opciones, format_func=nombres.get, key=clave)
    return estado[estado["kpi_id"] == kpi_id].iloc[0]


def vista_evolucion(ruta: Path, estado: pd.DataFrame, modo: str) -> None:
    fila = selector_kpi(estado, "kpi_evolucion")
    if fila is None:
        return
    historial = cargar_historial(str(ruta), fila["kpi_id"])
    unidad_var = fila["unidad_variacion"] or fila["unidad"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Valor actual", fmt(fila["valor"], fila["unidad"]))
    c2.metric("Meta", fmt(fila["meta"], fila["unidad"]))
    c3.metric("Variación", fmt(fila["variacion"], unidad_var, signo=True))
    c4.metric("Nivel", etiqueta_nivel(fila["nivel_alerta"]))

    dibujar(graficos.figura_evolucion(historial, fila["unidad"], modo))
    dibujar(graficos.figura_variaciones(historial, unidad_var, modo))
    with st.expander("Ver datos de la serie"):
        st.dataframe(pd.DataFrame({
            "Periodo": historial["fecha_referencia"].dt.strftime("%Y-%m-%d"),
            "Valor": historial["valor"], "Meta": historial["meta"], "Variación": historial["variacion"],
            "Intervalo": historial["intervalo_magnitud"], "Nivel": historial["nivel_alerta"].map(etiqueta_nivel),
            "Notificada": historial["notificada"].map(si_no), "Versión del dato": historial["version"],
        }), hide_index=True, width="stretch")

    st.subheader("Intervalos y frecuencias de la simulación Monte Carlo")
    st.caption("Referencia EXPLORATORIA (10.000 escenarios del NPS): sirve para diseñar los intervalos; "
               "no son probabilidades garantizadas ni límites de control validados.")
    simulacion = consultas.cargar_simulacion(RUTA_SIMULACION)
    observado = None
    if fila["unidad"] == "puntos" and historial["variacion"].notna().any():
        observado = consultas.distribucion_observada(historial["variacion"])
    else:
        st.caption("La comparación con lo observado se muestra solo para KPI en puntos (como el NPS), "
                   "porque la simulación usa esa escala.")
    dibujar(graficos.figura_frecuencias(simulacion, observado, modo))
    with st.expander("Ver datos de frecuencias"):
        tabla = simulacion.rename(columns={"porcentaje": "% simulación"})
        if observado is not None:
            tabla = tabla.merge(observado.rename(columns={"porcentaje": "% observado",
                                                          "observaciones": "n observado"}),
                                on=["direccion", "intervalo"])
        st.dataframe(tabla, hide_index=True, width="stretch")


def vista_comportamiento(estado: pd.DataFrame) -> None:
    tabla = ordenar_por_nivel(estado)
    st.markdown("**Señales por regla** (experimentales: 3 variaciones consecutivas). Se usan para decidir el nivel.")
    st.dataframe(pd.DataFrame({
        "Canal": tabla["canal"], "KPI": tabla["kpi_id"],
        "Tendencia": tabla["tendencia"].map(TENDENCIAS),
        "Estable": tabla["estable"].map(si_no),
        "Cumple meta": tabla["cumple_meta"].map(lambda v: si_no(v, "Sin meta")),
        "Lectura": [
            "Estable pero bajo la meta: no implica buen desempeño" if e is True and c is False else ""
            for e, c in zip(tabla["estable"], tabla["cumple_meta"])
        ],
    }), hide_index=True, width="stretch")

    st.markdown("**Señales exploratorias** (indicios estadísticos: piden revisar, no confirman un problema).")
    st.dataframe(pd.DataFrame({
        "Canal": tabla["canal"], "KPI": tabla["kpi_id"],
        "Volatilidad": [f"{v or 'datos_insuficientes'}" + (f" (razón {r})" if pd.notna(r) else "")
                        for v, r in zip(tabla["volatilidad"], tabla["razon_volatilidad"])],
        "Dato inusual": [si_no(d) + (f" (z = {z})" if pd.notna(z) else "")
                         for d, z in zip(tabla["dato_inusual"], tabla["z"])],
        "Patrón repetitivo": [d if isinstance(d, str) else "—" for d in tabla["descripcion_patron"]],
    }), hide_index=True, width="stretch",
        column_config={"Patrón repetitivo": st.column_config.TextColumn(width="large")})


def evidencia(detalle: dict[str, Any], unidad: str) -> pd.DataFrame:
    """Tabla de reglas y señales que respaldan una alerta."""
    vol = detalle.get("volatilidad") or {}
    inusual = detalle.get("dato_inusual") or {}
    patron = detalle.get("patron_repetitivo") or {}
    filas = [
        ("Magnitud del cambio", "regla", detalle.get("intervalo_magnitud") or "—",
         f"variación {fmt(detalle.get('variacion'), detalle.get('unidad_variacion'), signo=True)}"),
        ("Cumplimiento de meta", "regla", si_no(detalle.get("cumple_meta"), "Sin meta"),
         f"valor {fmt(detalle.get('valor_actual'), unidad)} vs meta {fmt(detalle.get('meta'), unidad)}"),
        ("Tendencia", "regla", TENDENCIAS.get(detalle.get("tendencia"), "—"), "3 variaciones consecutivas"),
        ("Estabilidad", "regla", si_no(detalle.get("estable")), "3 variaciones con |v| < umbral"),
        ("Volatilidad", "exploratoria", vol.get("clasificacion", "—"),
         f"desv. reciente {vol.get('desviacion_reciente')} / histórica {vol.get('desviacion_historica')}"),
        ("Dato inusual", "exploratoria", si_no(inusual.get("es_inusual")),
         inusual.get("nota") or f"z = {inusual.get('z')} con {inusual.get('n_historico')} variaciones previas"),
        ("Patrón repetitivo", "exploratoria", si_no(patron.get("detectado")), patron.get("descripcion") or "—"),
    ]
    return pd.DataFrame(filas, columns=["Señal", "Tipo", "Resultado", "Evidencia"])


def vista_detalle_alertas(alertas: pd.DataFrame, kpis_visibles: set[str]) -> None:
    incluir_todas = st.toggle("Incluir alertas detectadas que no se notificaron", value=False)
    tabla = alertas[alertas["kpi_id"].isin(kpis_visibles)] if not alertas.empty else alertas
    if not incluir_todas and not tabla.empty:
        tabla = tabla[tabla["estado_notificacion"] == "notificada"]
    if tabla.empty:
        st.info("No hay alertas para mostrar con los filtros actuales.")
        return
    st.caption(f"{len(tabla)} alertas, de la más reciente a la más antigua.")
    for fila in tabla.head(50).itertuples():
        titulo = (f"{etiqueta_nivel(fila.nivel_alerta)} · {fila.prioridad_revision or '—'} · {fila.canal} · "
                  f"{fila.nombre_kpi} · periodo {fila.fecha_referencia}")
        with st.expander(titulo):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Valor actual", fmt(fila.valor, fila.unidad))
            c2.metric("Valor anterior", fmt(fila.valor_anterior, fila.unidad))
            c3.metric("Meta", fmt(fila.meta, fila.unidad))
            c4.metric("Variación", fmt(fila.variacion, fila.unidad_variacion, signo=True))
            st.markdown(
                f"**KPI:** `{fila.kpi_id}` · **Canal:** {fila.canal} · **Criticidad:** {fila.criticidad} · "
                f"**Nivel:** {etiqueta_nivel(fila.nivel_alerta)}  \n"
                f"**Detectada:** {fmt_fecha(fila.evaluado_en)} · **Notificación:** {fila.estado_notificacion}"
                + (f" (lote `{fila.id_lote[:8]}`, {fmt_fecha(fila.escrito_en)})" if pd.notna(fila.id_lote) else "")
                + f" · **Revisión:** {fila.estado_revision} · **Nodo origen:** {fila.nodo_id}"
            )
            st.markdown("**Reglas activadas**")
            st.markdown("\n".join(f"- {motivo}" for motivo in fila.detalle.get("motivos", [])))
            st.markdown("**Evidencia utilizada**")
            st.dataframe(evidencia(fila.detalle, fila.unidad), hide_index=True, width="stretch")
            st.info(f"**Acción recomendada:** {fila.accion_recomendada}")


def vista_historial(alertas: pd.DataFrame, kpis_visibles: set[str]) -> None:
    tabla = alertas[alertas["kpi_id"].isin(kpis_visibles)] if not alertas.empty else alertas
    if tabla.empty:
        st.info("Todavía no se ha detectado ninguna alerta.")
        return
    conteo = tabla["estado_notificacion"].value_counts()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Detectadas", len(tabla))
    c2.metric("Notificadas", int(conteo.get("notificada", 0)))
    c3.metric("Significativas sin notificar", int(conteo.get("significativa sin notificar", 0)),
              help="Periodos históricos (carga inicial) o condiciones ya notificadas antes.")
    c4.metric("Bajo el umbral", int(conteo.get("bajo el umbral", 0)),
              help="Nivel menor que 'nivel_minimo_notificacion' del config del padre.")
    st.caption("Cada condición se notifica una sola vez: el padre guarda una clave (KPI + periodo + nivel + dato) "
               "y no vuelve a escribirla en el JSON aunque el hijo reenvíe los mismos datos.")
    st.dataframe(pd.DataFrame({
        "Detectada": tabla["evaluado_en"].map(fmt_fecha),
        "Nivel": tabla["nivel_alerta"].map(etiqueta_nivel),
        "Canal": tabla["canal"], "KPI": tabla["kpi_id"], "Periodo": tabla["fecha_referencia"],
        "Notificación": tabla["estado_notificacion"],
        "Lote": tabla["id_lote"].map(lambda v: v[:8] if isinstance(v, str) else "—"),
        "Escrita en JSON": tabla["escrito_en"].map(fmt_fecha),
        "Revisión": tabla["estado_revision"],
        "Versión del dato": tabla["version"],
    }), hide_index=True, width="stretch")


# ---------------------------------------------------------------- página

def main() -> None:
    st.set_page_config(page_title="Monitor de KPI", page_icon="📈", layout="wide")
    st.title("Monitor de KPI")
    ruta = ruta_base_datos()
    st.sidebar.caption(f"Base de datos: `{ruta.name}`")
    if st.sidebar.button("Actualizar datos", width="stretch"):
        st.cache_data.clear()

    try:
        datos = cargar_datos(str(ruta))
    except consultas.ErrorBaseDatos as error:
        st.error(f"{error}")
        st.markdown("Arranca el nodo padre y al menos un nodo hijo (ver README), o crea datos de demostración con "
                    "`python scripts/poblar_demo.py` y abre `streamlit run dashboard/app.py -- --bd "
                    "datos/registro/demo.sqlite3`.")
        st.stop()
    estado, alertas, nodos = datos["estado"], datos["alertas"], datos["nodos"]
    if estado.empty:
        st.info("La base existe pero aún no tiene observaciones. Ejecuta un nodo hijo para enviar datos.")
        st.stop()

    filtrado = aplicar_filtros(estado)
    kpis_visibles = set(filtrado["kpi_id"])
    st.caption(f"Datos actualizados cada {SEGUNDOS_CACHE} s como máximo · mostrando {len(filtrado)} de "
               f"{len(estado)} KPI")
    modo = modo_color()
    pestañas = st.tabs(["Resumen", "Monitor de KPI", "Evolución histórica", "Comportamiento",
                        "Detalle de alertas", "Historial de alertas"])
    with pestañas[0]:
        vista_resumen(filtrado, alertas, nodos, modo)
    with pestañas[1]:
        vista_monitor(filtrado)
    with pestañas[2]:
        vista_evolucion(ruta, filtrado, modo)
    with pestañas[3]:
        vista_comportamiento(filtrado)
    with pestañas[4]:
        vista_detalle_alertas(alertas, kpis_visibles)
    with pestañas[5]:
        vista_historial(alertas, kpis_visibles)


main()
