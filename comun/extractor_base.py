"""Plantilla (clase abstracta) que todo extractor de un nodo hijo debe cumplir.

Un nodo hijo solo necesita una clase que herede de ExtractorBase e implemente
`fuente` y `extraer()`. El main.py del nodo no cambia, sin importar si la fuente es
un Excel, un CSV, una API o una base de datos.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from comun.contrato import Observacion


class ErrorExtraccion(Exception):
    """Error controlado al leer la fuente (no existe, está bloqueada, formato inesperado...)."""


class ExtractorBase(ABC):
    tipo_fuente: str = "desconocido"

    @property
    @abstractmethod
    def fuente(self) -> str:
        """Nombre legible de la fuente (archivo, tabla, URL...)."""

    @abstractmethod
    def extraer(self) -> list[Observacion]:
        """Lee la fuente y devuelve observaciones que cumplen el contrato.

        Debe lanzar ErrorExtraccion ante problemas esperables de la fuente.
        """
