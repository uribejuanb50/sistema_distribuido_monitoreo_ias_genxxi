"""Guardado del nodo padre.

- Almacen: historial completo en SQLite (mensajes, observaciones con versiones,
  evaluaciones y notificaciones emitidas).
- escribir_json_salida: escribe el JSON que vigila Power Automate. Se escribe de forma
  "atómica" (archivo temporal + reemplazo) para que nunca se lea un archivo a medias.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from comun.contrato import Mensaje, Observacion, ahora_iso

if TYPE_CHECKING:
    from pensamiento import Evaluacion

log = logging.getLogger(__name__)

VERSION_SALIDA = "1.0"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS mensajes (
    id_mensaje       TEXT PRIMARY KEY,
    nodo_id          TEXT NOT NULL,
    tipo_fuente      TEXT NOT NULL,
    fuente           TEXT NOT NULL,
    version_esquema  TEXT NOT NULL,
    secuencia        INTEGER NOT NULL,
    enviado_en       TEXT NOT NULL,
    recibido_en      TEXT NOT NULL,
    n_observaciones  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS observaciones (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    kpi_id            TEXT NOT NULL,
    canal             TEXT NOT NULL,
    nombre_kpi        TEXT NOT NULL,
    fecha_referencia  TEXT NOT NULL,            -- fecha A LA QUE CORRESPONDE el dato
    tipo_periodo      TEXT NOT NULL,
    valor             REAL NOT NULL,
    unidad            TEXT NOT NULL,
    meta              REAL,
    atributos_json    TEXT NOT NULL,
    huella            TEXT NOT NULL,
    version           INTEGER NOT NULL,         -- 1 = original; >1 = el valor cambió en la fuente
    nodo_id           TEXT NOT NULL,
    id_mensaje        TEXT NOT NULL REFERENCES mensajes(id_mensaje),
    fecha_captura     TEXT NOT NULL,            -- CUÁNDO lo leyó el nodo hijo
    registrado_en     TEXT NOT NULL,            -- cuándo lo guardó el padre
    ultima_vez_visto  TEXT NOT NULL,
    UNIQUE (kpi_id, fecha_referencia, version)
);

CREATE TABLE IF NOT EXISTS evaluaciones (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    observacion_id    INTEGER NOT NULL REFERENCES observaciones(id),
    kpi_id            TEXT NOT NULL,
    fecha_referencia  TEXT NOT NULL,
    nivel_alerta      TEXT NOT NULL,
    es_significativo  INTEGER NOT NULL,
    evaluado_en       TEXT NOT NULL,
    detalle_json      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notificaciones (
    clave             TEXT PRIMARY KEY,         -- evita notificar dos veces la misma condición
    id_lote           TEXT NOT NULL,
    id_evaluacion     INTEGER NOT NULL REFERENCES evaluaciones(id),
    kpi_id            TEXT NOT NULL,
    fecha_referencia  TEXT NOT NULL,
    nivel_alerta      TEXT NOT NULL,
    escrito_en        TEXT NOT NULL,
    ruta_json         TEXT NOT NULL,
    estado_revision   TEXT NOT NULL DEFAULT 'pendiente'
);
"""


@dataclass(frozen=True)
class ResultadoGuardado:
    observacion: Observacion
    estado: str          # nueva | revision | repetida
    id_observacion: int


