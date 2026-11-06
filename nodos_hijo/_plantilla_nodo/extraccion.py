"""Extracción de la PLANTILLA de nodo hijo: lee un CSV ya ordenado (una fila por observación).

Para crear un nodo nuevo:
1. Copia esta carpeta (p. ej. nodos_hijo/nodo_mi_fuente/).
2. Edita config.json: un 'nodo_id' único y la ruta de tu fuente.
3. Reescribe ExtractorCSV.extraer() (o crea tu propia clase) para que devuelva
   list[Observacion]. main.py NO se toca.

Columnas esperadas del CSV:
    kpi_id, canal, nombre_kpi, fecha_referencia, tipo_periodo, valor, unidad, meta
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

from comun.contrato import Observacion, ahora_iso
from comun.extractor_base import ErrorExtraccion, ExtractorBase
from comun.utilidades import resolver_ruta

log = logging.getLogger(__name__)

COLUMNAS_REQUERIDAS = ("kpi_id", "canal", "nombre_kpi", "fecha_referencia", "tipo_periodo", "valor", "unidad")


class ExtractorCSV(ExtractorBase):
    tipo_fuente = "csv"

    def __init__(self, ruta: Path, separador: str = ",") -> None:
        self.ruta = ruta
        self.separador = separador

    @property
    def fuente(self) -> str:
        return self.ruta.name

    def extraer(self) -> list[Observacion]:
        if not self.ruta.exists():
            raise ErrorExtraccion(f"No existe el archivo: {self.ruta}")
        try:
            with open(self.ruta, newline="", encoding="utf-8-sig") as archivo:
                lector = csv.DictReader(archivo, delimiter=self.separador)
                faltantes = [c for c in COLUMNAS_REQUERIDAS if c not in (lector.fieldnames or [])]
                if faltantes:
                    raise ErrorExtraccion(f"Faltan columnas en el CSV: {faltantes}")
                filas = list(lector)
        except OSError as error:
            raise ErrorExtraccion(f"No se pudo leer {self.ruta}: {error}") from error

        fecha_captura = ahora_iso()
        observaciones: list[Observacion] = []
        vistos: set[tuple[str, str]] = set()
        for numero_fila, fila in enumerate(filas, start=2):  # fila 1 = encabezados
            try:
                meta = (fila.get("meta") or "").strip()
                obs = Observacion(
                    kpi_id=fila["kpi_id"].strip(),
                    canal=fila["canal"].strip(),
                    nombre_kpi=fila["nombre_kpi"].strip(),
                    fecha_referencia=fila["fecha_referencia"].strip(),
                    tipo_periodo=fila["tipo_periodo"].strip(),
                    valor=float(fila["valor"]),
                    unidad=fila["unidad"].strip(),
                    meta=float(meta) if meta else None,
                    fecha_captura=fecha_captura,
                    atributos={"fila_origen": numero_fila},
                )
            except (ValueError, TypeError, AttributeError) as error:  # incluye ErrorContrato
                log.warning("Fila %d descartada (dato faltante o inválido): %s", numero_fila, error)
                continue
            clave = (obs.kpi_id, obs.fecha_referencia)
            if clave in vistos:
                log.warning("Fila %d duplicada %s: se conserva la primera aparición", numero_fila, clave)
                continue
            vistos.add(clave)
            observaciones.append(obs)
        return observaciones


def crear_extractor(config: dict[str, Any]) -> ExtractorCSV:
    """Punto de entrada que usa main.py: construye el extractor desde config.json."""
    return ExtractorCSV(resolver_ruta(config["ruta_archivo"]), config.get("separador", ","))
