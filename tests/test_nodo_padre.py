"""Pruebas del flujo completo del nodo padre (sin red): mensaje -> SQLite -> análisis -> JSON."""
import json
from pathlib import Path

import pytest

from comun.utilidades import cargar_json
from conftest import RAIZ, cargar_modulo, crear_mensaje
from guardado import Almacen
from pensamiento import ConfiguracionAnalisis

padre = cargar_modulo("main_nodo_padre", RAIZ / "nodo_padre" / "nodo_padre" / "main.py")

# Serie con 3 caídas consecutivas al final y bajo la meta (45): debe alertar.
SERIE_CON_ALERTA = [42.0, 43.5, 41.8, 44.2, 45.0, 44.6, 46.1, 46.4, 45.3, 44.0, 42.7, 41.2]


@pytest.fixture
def ctx(tmp_path: Path):
    config = cargar_json(RAIZ / "nodo_padre" / "nodo_padre" / "config.json")
    almacen = Almacen(tmp_path / "padre.sqlite3")
    eventos = []
    contexto = padre.ContextoPadre(
        almacen=almacen,
        configuracion=ConfiguracionAnalisis.desde_dict(config["analisis"]),
        ruta_json=tmp_path / "salida" / "alertas.json",
        notificar_evento=eventos.append,
    )
    contexto.eventos = eventos
    yield contexto
    almacen.cerrar()


def leer_json(ctx) -> dict:
    return json.loads(ctx.ruta_json.read_text(encoding="utf-8"))


def test_carga_inicial_notifica_solo_el_ultimo_periodo(ctx):
    padre.procesar_mensaje(crear_mensaje(SERIE_CON_ALERTA).a_bytes(), ctx)
    salida = leer_json(ctx)
    assert salida["total_alertas"] == 1
    alerta = salida["alertas"][0]
    assert alerta["fecha_referencia"] == "2026-12-01"
    assert alerta["nivel_alerta"] == "media" and alerta["prioridad_revision"] == "P1"
    assert alerta["senales"]["tendencia"]["valor"] == "descendente_sostenida"
    assert alerta["nodo_origen"] == "nodo_prueba"
    assert ctx.eventos[-1]["tipo"] == "json_actualizado"


def test_no_duplica_notificaciones(ctx):
    padre.procesar_mensaje(crear_mensaje(SERIE_CON_ALERTA).a_bytes(), ctx)
    id_lote = leer_json(ctx)["id_lote"]
    padre.procesar_mensaje(crear_mensaje(SERIE_CON_ALERTA).a_bytes(), ctx)  # reenvío periódico
    assert leer_json(ctx)["id_lote"] == id_lote
    assert ctx.estadisticas.alertas_emitidas == 1


def test_mismo_mensaje_dos_veces_se_ignora(ctx):
    crudo = crear_mensaje(SERIE_CON_ALERTA).a_bytes()
    padre.procesar_mensaje(crudo, ctx)
    padre.procesar_mensaje(crudo, ctx)
    assert ctx.estadisticas.mensajes_recibidos == 2
    assert ctx.estadisticas.observaciones_nuevas == len(SERIE_CON_ALERTA)


def test_nuevo_periodo_con_caida_fuerte_genera_nueva_alerta(ctx):
    padre.procesar_mensaje(crear_mensaje(SERIE_CON_ALERTA[:-1]).a_bytes(), ctx)
    primer_lote = leer_json(ctx)["id_lote"]
    padre.procesar_mensaje(crear_mensaje(SERIE_CON_ALERTA[:-1] + [36.0]).a_bytes(), ctx)
    salida = leer_json(ctx)
    assert salida["id_lote"] != primer_lote
    assert salida["alertas"][0]["nivel_alerta"] == "alta"


def test_sin_cambios_significativos_no_escribe_json(ctx):
    padre.procesar_mensaje(crear_mensaje([46.0, 47.0, 48.0]).a_bytes(), ctx)
    assert not ctx.ruta_json.exists()


def test_mensaje_invalido_no_detiene_al_padre(ctx):
    padre.procesar_mensaje(b"basura", ctx)
    assert ctx.estadisticas.mensajes_invalidos == 1


def test_error_al_escribir_json_deja_alertas_pendientes_y_reintenta(ctx):
    destino = ctx.ruta_json
    ctx.ruta_json = destino.parent  # una carpeta: la escritura falla
    ctx.ruta_json.mkdir(parents=True)
    padre.procesar_mensaje(crear_mensaje(SERIE_CON_ALERTA).a_bytes(), ctx)
    assert len(ctx.pendientes) == 1 and ctx.estadisticas.alertas_emitidas == 0

    ctx.ruta_json = destino  # el destino vuelve a estar disponible
    assert padre.vaciar_pendientes(ctx) is True
    assert ctx.pendientes == [] and leer_json(ctx)["total_alertas"] == 1