class Almacen:
    """Acceso a la base de datos SQLite del nodo padre."""

    def __init__(self, ruta_db: Path) -> None:
        ruta_db.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(ruta_db)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA foreign_keys=ON")
        self._con.executescript(ESQUEMA)

    def cerrar(self) -> None:
        self._con.close()

    # ------------------------------------------------------------ mensajes y observaciones

    def registrar_mensaje(self, mensaje: Mensaje) -> bool:
        """Guarda el header. Devuelve False si el mensaje ya se había recibido."""
        enc = mensaje.encabezado
        try:
            with self._con:
                self._con.execute(
                    "INSERT INTO mensajes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (enc.id_mensaje, enc.nodo_id, enc.tipo_fuente, enc.fuente, enc.version_esquema,
                     enc.secuencia, enc.enviado_en, ahora_iso(), len(mensaje.observaciones)),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def guardar_observaciones(self, mensaje: Mensaje) -> list[ResultadoGuardado]:
        """Guarda cada observación: nueva, revisión (cambió el valor) o repetida (sin cambios)."""
        enc = mensaje.encabezado
        ahora = ahora_iso()
        resultados = []
        with self._con:
            for obs in mensaje.observaciones:
                huella = obs.huella()
                previa = self._con.execute(
                    "SELECT id, huella, version FROM observaciones WHERE kpi_id = ? AND fecha_referencia = ? "
                    "ORDER BY version DESC LIMIT 1",
                    (obs.kpi_id, obs.fecha_referencia),
                ).fetchone()
                if previa is not None and previa["huella"] == huella:
                    self._con.execute("UPDATE observaciones SET ultima_vez_visto = ? WHERE id = ?",
                                      (ahora, previa["id"]))
                    resultados.append(ResultadoGuardado(obs, "repetida", previa["id"]))
                    continue
                version = 1 if previa is None else previa["version"] + 1
                cursor = self._con.execute(
                    "INSERT INTO observaciones (kpi_id, canal, nombre_kpi, fecha_referencia, tipo_periodo, valor, "
                    "unidad, meta, atributos_json, huella, version, nodo_id, id_mensaje, fecha_captura, "
                    "registrado_en, ultima_vez_visto) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (obs.kpi_id, obs.canal, obs.nombre_kpi, obs.fecha_referencia, obs.tipo_periodo, obs.valor,
                     obs.unidad, obs.meta, json.dumps(obs.atributos, ensure_ascii=False), huella, version,
                     enc.nodo_id, enc.id_mensaje, obs.fecha_captura, ahora, ahora),
                )
                estado = "nueva" if previa is None else "revision"
                resultados.append(ResultadoGuardado(obs, estado, cursor.lastrowid))
        return resultados

    def obtener_serie(self, kpi_id: str, hasta: str = "9999-12-31") -> list[tuple[str, float]]:
        """Serie histórica (fecha, valor) usando la última versión de cada fecha."""
        filas = self._con.execute(
            "SELECT o.fecha_referencia, o.valor FROM observaciones o "
            "WHERE o.kpi_id = ? AND o.fecha_referencia <= ? AND o.version = ("
            "  SELECT MAX(v.version) FROM observaciones v "
            "  WHERE v.kpi_id = o.kpi_id AND v.fecha_referencia = o.fecha_referencia) "
            "ORDER BY o.fecha_referencia",
            (kpi_id, hasta),
        ).fetchall()
        return [(fila["fecha_referencia"], fila["valor"]) for fila in filas]

    def ultima_fecha(self, kpi_id: str) -> str | None:
        fila = self._con.execute("SELECT MAX(fecha_referencia) AS f FROM observaciones WHERE kpi_id = ?",
                                 (kpi_id,)).fetchone()
        return fila["f"]

    # ------------------------------------------------------------ evaluaciones y notificaciones

    def guardar_evaluacion(self, obs: Observacion, evaluacion: Evaluacion, id_observacion: int) -> int:
        with self._con:
            cursor = self._con.execute(
                "INSERT INTO evaluaciones (observacion_id, kpi_id, fecha_referencia, nivel_alerta, "
                "es_significativo, evaluado_en, detalle_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (id_observacion, obs.kpi_id, obs.fecha_referencia, evaluacion.nivel_alerta,
                 int(evaluacion.es_significativo), ahora_iso(),
                 json.dumps(evaluacion.a_dict(), ensure_ascii=False)),
            )
        return cursor.lastrowid

    def notificacion_existe(self, clave: str) -> bool:
        return self._con.execute("SELECT 1 FROM notificaciones WHERE clave = ?", (clave,)).fetchone() is not None

    def registrar_notificaciones(self, alertas: list[dict[str, Any]], id_lote: str, ruta_json: str) -> None:
        ahora = ahora_iso()
        with self._con:
            self._con.executemany(
                "INSERT OR IGNORE INTO notificaciones (clave, id_lote, id_evaluacion, kpi_id, fecha_referencia, "
                "nivel_alerta, escrito_en, ruta_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(a["clave_notificacion"], id_lote, a["id_evaluacion"], a["kpi_id"], a["fecha_referencia"],
                  a["nivel_alerta"], ahora, ruta_json) for a in alertas],
            )


# ---------------------------------------------------------------- JSON para Power Automate

def construir_alerta(obs: Observacion, evaluacion: Evaluacion, nodo_id: str,
                     id_evaluacion: int) -> dict[str, Any]:
    """Arma el registro de alerta que irá al JSON de salida."""
    return {
        "clave_notificacion": f"{obs.kpi_id}|{obs.fecha_referencia}|{evaluacion.nivel_alerta}|{obs.huella()[:12]}",
        "id_evaluacion": id_evaluacion,
        "kpi_id": obs.kpi_id,
        "nombre_kpi": obs.nombre_kpi,
        "canal": obs.canal,
        "fecha_referencia": obs.fecha_referencia,
        "tipo_periodo": obs.tipo_periodo,
        "nivel_alerta": evaluacion.nivel_alerta,
        "prioridad_revision": evaluacion.prioridad_revision,
        "criticidad": evaluacion.criticidad,
        "valor_actual": evaluacion.valor_actual,
        "valor_anterior": evaluacion.valor_anterior,
        "meta": evaluacion.meta,
        "unidad": obs.unidad,
        "variacion": evaluacion.variacion,
        "unidad_variacion": evaluacion.unidad_variacion,
        "intervalo_magnitud": evaluacion.intervalo_magnitud,
        "cumple_meta": evaluacion.cumple_meta,
        "motivos": list(evaluacion.motivos),
        "senales": evaluacion.senales,
        "accion_recomendada": evaluacion.accion_recomendada,
        "detectado_en": ahora_iso(),
        "nodo_origen": nodo_id,
        "estado_revision": "pendiente",
    }


def escribir_json_salida(ruta: Path, alertas: list[dict[str, Any]], intentos: int = 3) -> str:
    """Escribe el lote de alertas y devuelve su id. Reintenta si OneDrive bloquea el archivo."""
    id_lote = uuid.uuid4().hex
    contenido = {
        "version_esquema": VERSION_SALIDA,
        "id_lote": id_lote,
        "generado_en": ahora_iso(),
        "origen": "nodo_padre",
        "total_alertas": len(alertas),
        "alertas": alertas,
    }
    texto = json.dumps(contenido, ensure_ascii=False, indent=2)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(ruta.name + ".tmp")
    for intento in range(1, intentos + 1):
        try:
            temporal.write_text(texto, encoding="utf-8")
            os.replace(temporal, ruta)
            return id_lote
        except OSError:
            if intento == intentos:
                temporal.unlink(missing_ok=True)
                raise
            time.sleep(0.5 * intento)
    raise AssertionError("inalcanzable")
