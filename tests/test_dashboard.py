"""Pruebas del dashboard: consultas a SQLite, gráficos y la app completa (AppTest de Streamlit)."""
import sqlite3
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from comun.utilidades import cargar_json
from conftest import RAIZ, cargar_modulo, crear_mensaje
from guardado import Almacen
from pensamiento import ConfiguracionAnalisis

sys_path_dashboard = str(RAIZ / "dashboard")
import sys  # noqa: E402

if sys_path_dashboard not in sys.path:
    sys.path.insert(0, sys_path_dashboard)

import consultas  # noqa: E402
import graficos  # noqa: E402

padre = cargar_modulo("main_nodo_padre", RAIZ / "nodo_padre" / "nodo_padre" / "main.py")
SERIE_CON_ALERTA = [42.0, 43.5, 41.8, 44.2, 45.0, 44.6, 46.1, 46.4, 45.3, 44.0, 42.7, 41.2]


@pytest.fixture
def base_datos(tmp_path: Path) -> Path:
    """Base con un NPS que alerta en el último periodo y otro KPI que cumple."""
    ruta = tmp_path / "padre.sqlite3"
    config = cargar_json(RAIZ / "nodo_padre" / "nodo_padre" / "config.json")
    almacen = Almacen(ruta)
    ctx = padre.ContextoPadre(almacen=almacen, configuracion=ConfiguracionAnalisis.desde_dict(config["analisis"]),
                              ruta_json=tmp_path / "alertas.json")
    padre.procesar_mensaje(crear_mensaje(SERIE_CON_ALERTA).a_bytes(), ctx)
    padre.procesar_mensaje(crear_mensaje([50.0, 51.0, 52.0], kpi_id="otro.kpi_estable").a_bytes(), ctx)
    almacen.cerrar()
    return ruta


def test_estado_actual_una_fila_por_kpi(base_datos):
    with consultas.abrir(base_datos) as con:
        estado = consultas.estado_actual(con).set_index("kpi_id")
    assert list(estado.index) == ["otro.kpi_estable", "viki.nps_general_call_center"]
    nps = estado.loc["viki.nps_general_call_center"]
    assert nps["fecha_referencia"] == "2026-12-01" and nps["valor"] == 41.2
    assert nps["nivel_alerta"] == "media" and nps["criticidad"] == "critica"
    assert nps["tendencia"] == "descendente_sostenida" and nps["cumple_meta"] is False
    assert estado.loc["otro.kpi_estable", "nivel_alerta"] == "sin_alerta"


def test_historial_y_alertas(base_datos):
    with consultas.abrir(base_datos) as con:
        historial = consultas.historial_kpi(con, "viki.nps_general_call_center")
        alertas = consultas.alertas(con)
    assert len(historial) == 12 and historial["notificada"].sum() == 1
    assert historial["variacion"].iloc[-1] == pytest.approx(-1.5)
    assert set(alertas["estado_notificacion"]) <= {"notificada", "significativa sin notificar", "bajo el umbral"}
    assert (alertas["estado_notificacion"] == "notificada").sum() == 1
    assert (alertas.loc[alertas["estado_notificacion"] == "notificada", "estado_revision"] == "pendiente").all()


def test_conexion_es_solo_lectura(base_datos):
    with consultas.abrir(base_datos) as con, pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM observaciones")


def test_base_inexistente(tmp_path):
    with pytest.raises(consultas.ErrorBaseDatos), consultas.abrir(tmp_path / "no_existe.sqlite3"):
        pass


def test_distribucion_observada_usa_intervalos_de_la_simulacion():
    distribucion = consultas.distribucion_observada(pd.Series([-0.5, -1.0, 2.0, 0.0, None, 7.0]))
    filas = distribucion.set_index(["direccion", "intervalo"])["observaciones"]
    assert filas[("caida", "<1")] == 1 and filas[("caida", "[1, 2)")] == 1
    assert filas[("subida", "[2, 3)")] == 1 and filas[("subida", ">=5")] == 1
    assert distribucion["porcentaje"].sum() == pytest.approx(100)


def test_simulacion_suma_lo_esperado():
    simulacion = consultas.cargar_simulacion(RAIZ / "dashboard" / "simulacion_montecarlo.json")
    totales = simulacion.groupby("direccion")["porcentaje"].sum()
    assert totales["caida"] == pytest.approx(47.13) and totales["subida"] == pytest.approx(52.87)


def test_figuras_se_construyen(base_datos):
    with consultas.abrir(base_datos) as con:
        historial = consultas.historial_kpi(con, "viki.nps_general_call_center")
        estado = consultas.estado_actual(con)
    simulacion = consultas.cargar_simulacion(RAIZ / "dashboard" / "simulacion_montecarlo.json")
    for modo in ("claro", "oscuro"):
        evolucion = graficos.figura_evolucion(historial, "puntos", modo)
        assert {t.name for t in evolucion.data} >= {"Valor", "Meta", "Alerta media"}
        graficos.figura_variaciones(historial, "puntos", modo)
        graficos.figura_frecuencias(simulacion, consultas.distribucion_observada(historial["variacion"]), modo)
        graficos.figura_tendencias(estado, modo)


def test_app_renderiza_las_seis_vistas_sin_errores(base_datos, monkeypatch):
    monkeypatch.setenv("DASHBOARD_BD", str(base_datos))
    app = AppTest.from_file(str(RAIZ / "dashboard" / "app.py"), default_timeout=60)
    app.run()
    assert not app.exception
    assert [pestaña.label for pestaña in app.tabs] == ["Resumen", "Monitor de KPI", "Evolución histórica",
                                                      "Comportamiento", "Detalle de alertas",
                                                      "Historial de alertas"]
    metricas = {m.label: m.value for m in app.metric}
    assert metricas["KPI monitoreados"] == "2"
    assert metricas["Por debajo de la meta"] == "1"


def test_app_sin_base_de_datos_muestra_instrucciones(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_BD", str(tmp_path / "no_existe.sqlite3"))
    app = AppTest.from_file(str(RAIZ / "dashboard" / "app.py"), default_timeout=60)
    app.run()
    assert not app.exception
    assert "No existe la base de datos" in app.error[0].value
