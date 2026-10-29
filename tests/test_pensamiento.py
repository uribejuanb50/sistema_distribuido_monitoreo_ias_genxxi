"""Pruebas del cerebro (reglas de clasificación y señales estadísticas)."""
from pathlib import Path

import pytest

from comun.utilidades import cargar_json
from pensamiento import (ConfiguracionAnalisis, PerfilKPI, ReglasGenerales, calcular_prioridad, etiquetas_intervalos,
                         evaluar, indice_magnitud)

RAIZ = Path(__file__).resolve().parents[1]
PERFIL = PerfilKPI()
REGLAS = ReglasGenerales()


def serie(*valores: float) -> list[tuple[str, float]]:
    return [(f"2026-{i:02d}-01", v) for i, v in enumerate(valores, start=1)]


def evaluar_valores(*valores, meta=45.0, perfil=PERFIL, reglas=REGLAS, unidad="puntos"):
    return evaluar(serie(*valores), meta, unidad, perfil, reglas)


# --- A. magnitud ---------------------------------------------------------------------------

@pytest.mark.parametrize("valor, esperado", [
    (0, "<1"), (0.999, "<1"), (1, "[1, 2)"), (1.999, "[1, 2)"), (2, "[2, 3)"), (3, "[3, 4)"),
    (4, "[4, 5)"), (4.999, "[4, 5)"), (5, ">=5"), (50, ">=5"),
])
def test_intervalos_en_los_limites(valor, esperado):
    limites = PERFIL.limites_magnitud
    assert etiquetas_intervalos(limites)[indice_magnitud(valor, limites)] == esperado


def test_caida_subida_y_cero():
    caida = evaluar_valores(50, 47)
    assert caida.direccion == "caida" and caida.variacion == -3 and caida.deterioro
    subida = evaluar_valores(47, 50)
    assert subida.direccion == "subida" and not subida.deterioro
    cero = evaluar_valores(50, 50)
    assert cero.direccion == "sin_cambio" and cero.variacion == 0 and cero.intervalo_magnitud == "<1"


def test_porcentaje_se_expresa_en_puntos_porcentuales():
    ev = evaluar_valores(90, 87.5, meta=100, unidad="porcentaje")
    assert ev.unidad_variacion == "pp" and ev.variacion == -2.5


# --- B. meta + matriz ------------------------------------------------------------------------

@pytest.mark.parametrize("valores, meta, nivel", [
    ((50, 49.5), 45, "sin_alerta"),  # caída <1 cumpliendo
    ((50, 47), 45, "baja"),          # caída 3 cumpliendo
    ((48, 45), 45, "baja"),          # exactamente en la meta = cumple
    ((46, 43), 45, "media"),         # caída 3 sin cumplir
    ((50, 45.5), 45, "media"),       # caída 4.5 cumpliendo
    ((49, 44), 45, "alta"),          # caída 5 sin cumplir
])
def test_matriz_de_decision(valores, meta, nivel):
    assert evaluar_valores(*valores, meta=meta).nivel_alerta == nivel


def test_mejora_no_es_alerta_negativa_pero_bajo_meta_sigue_en_seguimiento():
    assert evaluar_valores(46, 48).nivel_alerta == "sin_alerta"
    bajo_meta = evaluar_valores(40, 42)
    assert bajo_meta.nivel_alerta == "baja"
    assert any("debajo de la meta" in m for m in bajo_meta.motivos)


def test_primera_observacion():
    ev = evaluar_valores(40)
    assert ev.valor_anterior is None and ev.variacion is None and ev.direccion == "sin_referencia"
    assert ev.nivel_alerta == "baja"  # sin comparación, pero bajo la meta


# --- C. criticidad ---------------------------------------------------------------------------

def test_criticidad_cambia_prioridad_no_nivel():
    critica = evaluar_valores(46, 43, perfil=PerfilKPI(criticidad="critica"))
    seguimiento = evaluar_valores(46, 43, perfil=PerfilKPI(criticidad="seguimiento"))
    assert critica.nivel_alerta == seguimiento.nivel_alerta == "media"
    assert critica.prioridad_revision == "P1" and seguimiento.prioridad_revision == "P3"


