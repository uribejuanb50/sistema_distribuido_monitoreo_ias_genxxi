"""Pruebas del extractor de la plantilla de nodo (CSV): datos faltantes y duplicados."""
import pytest

from comun.extractor_base import ErrorExtraccion
from conftest import RAIZ, cargar_modulo

plantilla = cargar_modulo("extraccion_plantilla", RAIZ / "nodos_hijo" / "_plantilla_nodo" / "extraccion.py")

ENCABEZADO = "kpi_id,canal,nombre_kpi,fecha_referencia,tipo_periodo,valor,unidad,meta\n"


def escribir_csv(tmp_path, filas: str):
    ruta = tmp_path / "datos.csv"
    ruta.write_text(ENCABEZADO + filas, encoding="utf-8")
    return plantilla.ExtractorCSV(ruta)


def test_lee_el_csv_de_ejemplo():
    observaciones = plantilla.ExtractorCSV(RAIZ / "datos" / "ejemplo" / "nps_viki_ficticio.csv").extraer()
    assert len(observaciones) == 12 and observaciones[0].canal == "VIKI"


def test_descarta_filas_con_datos_faltantes_o_invalidos(tmp_path):
    extractor = escribir_csv(tmp_path, (
        "k,VIKI,NPS,2026-01-01,mensual,40,puntos,45\n"
        "k,VIKI,NPS,2026-02-01,mensual,,puntos,45\n"        # valor faltante
        "k,VIKI,NPS,fecha-mala,mensual,41,puntos,45\n"      # fecha inválida
        "k,VIKI,NPS,2026-04-01,mensual,42,puntos,\n"        # meta vacía: permitido
    ))
    observaciones = extractor.extraer()
    assert [o.fecha_referencia for o in observaciones] == ["2026-01-01", "2026-04-01"]
    assert observaciones[1].meta is None


def test_descarta_duplicados(tmp_path):
    extractor = escribir_csv(tmp_path, (
        "k,VIKI,NPS,2026-01-01,mensual,40,puntos,45\n"
        "k,VIKI,NPS,2026-01-01,mensual,99,puntos,45\n"
    ))
    observaciones = extractor.extraer()
    assert len(observaciones) == 1 and observaciones[0].valor == 40


def test_columnas_faltantes(tmp_path):
    ruta = tmp_path / "malo.csv"
    ruta.write_text("kpi_id,valor\nk,1\n", encoding="utf-8")
    with pytest.raises(ErrorExtraccion, match="Faltan columnas"):
        plantilla.ExtractorCSV(ruta).extraer()
