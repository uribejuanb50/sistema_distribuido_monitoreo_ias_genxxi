"""Configuración común de pytest: rutas de importación y datos ficticios."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
for ruta in (RAIZ / "scripts", RAIZ / "nodos_hijo" / "nodo_excel_cumplimiento",
             RAIZ / "nodo_padre" / "nodo_padre", RAIZ):
    if str(ruta) not in sys.path:
        sys.path.insert(0, str(ruta))

from comun.contrato import Encabezado, Mensaje, Observacion  # noqa: E402
from generar_excel_ficticio import escribir_libro, generar_datos  # noqa: E402


def cargar_modulo(nombre: str, ruta: Path):
    """Importa un archivo con un nombre único (main.py y extraccion.py se repiten entre nodos)."""
    especificacion = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    sys.modules[nombre] = modulo  # las dataclasses necesitan encontrar su módulo registrado
    especificacion.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def excel_ficticio(tmp_path: Path) -> Path:
    return escribir_libro(tmp_path / "cumplimiento_ficticio.xlsx", generar_datos())


def crear_observacion(fecha: str, valor: float, kpi_id: str = "viki.nps_general_call_center",
                      meta: float | None = 45.0, unidad: str = "puntos") -> Observacion:
    return Observacion(kpi_id=kpi_id, canal="VIKI", nombre_kpi="NPS ficticio", fecha_referencia=fecha,
                       tipo_periodo="mensual", valor=valor, unidad=unidad, meta=meta)


def crear_mensaje(valores: list[float], kpi_id: str = "viki.nps_general_call_center",
                  meta: float | None = 45.0) -> Mensaje:
    """Un mensaje con una serie mensual desde 2026-01."""
    observaciones = tuple(crear_observacion(f"2026-{mes:02d}-01", valor, kpi_id, meta)
                          for mes, valor in enumerate(valores, start=1))
    return Mensaje(Encabezado(nodo_id="nodo_prueba", tipo_fuente="prueba", fuente="memoria"), observaciones)
