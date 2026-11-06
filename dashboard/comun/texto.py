"""Normalización de textos compartida (etiquetas de Excel, encabezados de Power BI, kpi_id)."""
from __future__ import annotations

import re
import unicodedata
from typing import Any


def normalizar_texto(texto: Any) -> str:
    """'  LÍNEA   Base ' -> 'linea base' (sin tildes, minúsculas, espacios simples)."""
    sin_tildes = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sin_tildes).strip().lower()


def a_slug(texto: str) -> str:
    """'Tv + Voz' -> 'tv_voz' (para construir kpi_id estables)."""
    return re.sub(r"[^a-z0-9]+", "_", normalizar_texto(texto)).strip("_")
