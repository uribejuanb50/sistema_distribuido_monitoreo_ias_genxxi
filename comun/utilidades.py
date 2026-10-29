"""Utilidades compartidas: rutas del proyecto, lectura de configuración y logging."""
from __future__ import annotations

import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

RAIZ_PROYECTO = Path(__file__).resolve().parents[1]
DIR_LOGS = RAIZ_PROYECTO / "logs"
FORMATO_LOG = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def resolver_ruta(ruta: str | Path) -> Path:
    """Convierte una ruta relativa (a la raíz del proyecto) en absoluta."""
    ruta = Path(ruta)
    return ruta if ruta.is_absolute() else RAIZ_PROYECTO / ruta


def cargar_json(ruta: str | Path) -> dict[str, Any]:
    """Lee un archivo JSON de configuración."""
    with open(resolver_ruta(ruta), encoding="utf-8") as archivo:
        return json.load(archivo)


def sin_comentarios(datos: dict[str, Any]) -> dict[str, Any]:
    """Quita las claves que empiezan por '_' (se usan como comentarios en los JSON)."""
    return {clave: valor for clave, valor in datos.items() if not clave.startswith("_")}


def configurar_logging(nombre: str, nivel: int = logging.INFO) -> logging.Logger:
    """Envía los logs a la consola y a logs/<nombre>.log (con rotación)."""
    DIR_LOGS.mkdir(exist_ok=True)
    formato = logging.Formatter(FORMATO_LOG, "%Y-%m-%d %H:%M:%S")

    # En consolas de Windows algunos caracteres pueden fallar: se reemplazan en vez de romper.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    consola = logging.StreamHandler(sys.stdout)
    consola.setFormatter(formato)
    archivo = RotatingFileHandler(
        DIR_LOGS / f"{nombre}.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    archivo.setFormatter(formato)

    raiz = logging.getLogger()
    raiz.handlers.clear()
    raiz.setLevel(nivel)
    raiz.addHandler(consola)
    raiz.addHandler(archivo)
    return logging.getLogger(nombre)
