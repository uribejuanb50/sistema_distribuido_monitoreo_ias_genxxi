"""Consultas de solo lectura a la base SQLite del nodo padre.

No usa Streamlit: devuelve DataFrames de pandas, así se puede probar por separado y
reutilizar en otros reportes. La conexión es de SOLO LECTURA (mode=ro): el dashboard
nunca modifica el historial y puede leer mientras el nodo padre escribe.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

import pandas as pd

from pensamiento import etiquetas_intervalos, indice_magnitud

LIMITES_SIMULACION = (1.0, 2.0, 3.0, 4.0, 5.0)  # intervalos usados en la simulación Monte Carlo del NPS
COLUMNAS_SI_NO = ("cumple_meta", "estable", "dato_inusual", "patron_repetitivo")


class ErrorBaseDatos(Exception):
    """La base de datos no existe o no se puede abrir."""


@contextmanager
def abrir(ruta: Path) -> Iterator[sqlite3.Connection]:
    """Abre la base en modo solo lectura y la cierra al terminar."""
    if not ruta.exists():
        raise ErrorBaseDatos(f"No existe la base de datos: {ruta}")
    try:
        conexion = sqlite3.connect(f"{ruta.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise ErrorBaseDatos(f"No se pudo abrir {ruta}: {error}") from error
    with closing(conexion):
        yield conexion


def _expandir_evaluacion(detalle_json: str | None) -> dict[str, Any]:
    """Convierte el JSON guardado de una evaluación en columnas planas."""
    detalle = json.loads(detalle_json) if detalle_json else {}
    inusual = detalle.get("dato_inusual") or {}
    volatilidad = detalle.get("volatilidad") or {}
    patron = detalle.get("patron_repetitivo") or {}
    return {
        "valor_anterior": detalle.get("valor_anterior"),
        "variacion": detalle.get("variacion"),
        "unidad_variacion": detalle.get("unidad_variacion"),
        "direccion": detalle.get("direccion"),
        "intervalo_magnitud": detalle.get("intervalo_magnitud"),
        "cumple_meta": detalle.get("cumple_meta"),
        "tendencia": detalle.get("tendencia"),
        "estable": detalle.get("estable"),
        "volatilidad": volatilidad.get("clasificacion"),
        "razon_volatilidad": volatilidad.get("razon"),
        "dato_inusual": inusual.get("es_inusual"),
        "z": inusual.get("z"),
        "patron_repetitivo": patron.get("detectado"),
        "descripcion_patron": patron.get("descripcion"),
        "criticidad": detalle.get("criticidad", "no_registrada"),
        "prioridad_revision": detalle.get("prioridad_revision"),
        "motivos": " ".join(detalle.get("motivos", [])),
        "accion_recomendada": detalle.get("accion_recomendada"),
        "detalle": detalle,
    }


def _con_evaluacion(filas: pd.DataFrame) -> pd.DataFrame:
    """Añade al DataFrame las columnas que salen de 'detalle_json'."""
    if filas.empty:
        return filas.drop(columns=["detalle_json"], errors="ignore")
    expandidas = pd.DataFrame([_expandir_evaluacion(d) for d in filas["detalle_json"]], index=filas.index)
    # Sí/No/sin dato: se guardan como bool de Python o None (pandas los volvería bool de NumPy).
    for columna in COLUMNAS_SI_NO:
        expandidas[columna] = pd.Series([None if pd.isna(v) else bool(v) for v in expandidas[columna]],
                                        index=filas.index, dtype=object)
    return pd.concat([filas.drop(columns=["detalle_json"]), expandidas], axis=1)


# Última versión de cada (kpi, fecha): la que vale si el dato fue corregido en la fuente.
_ES_ULTIMA_VERSION = ("o.version = (SELECT MAX(v.version) FROM observaciones v "
                      "WHERE v.kpi_id = o.kpi_id AND v.fecha_referencia = o.fecha_referencia)")
_ULTIMA_EVALUACION = "e.id = (SELECT MAX(x.id) FROM evaluaciones x WHERE x.observacion_id = o.id)"


def estado_actual(conexion: sqlite3.Connection) -> pd.DataFrame:
    """Una fila por KPI con su periodo más reciente y la evaluación de ese periodo."""
    filas = pd.read_sql_query(
        f"""
        SELECT o.kpi_id, o.canal, o.nombre_kpi, o.tipo_periodo, o.unidad, o.fecha_referencia,
               o.valor, o.meta, o.nodo_id, o.registrado_en, o.ultima_vez_visto,
               e.nivel_alerta, e.es_significativo, e.evaluado_en, e.detalle_json
        FROM observaciones o
        LEFT JOIN evaluaciones e ON {_ULTIMA_EVALUACION}
        WHERE {_ES_ULTIMA_VERSION}
          AND o.fecha_referencia = (SELECT MAX(m.fecha_referencia) FROM observaciones m WHERE m.kpi_id = o.kpi_id)
        ORDER BY o.canal, o.kpi_id
        """,
        conexion,
    )
    return _con_evaluacion(filas)


def historial_kpi(conexion: sqlite3.Connection, kpi_id: str) -> pd.DataFrame:
    """Serie completa de un KPI (una fila por periodo) con su evaluación y si se notificó."""
    filas = pd.read_sql_query(
        f"""
        SELECT o.fecha_referencia, o.valor, o.meta, o.version, e.nivel_alerta, e.detalle_json,
               EXISTS (SELECT 1 FROM notificaciones n WHERE n.id_evaluacion = e.id) AS notificada
        FROM observaciones o
        LEFT JOIN evaluaciones e ON {_ULTIMA_EVALUACION}
        WHERE o.kpi_id = ? AND {_ES_ULTIMA_VERSION}
        ORDER BY o.fecha_referencia
        """,
        conexion,
        params=(kpi_id,),
    )
    filas = _con_evaluacion(filas)
    if not filas.empty:
        filas["fecha_referencia"] = pd.to_datetime(filas["fecha_referencia"])
        filas["notificada"] = filas["notificada"].astype(bool)
    return filas


def alertas(conexion: sqlite3.Connection) -> pd.DataFrame:
    """Todas las evaluaciones con nivel distinto de 'sin_alerta', notificadas o no."""
    filas = pd.read_sql_query(
        """
        SELECT e.id AS id_evaluacion, e.kpi_id, o.canal, o.nombre_kpi, o.unidad, e.fecha_referencia,
               e.nivel_alerta, e.es_significativo, e.evaluado_en, e.detalle_json,
               o.valor, o.meta, o.version, o.nodo_id,
               n.clave, n.id_lote, n.escrito_en, n.estado_revision
        FROM evaluaciones e
        JOIN observaciones o ON o.id = e.observacion_id
        LEFT JOIN notificaciones n ON n.id_evaluacion = e.id
        WHERE e.nivel_alerta != 'sin_alerta'
        ORDER BY e.id DESC
        """,
        conexion,
    )
    filas = _con_evaluacion(filas)
    if filas.empty:
        return filas
    filas["es_significativo"] = filas["es_significativo"].astype(bool)
    filas["estado_notificacion"] = [
        "notificada" if pd.notna(clave) else ("significativa sin notificar" if significativa else "bajo el umbral")
        for clave, significativa in zip(filas["clave"], filas["es_significativo"])
    ]
    filas["estado_revision"] = filas["estado_revision"].fillna("no aplica")
    return filas


def actividad_nodos(conexion: sqlite3.Connection) -> pd.DataFrame:
    """Qué nodos hijos han enviado datos, cuántos mensajes y cuándo fue el último."""
    return pd.read_sql_query(
        """
        SELECT nodo_id, tipo_fuente, fuente, COUNT(*) AS mensajes,
               SUM(n_observaciones) AS observaciones_recibidas, MAX(recibido_en) AS ultimo_mensaje
        FROM mensajes
        GROUP BY nodo_id, tipo_fuente, fuente
        ORDER BY ultimo_mensaje DESC
        """,
        conexion,
    )


# ---------------------------------------------------------------- simulación Monte Carlo

def cargar_simulacion(ruta: Path) -> pd.DataFrame:
    """Frecuencias de la simulación (referencia exploratoria, no probabilidades garantizadas)."""
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    filas = [{"direccion": direccion, "intervalo": intervalo, "porcentaje": porcentaje}
             for direccion, valores in datos["frecuencias"].items()
             for intervalo, porcentaje in valores.items()]
    return pd.DataFrame(filas)


def distribucion_observada(variaciones: pd.Series, limites: tuple[float, ...] = LIMITES_SIMULACION) -> pd.DataFrame:
    """Reparte las variaciones observadas en los mismos intervalos de la simulación (% del total)."""
    variaciones = variaciones.dropna()
    variaciones = variaciones[variaciones != 0]  # la simulación no tiene la categoría "sin cambio"
    etiquetas = etiquetas_intervalos(limites)
    conteo = {(d, e): 0 for d in ("caida", "subida") for e in etiquetas}
    for valor in variaciones:
        conteo[("caida" if valor < 0 else "subida", etiquetas[indice_magnitud(abs(valor), limites)])] += 1
    total = len(variaciones)
    return pd.DataFrame([
        {"direccion": d, "intervalo": e, "observaciones": n, "porcentaje": round(n / total * 100, 2) if total else 0.0}
        for (d, e), n in conteo.items()
    ])
