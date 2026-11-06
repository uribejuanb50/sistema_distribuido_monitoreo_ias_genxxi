"""Pruebas del nodo Power BI: números, interpretación con el mapa, validaciones y Selenium local."""
import json
import sys
from datetime import date

import pytest

from captura_powerbi_ficticia import MAPA, captura_consola, captura_nodo, html_reporte
from comun.extractor_base import ErrorExtraccion
from conftest import RAIZ, cargar_modulo

DIR_POWERBI = RAIZ / "nodos_hijo" / "nodo_powerbi_viki"
if str(DIR_POWERBI) not in sys.path:
    sys.path.append(str(DIR_POWERBI))  # al final: no tapa el extraccion.py del nodo Excel

pbi = cargar_modulo("extraccion_powerbi", DIR_POWERBI / "extraccion.py")


def interpretar_consola(**kwargs):
    return pbi.interpretar(pbi.normalizar_captura(captura_consola(**kwargs), "prueba"), MAPA)


def por_kpi(resultado):
    return {obs.kpi_id: obs for obs in resultado.observaciones}


# --- conversión de textos -------------------------------------------------------------------

@pytest.mark.parametrize("texto, esperado", [
    ("2.363.989", 2363989), ("3.752", 3752), ("8,6 %", 8.6), ("-5,2 %", -5.2), ("0,0 %", 0.0), ("1,4", 1.4),
    ("37,8", 37.8), ("09:42", 582), ("10:20", 620), ("1:02:03", 3723), ("150", 150),
    ("Semana 1", None), ("0 mil", None), ("Total", None), ("", None), ("1.23", None),
])
def test_a_numero(texto, esperado):
    assert pbi.a_numero(texto) == esperado


def test_fecha_y_duplicados():
    assert pbi.leer_fecha("Actualizado: miércoles, 28 de octubre de 2026") == date(2026, 10, 28)
    assert pbi.leer_fecha("sin fecha") is None
    assert pbi.es_duplicado("20 mil20 mil") and pbi.sin_duplicar("FECHAFECHA") == "FECHA"
    assert not pbi.es_duplicado("9.711")


# --- interpretación -------------------------------------------------------------------------

def test_captura_de_consola_se_interpreta_y_valida():
    resultado = interpretar_consola()
    assert resultado.errores == []
    assert resultado.fecha_actualizacion == date(2026, 10, 28)
    obs = por_kpi(resultado)
    assert obs["viki.mtd.total.usuarios"].valor == 1800
    assert obs["viki.mtd.total.usuarios"].tipo_periodo == "acumulado"
    assert obs["viki.mtd.total.usuarios"].fecha_referencia == "2026-10-28"
    assert obs["viki.mtd.telefonico.usabilidad"].unidad == "porcentaje"
    assert obs["viki.mtd.general.licencias_asignadas"].valor == 2000
    assert obs["viki.mtd.total.tmo"].valor == 583 and obs["viki.mtd.total.tmo"].unidad == "segundos"
    assert obs["viki.mtd.antiguedad.1_0_3_meses.acciones"].valor == 5000


def test_semanas_con_fechas_y_sin_conteos_parciales():
    completas = [o for o in interpretar_consola().observaciones if o.kpi_id.startswith("viki.semanal.")]
    assert {o.fecha_referencia for o in completas} == {"2026-10-01", "2026-10-08", "2026-10-15", "2026-10-22"}
    assert not any("var" in o.kpi_id for o in completas)  # Var_Sesiones se ignora (el cerebro la calcula)

    a_medias = interpretar_consola(dia_actualizacion=25)  # la semana 4 (22-28) aún no termina
    semana4 = {o.kpi_id for o in a_medias.observaciones if o.fecha_referencia == "2026-10-22"}
    assert "viki.semanal.nps" in semana4 and "viki.semanal.sesiones_llamada" not in semana4
    assert a_medias.errores == []


def test_tabla_sin_valores_genera_aviso_y_datos_personales_se_excluyen():
    resultado = interpretar_consola()
    assert any("gerencias_viki" in aviso for aviso in resultado.avisos)
    assert "EXCLUIDO" in resultado.asignaciones["bloque 128"]
    assert "bloque 127" not in resultado.asignaciones  # no identificado: no se envía


