"""Pruebas del contrato JSON compartido entre nodos."""
import json

import pytest

from comun.contrato import Encabezado, ErrorContrato, Mensaje, Observacion, huella_lote
from conftest import crear_mensaje, crear_observacion


def test_ida_y_vuelta_conserva_el_mensaje():
    mensaje = crear_mensaje([40.0, 41.5])
    assert Mensaje.desde_bytes(mensaje.a_bytes()) == mensaje


def test_header_identifica_al_nodo():
    datos = json.loads(crear_mensaje([40.0]).a_bytes())
    assert datos["header"]["nodo_id"] == "nodo_prueba"
    assert datos["header"]["version_esquema"] == "1.0"
    assert len(datos["header"]["id_mensaje"]) == 32


@pytest.mark.parametrize("cambios", [
    {"valor": float("nan")},
    {"valor": "40"},
    {"valor": True},
    {"fecha_referencia": "01/02/2026"},
    {"tipo_periodo": "anual"},
    {"unidad": "dolares"},
    {"kpi_id": "  "},
])
def test_observacion_invalida_lanza_error_de_contrato(cambios):
    datos = crear_observacion("2026-01-01", 40.0).a_dict() | cambios
    with pytest.raises(ErrorContrato):
        Observacion.desde_dict(datos)


def test_campo_obligatorio_faltante():
    datos = crear_observacion("2026-01-01", 40.0).a_dict()
    del datos["valor"]
    with pytest.raises(ErrorContrato):
        Observacion.desde_dict(datos)


@pytest.mark.parametrize("crudo", [b"no es json", b"[1, 2]", b'{"header": {}, "payload": {}}', b"\xff\xfe"])
def test_mensajes_malformados(crudo):
    with pytest.raises(ErrorContrato):
        Mensaje.desde_bytes(crudo)


def test_version_de_esquema_incompatible():
    with pytest.raises(ErrorContrato):
        Encabezado(nodo_id="x", tipo_fuente="y", fuente="z", version_esquema="2.0")


def test_huella_ignora_la_fecha_de_captura():
    a = crear_observacion("2026-01-01", 40.0)
    b = Observacion.desde_dict(a.a_dict() | {"fecha_captura": "2030-01-01T00:00:00+00:00"})
    assert a.huella() == b.huella()
    assert huella_lote([a]) == huella_lote([b])
    assert a.huella() != crear_observacion("2026-01-01", 40.1).huella()
