"""Pruebas del almacenamiento SQLite y del JSON de salida."""
import json

import pytest

from comun.contrato import Encabezado, Mensaje
from conftest import crear_mensaje, crear_observacion
from guardado import Almacen, escribir_json_salida


@pytest.fixture
def almacen(tmp_path):
    almacen = Almacen(tmp_path / "db" / "prueba.sqlite3")
    yield almacen
    almacen.cerrar()


def test_mensaje_duplicado_se_detecta(almacen):
    mensaje = crear_mensaje([40.0])
    assert almacen.registrar_mensaje(mensaje) is True
    assert almacen.registrar_mensaje(mensaje) is False


def test_observaciones_nuevas_repetidas_y_revisadas(almacen):
    primero = crear_mensaje([40.0, 41.0])
    almacen.registrar_mensaje(primero)
    assert [r.estado for r in almacen.guardar_observaciones(primero)] == ["nueva", "nueva"]

    repetido = crear_mensaje([40.0, 41.0])  # mismo contenido, otro id_mensaje
    almacen.registrar_mensaje(repetido)
    assert [r.estado for r in almacen.guardar_observaciones(repetido)] == ["repetida", "repetida"]

    corregido = crear_mensaje([40.0, 39.0])  # febrero cambió en la fuente
    almacen.registrar_mensaje(corregido)
    assert [r.estado for r in almacen.guardar_observaciones(corregido)] == ["repetida", "revision"]
    assert almacen.obtener_serie("viki.nps_general_call_center") == [("2026-01-01", 40.0), ("2026-02-01", 39.0)]


def test_serie_hasta_una_fecha_y_ultima_fecha(almacen):
    mensaje = crear_mensaje([40.0, 41.0, 42.0])
    almacen.registrar_mensaje(mensaje)
    almacen.guardar_observaciones(mensaje)
    assert len(almacen.obtener_serie("viki.nps_general_call_center", hasta="2026-02-01")) == 2
    assert almacen.ultima_fecha("viki.nps_general_call_center") == "2026-03-01"
    assert almacen.ultima_fecha("no.existe") is None


def test_observaciones_desordenadas_se_ordenan_por_fecha(almacen):
    obs = (crear_observacion("2026-03-01", 3.0), crear_observacion("2026-01-01", 1.0))
    mensaje = Mensaje(Encabezado(nodo_id="n", tipo_fuente="t", fuente="f"), obs)
    almacen.registrar_mensaje(mensaje)
    almacen.guardar_observaciones(mensaje)
    assert [v for _, v in almacen.obtener_serie("viki.nps_general_call_center")] == [1.0, 3.0]


def test_json_de_salida_atomico(tmp_path):
    ruta = tmp_path / "salida" / "alertas.json"
    id_lote = escribir_json_salida(ruta, [{"kpi_id": "x", "nivel_alerta": "alta"}])
    contenido = json.loads(ruta.read_text(encoding="utf-8"))
    assert contenido["id_lote"] == id_lote and contenido["total_alertas"] == 1
    assert not ruta.with_name(ruta.name + ".tmp").exists()


def test_json_de_salida_con_error_de_escritura(tmp_path):
    ruta = tmp_path / "soy_carpeta"
    ruta.mkdir()  # no se puede reemplazar una carpeta por un archivo
    with pytest.raises(OSError):
        escribir_json_salida(ruta, [{"kpi_id": "x"}], intentos=2)