@pytest.mark.parametrize("nivel, criticidad, esperado", [
    ("alta", "critica", "P1"), ("alta", "seguimiento", "P2"), ("baja", "seguimiento", "P4"),
    ("baja", "importante", "P3"), ("sin_alerta", "critica", None),
])
def test_prioridad(nivel, criticidad, esperado):
    assert calcular_prioridad(nivel, criticidad) == esperado


# --- D/E. tendencias -------------------------------------------------------------------------

def test_tendencia_descendente_sostenida_eleva_a_media():
    ev = evaluar_valores(50, 49.5, 49.2, 48.9)  # 3 caídas pequeñas, cumpliendo la meta
    assert ev.tendencia == "descendente_sostenida" and ev.nivel_alerta == "media"


def test_caida_aislada_no_es_tendencia():
    ev = evaluar_valores(50, 51, 52, 51.5)
    assert ev.tendencia == "caida_aislada" and ev.nivel_alerta == "sin_alerta"


def test_tendencia_ascendente():
    ev = evaluar_valores(40, 41, 42, 43)
    assert ev.tendencia == "ascendente_sostenida"
    assert ev.nivel_alerta == "baja"  # mejora, pero sigue bajo la meta


def test_sentido_bajar_invierte_el_deterioro():
    perfil = PerfilKPI(sentido_favorable="bajar")  # p. ej. tiempo de espera
    ev = evaluar_valores(30, 36, meta=35, perfil=perfil)
    assert ev.deterioro and ev.cumple_meta is False and ev.nivel_alerta == "alta"


# --- F/G. volatilidad y estabilidad -----------------------------------------------------------

def test_estabilidad_bajo_meta_no_es_buen_desempeno():
    ev = evaluar_valores(40, 40.3, 40.1, 40.4)
    assert ev.estable is True
    assert any("estabilidad no implica" in m for m in ev.motivos)


def test_volatilidad_alta():
    ev = evaluar_valores(50, 50.2, 50.1, 50.3, 50.2, 54, 49, 55, 48)
    assert ev.volatilidad["clasificacion"] == "alta"
    assert ev.volatilidad["tipo"] == "exploratoria"


def test_volatilidad_con_datos_insuficientes():
    assert evaluar_valores(50, 51, 52).volatilidad["clasificacion"] == "datos_insuficientes"


# --- H/I. patrón y dato inusual --------------------------------------------------------------

def test_dato_inusual_con_historial_suficiente():
    ev = evaluar_valores(50, 50.4, 50.1, 50.6, 50.2, 50.7, 50.3, 50.8, 44)
    assert ev.dato_inusual["es_inusual"] is True and ev.dato_inusual["z"] < -2
    assert ev.nivel_alerta in ("media", "alta")
    assert any("calidad del dato" in m for m in ev.motivos)


def test_dato_inusual_sin_historial_suficiente_no_concluye():
    ev = evaluar_valores(50, 50.4, 44)
    assert ev.dato_inusual["es_inusual"] is None


def test_patron_repetitivo_exploratorio():
    ev = evaluar_valores(50, 48, 50, 48, 50, 48, 50)
    assert ev.patron_repetitivo["detectado"] is True
    assert "NO confirmado" in ev.patron_repetitivo["descripcion"]


# --- configuración --------------------------------------------------------------------------

def test_variacion_relativa_con_anterior_cero():
    ev = evaluar_valores(0, 10, perfil=PerfilKPI(modo_variacion="relativa"), meta=None)
    assert ev.variacion is None and ev.unidad_variacion == "%"


@pytest.mark.parametrize("parametros", [
    {"limites_magnitud": (1.0, 1.0, 2.0)},
    {"limites_magnitud": (0.0, 1.0)},
    {"matriz_cumple": ("baja",)},
    {"criticidad": "urgente"},
])
def test_perfil_invalido(parametros):
    with pytest.raises(ValueError):
        PerfilKPI(**parametros)


def test_configuracion_del_nodo_padre_es_valida_y_asigna_perfiles():
    config = ConfiguracionAnalisis.desde_dict(cargar_json(RAIZ / "nodo_padre/nodo_padre/config.json")["analisis"])
    assert config.perfil_para("viki.nps_general_call_center").criticidad == "critica"
    assert config.perfil_para("pospago.cumplimiento_mensual").limites_magnitud == (5, 10, 15, 20, 25)
    assert config.perfil_para("pospago.real_mensual").modo_variacion == "relativa"
    assert config.perfil_para("otro.kpi") == config.perfil_por_defecto
