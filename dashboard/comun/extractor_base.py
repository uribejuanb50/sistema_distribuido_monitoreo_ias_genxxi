"""Plantilla (clase abstracta) que todo extractor de un nodo hijo debe cumplir.

Un nodo hijo solo necesita una clase que herede de ExtractorBase e implemente
`fuente` y `extraer()`. El main.py del nodo no cambia, sin importar si la fuente es
un Excel, un CSV, una API o una base de datos.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from comun.contrato import Observacion


class ErrorExtraccion(Exception):
    """Error controlado al leer la fuente (no existe, está bloqueada, formato inesperado...).

    El nodo sigue vivo: avisa al padre que está "degradado" y reintenta en el próximo ciclo.
    """


class ErrorServicio(ErrorExtraccion):
    """El servicio de la fuente se cayó (navegador cerrado, sesión vencida, reporte que no carga...).

    Si el nodo tiene "cerrar_ante_error": true, avisa al padre que está "caido" y se cierra.
    """


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

    def captura_cruda(self) -> dict | None:
        """Opcional: los valores crudos de la última lectura (payload de un mensaje "captura").

        main.py la envía al padre después de cada extracción, aunque la interpretación falle.
        """
        return None

    def cerrar(self) -> None:
        """Libera recursos (navegador, conexiones). main.py lo llama al terminar."""