def test_bloques_intercambiados_rompen_las_validaciones():
    normal = interpretar_consola()
    textos = {59: None, 60: None}
    for visual in captura_consola():
        for bloque in textos:
            if visual["Sección / Visual"].endswith(f"Bloque {bloque}"):
                textos[bloque] = visual["Valores Extraídos"][0]
    alterado = interpretar_consola(cambios={"59": textos[60], "60": textos[59]})  # usuarios <-> sesiones
    assert normal.errores == [] and alterado.errores


def test_tipo_de_valor_incorrecto_se_detecta():
    resultado = interpretar_consola(cambios={"52": "2.967"})  # se esperaba un porcentaje
    assert any("bloque 52" in error and "porcentaje" in error for error in resultado.errores)


def test_bloque_faltante_se_detecta():
    captura = [v for v in captura_consola() if v["Sección / Visual"] != "Visual / Bloque 61"]
    resultado = pbi.interpretar(pbi.normalizar_captura(captura, "prueba"), MAPA)
    assert any("bloque 61" in error for error in resultado.errores)


def test_sin_fecha_de_actualizacion_falla():
    captura = [v for v in captura_consola() if v["Sección / Visual"] != "Visual / Bloque 4"]
    with pytest.raises(ErrorExtraccion, match="Actualizado"):
        pbi.interpretar(pbi.normalizar_captura(captura, "prueba"), MAPA)


def test_tabla_con_filas_aria_genera_kpi_por_gerencia():
    resultado = pbi.interpretar(pbi.normalizar_captura(captura_nodo(), "prueba"), MAPA)
    assert resultado.errores == []
    obs = por_kpi(resultado)
    assert obs["viki.mtd.gerencia.gerencia_norte.usuarios"].valor == 1100
    assert obs["viki.mtd.gerencia.gerencia_sur.acciones_x_sesion"].valor == 1.4
    assert any("gerencias_viki: Total usuarios" in v[0] or "'gerencias_viki': Total usuarios" in v[0]
               for v in resultado.validaciones)


def test_extractor_sin_conexion_lee_el_txt_de_la_consola(tmp_path):
    ruta = tmp_path / "captura.txt"
    ruta.write_text(json.dumps(captura_consola(), ensure_ascii=False) + "\n\nInyección por consola:\nlet x = 1;",
                    encoding="utf-8")
    extractor = pbi.ExtractorPowerBI(MAPA, ruta_captura=ruta)
    observaciones = extractor.extraer()
    extractor.cerrar()
    assert len(observaciones) == len(interpretar_consola().observaciones)


def test_extractor_no_envia_si_fallan_validaciones(tmp_path):
    ruta = tmp_path / "captura.json"
    ruta.write_text(json.dumps(captura_consola(cambios={"52": "2.967"}), ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ErrorExtraccion, match="validaciones"):
        pbi.ExtractorPowerBI(MAPA, ruta_captura=ruta).extraer()


# --- Selenium con una página local ----------------------------------------------------------

def test_selenium_captura_pagina_local(tmp_path):
    navegador_mod = pytest.importorskip("navegador")
    pagina = tmp_path / "reporte.html"
    pagina.write_text(html_reporte(captura_nodo()), encoding="utf-8")
    navegador = navegador_mod.NavegadorPowerBI(pagina.as_uri(), carpeta_perfil=tmp_path / "perfil", visible=False,
                                               minimo_visuales=5, segundos_estable=1, espera_carga_segundos=20)
    try:
        datos = navegador.capturar()
    except ErrorExtraccion as error:  # sin Edge o sin driver en esta máquina
        pytest.skip(f"Navegador no disponible: {error}")
    finally:
        navegador.cerrar()
    resultado = pbi.interpretar(pbi.normalizar_captura(datos, "local"), MAPA)
    assert resultado.errores == []
    obs = por_kpi(resultado)
    assert obs["viki.mtd.total.usuarios"].valor == 1800
    assert obs["viki.mtd.gerencia.gerencia_norte.usuarios"].valor == 1100  # leída por roles ARIA
