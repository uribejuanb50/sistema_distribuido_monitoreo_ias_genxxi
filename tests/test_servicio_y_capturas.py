"""Pruebas de los mensajes "captura" (números crudos) y "estado" (servicio caído), de punta a punta."""
import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest

from captura_powerbi_ficticia import MAPA, captura_consola, captura_nodo
from comun.contrato import Encabezado, ErrorContrato, Mensaje
from comun.extractor_base import ErrorExtraccion, ErrorServicio, ExtractorBase
from comun.utilidades import cargar_json
from conftest import RAIZ, cargar_modulo, crear_mensaje
from guardado import Almacen
from pensamiento import ConfiguracionAnalisis

DIR_POWERBI = RAIZ / "nodos_hijo" / "nodo_powerbi_viki"
if str(DIR_POWERBI) not in sys.path:
    sys.path.append(str(DIR_POWERBI))
pbi = cargar_modulo("extraccion_powerbi_servicio", DIR_POWERBI / "extraccion.py")
padre = cargar_modulo("main_nodo_padre_servicio", RAIZ / "nodo_padre" / "nodo_padre" / "main.py")


def encabezado(tipo: str, nodo: str = "nodo_powerbi_viki") -> Encabezado:
    return Encabezado(nodo_id=nodo, tipo_fuente="powerbi", fuente="reporte Power BI VIKI", tipo_mensaje=tipo)


def mensaje_estado(estado: str, motivo: str = "El reporte no cargó a tiempo") -> Mensaje:
    return Mensaje(encabezado("estado"), contenido={"estado": estado, "motivo": motivo, "detalle": {"x": 1}})


def mensaje_captura(cambios: dict[str, str] | None = None) -> Mensaje:
    captura = pbi.normalizar_captura(captura_consola(cambios=cambios), "prueba")
    return Mensaje(encabezado("captura"), contenido=pbi.a_payload_captura(captura))


@pytest.fixture
def ctx(tmp_path: Path):
    config = cargar_json(RAIZ / "nodo_padre" / "nodo_padre" / "config.json")
    almacen = Almacen(tmp_path / "padre.sqlite3")
    eventos = []
    contexto = padre.ContextoPadre(almacen=almacen, configuracion=ConfiguracionAnalisis.desde_dict(config["analisis"]),
                                   ruta_json=tmp_path / "salida" / "alertas.json", notificar_evento=eventos.append)
    contexto.eventos = eventos
    yield contexto
    almacen.cerrar()


# --- contrato ---------------------------------------------------------------------------------

def test_estado_y_captura_ida_y_vuelta():
    for mensaje in (mensaje_estado("caido"), mensaje_captura()):
        assert Mensaje.desde_bytes(mensaje.a_bytes()) == mensaje
    datos = json.loads(mensaje_estado("caido").a_bytes())
    assert datos["header"]["tipo_mensaje"] == "estado" and datos["payload"]["estado"] == "caido"


@pytest.mark.parametrize("payload", [
    {"estado": "explotó", "motivo": "x"},
    {"estado": "caido", "motivo": "  "},
    {"estado": "caido", "motivo": "x", "detalle": "texto"},
])
def test_estado_invalido(payload):
    crudo = json.dumps({"header": json.loads(mensaje_estado("caido").a_bytes())["header"], "payload": payload})
    with pytest.raises(ErrorContrato):
        Mensaje.desde_bytes(crudo.encode())


@pytest.mark.parametrize("visual", [
    {"bloque": 0, "seccion": "a", "valores": []},
    {"bloque": "3", "seccion": "a", "valores": []},
    {"bloque": 3, "seccion": "a", "valores": [3]},
    {"bloque": 3, "seccion": "a", "valores": ["3"], "numeros": [float("nan")]},
])
def test_captura_invalida(visual):
    with pytest.raises(ErrorContrato):
        Mensaje(encabezado("captura"), contenido={"visuales": [visual]})


# --- hijo Power BI: números crudos ----------------------------------------------------------

def test_payload_captura_trae_numeros_por_bloque():
    payload = pbi.a_payload_captura(pbi.normalizar_captura(captura_nodo(), "prueba"))
    por_bloque = {v["bloque"]: v for v in payload["visuales"]}
    assert por_bloque[59]["seccion"] == "Visual / Bloque 59"
    assert por_bloque[59]["numeros"] == [pbi.a_numero(por_bloque[59]["valores"][0])]
    assert por_bloque[52]["numeros"][0] is not None  # porcentaje
    tabla = por_bloque[114]  # tabla leída por roles ARIA: sus celdas van aplanadas
    assert "Gerencia Norte" in tabla["valores"] and 1100.0 in tabla["numeros"]
    assert por_bloque[4]["numeros"] == [None]  # texto "Actualizado: ...": no es número


def test_captura_cruda_se_conserva_aunque_fallen_las_validaciones(tmp_path):
    ruta = tmp_path / "captura.json"
    ruta.write_text(json.dumps(captura_consola(cambios={"52": "2.967"}), ensure_ascii=False), encoding="utf-8")
    extractor = pbi.ExtractorPowerBI(MAPA, ruta_captura=ruta)
    with pytest.raises(ErrorExtraccion) as error:
        extractor.extraer()
    assert not isinstance(error.value, ErrorServicio)  # problema de datos, no caída del servicio
    assert len(extractor.captura_cruda()["visuales"]) > 30


# --- navegador: detección del inicio de sesión ---------------------------------------------

def test_login_corporativo_federado_se_detecta():
    pytest.importorskip("selenium")
    navegador_mod = cargar_modulo("navegador_servicio", DIR_POWERBI / "navegador.py")
    nav = navegador_mod.NavegadorPowerBI("https://app.powerbi.com/groups/me/reports/x")
    for url, en_login in [("https://login.microsoftonline.com/abc", True), ("https://sts.empresa.com.co/adfs", True),
                          ("https://app.powerbi.com/groups/me/reports/x", False)]:
        nav._driver = types.SimpleNamespace(current_url=url)
        assert nav._en_inicio_de_sesion() is en_login
    nav._driver = None


# --- padre ------------------------------------------------------------------------------------

def test_padre_guarda_captura_y_no_duplica_si_no_cambia(ctx):
    padre.procesar_mensaje(mensaje_captura().a_bytes(), ctx)
    padre.procesar_mensaje(mensaje_captura().a_bytes(), ctx)  # mismos valores, otro mensaje
    padre.procesar_mensaje(mensaje_captura(cambios={"59": "9.999"}).a_bytes(), ctx)
    assert ctx.estadisticas.capturas_recibidas == 3
    capturas = ctx.almacen._con.execute("SELECT COUNT(*) FROM capturas").fetchone()[0]
    assert capturas == 2
    ultima = ctx.almacen.ultima_captura("nodo_powerbi_viki")
    assert ultima[59]["valores"] == ["9.999"] and ultima[59]["numeros"] == [9999.0]
    assert not ctx.ruta_json.exists()  # una captura no es una alerta


def test_servicio_caido_genera_alerta_para_power_automate(ctx):
    padre.procesar_mensaje(mensaje_estado("activo", "lectura correcta").a_bytes(), ctx)
    assert not ctx.ruta_json.exists()
    padre.procesar_mensaje(mensaje_estado("caido").a_bytes(), ctx)
    salida = json.loads(ctx.ruta_json.read_text(encoding="utf-8"))
    alerta = salida["alertas"][0]
    assert alerta["tipo_alerta"] == "servicio_caido" and alerta["prioridad_revision"] == "P1"
    assert alerta["nodo_origen"] == "nodo_powerbi_viki" and "no cargó" in alerta["motivos"][0]
    assert ctx.estadisticas.estados_nodos["nodo_powerbi_viki"] == "caido"
    assert padre.crear_latido(ctx)["estados_nodos"] == {"nodo_powerbi_viki": "caido"}
    assert any(e["tipo"] == "estado_nodo" and e["estado"] == "caido" for e in ctx.eventos)
    assert ctx.eventos[-1]["servicios_caidos"] == ["nodo_powerbi_viki"]
    assert ctx.almacen.estados_actuales() == {"nodo_powerbi_viki": "caido"}


def test_degradado_no_escribe_alerta(ctx):
    padre.procesar_mensaje(mensaje_estado("degradado", "validaciones").a_bytes(), ctx)
    assert not ctx.ruta_json.exists()


def test_alertas_de_kpi_siguen_funcionando_junto_con_las_de_servicio(ctx):
    serie = [42.0, 43.5, 41.8, 44.2, 45.0, 44.6, 46.1, 46.4, 45.3, 44.0, 42.7, 41.2]
    padre.procesar_mensaje(crear_mensaje(serie).a_bytes(), ctx)
    padre.procesar_mensaje(mensaje_estado("caido").a_bytes(), ctx)
    notificaciones = ctx.almacen._con.execute("SELECT COUNT(*) FROM notificaciones").fetchone()[0]
    assert notificaciones == 1 and ctx.estadisticas.alertas_emitidas == 2


def test_base_antigua_se_migra(tmp_path):
    ruta = tmp_path / "vieja.sqlite3"
    con = sqlite3.connect(ruta)
    con.execute("CREATE TABLE mensajes (id_mensaje TEXT PRIMARY KEY, nodo_id TEXT NOT NULL, tipo_fuente TEXT NOT NULL, "
                "fuente TEXT NOT NULL, version_esquema TEXT NOT NULL, secuencia INTEGER NOT NULL, enviado_en TEXT NOT "
                "NULL, recibido_en TEXT NOT NULL, n_observaciones INTEGER NOT NULL)")
    con.commit()
    con.close()
    almacen = Almacen(ruta)
    assert almacen.registrar_mensaje(mensaje_estado("caido")) is True
    almacen.cerrar()


# --- hijo: main.py avisa la caída y se cierra -----------------------------------------------

class ExtractorQueSeCae(ExtractorBase):
    tipo_fuente = "powerbi"

    def __init__(self, errores):
        self.errores = list(errores)
        self.cerrado = False

    @property
    def fuente(self):
        return "reporte de prueba"

    def extraer(self):
        error = self.errores.pop(0)
        if error is None:
            return []
        raise error

    def captura_cruda(self):
        return None

    def cerrar(self):
        self.cerrado = True


def correr_hijo(monkeypatch, tmp_path, errores, cerrar_ante_error=True, fallos=1):
    zmq = pytest.importorskip("zmq")
    hijo = cargar_modulo("main_hijo_servicio", DIR_POWERBI / "main.py")
    config = cargar_json(DIR_POWERBI / "config.json") | {"cerrar_ante_error": cerrar_ante_error,
                                                          "fallos_para_caido": fallos,
                                                          "intervalo_segundos": 0, "espera_conexion_segundos": 0}
    ruta_config = tmp_path / "config.json"
    ruta_config.write_text(json.dumps(config), encoding="utf-8")
    extractor = ExtractorQueSeCae(errores)
    enviados = []
    monkeypatch.setattr(hijo, "crear_extractor", lambda _config: extractor)
    monkeypatch.setattr(hijo, "configurar_logging", lambda nombre: None)
    monkeypatch.setattr(hijo, "publicar", lambda socket, topico, cuerpo: enviados.append(Mensaje.desde_bytes(cuerpo)))
    monkeypatch.setattr(hijo.zmq, "Context", lambda: types.SimpleNamespace(
        socket=lambda tipo: types.SimpleNamespace(setsockopt=lambda *a: None, connect=lambda e: None,
                                                  close=lambda: None), term=lambda: None))
    monkeypatch.setattr(sys, "argv", ["main.py", "--config", str(ruta_config)])
    return hijo.main(), enviados, extractor


def test_hijo_tolera_fallos_sueltos_antes_de_darse_por_caido(monkeypatch, tmp_path):
    codigo, enviados, _ = correr_hijo(
        monkeypatch, tmp_path, [ErrorServicio("lento"), None, ErrorServicio("a"), ErrorServicio("b"),
                                ErrorServicio("c")], fallos=3)
    assert codigo == 1
    estados = [m.contenido["estado"] for m in enviados if m.tipo == "estado"]
    assert estados == ["degradado", "activo", "degradado", "degradado", "caido"]


def test_hijo_avisa_caido_y_se_cierra(monkeypatch, tmp_path):
    codigo, enviados, extractor = correr_hijo(
        monkeypatch, tmp_path, [None, ErrorExtraccion("faltan datos"), ErrorServicio("Se cerró la ventana")])
    assert codigo == 1 and extractor.cerrado
    estados = [m.contenido["estado"] for m in enviados if m.tipo == "estado"]
    assert estados == ["activo", "degradado", "caido"]
    assert enviados[-1].contenido["motivo"] == "Se cerró la ventana"
    assert [m.encabezado.secuencia for m in enviados] == list(range(1, len(enviados) + 1))


def test_error_inesperado_tambien_cierra(monkeypatch, tmp_path):
    codigo, enviados, _ = correr_hijo(monkeypatch, tmp_path, [KeyError("visual")])
    assert codigo == 1 and enviados[-1].contenido["estado"] == "caido"
    assert enviados[-1].contenido["detalle"]["tipo_error"] == "KeyError"


def test_sin_cerrar_ante_error_el_hijo_sigue(monkeypatch, tmp_path):
    codigo, enviados, _ = correr_hijo(monkeypatch, tmp_path, [ErrorServicio("caída"), None, KeyboardInterrupt()],
                                      cerrar_ante_error=False)
    assert codigo == 0
    assert [m.contenido["estado"] for m in enviados if m.tipo == "estado"] == ["degradado", "activo", "detenido"]


# --- navegador: espera del embed y recarga en cada lectura -----------------------------------

class RelojFalso:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, segundos):
        self.t += segundos


class DriverFalso:
    """Imita a Selenium: cada consulta del estado de carga avanza un paso de 'estados'."""

    def __init__(self, estados, url="https://app.powerbi.com/groups/me/reports/x"):
        self.estados = list(estados)
        self.current_url = url
        self.title = "General - Tablero"
        self.acciones = []

    def execute_script(self, script, *args):
        if "pbi-svg-loading" in script:
            estado = self.estados.pop(0) if len(self.estados) > 1 else self.estados[0]
            if "url" in estado:
                self.current_url = estado["url"]
            return {k: v for k, v in estado.items() if k != "url"}
        return "firma-estable"

    def get(self, url):
        self.acciones.append("get")

    def refresh(self):
        self.acciones.append("refresh")


LISTO = {"visuales": 129, "con_texto": 47, "pantalla_carga": False, "visuales_cargando": 0, "texto_listo": True}


def navegador_falso(monkeypatch, estados, **kwargs):
    pytest.importorskip("selenium")
    navegador_mod = cargar_modulo("navegador_espera", DIR_POWERBI / "navegador.py")
    reloj = RelojFalso()
    monkeypatch.setattr(navegador_mod, "time", reloj)
    nav = navegador_mod.NavegadorPowerBI("https://app.powerbi.com/groups/me/reports/x", minimo_visuales=30,
                                         segundos_estable=2, espera_carga_segundos=30, **kwargs)
    nav._driver = DriverFalso(estados)
    return nav, reloj


def test_espera_pantalla_de_carga_y_spinners_antes_de_leer(monkeypatch):
    estados = ([{"visuales": 0, "con_texto": 0, "pantalla_carga": True, "visuales_cargando": 0,
                 "texto_listo": False}] * 4
               + [{**LISTO, "visuales_cargando": 12}] * 6
               + [LISTO])
    nav, reloj = navegador_falso(monkeypatch, estados)
    nav._esperar_reporte()
    assert reloj.t >= 0.5 * 10 + 2  # esperó la carga y luego 2 s de textos estables


def test_tablero_sin_visuales_es_servicio_caido(monkeypatch):
    nav, _ = navegador_falso(monkeypatch, [{"visuales": 0, "con_texto": 0, "pantalla_carga": True,
                                            "visuales_cargando": 0, "texto_listo": False}])
    with pytest.raises(ErrorServicio, match="no terminó de cargar"):
        nav._esperar_reporte()


def test_spinner_pegado_con_datos_visibles_se_lee_igual(monkeypatch):
    nav, reloj = navegador_falso(monkeypatch, [{**LISTO, "visuales_cargando": 1}])
    nav._esperar_reporte()  # no lanza: hay datos en pantalla, solo se avisa
    assert reloj.t > 30


def test_host_de_fabric_no_es_login(monkeypatch):
    nav, _ = navegador_falso(monkeypatch, [LISTO])
    nav._driver.current_url = "https://app.fabric.microsoft.com/groups/me/reports/x"
    assert nav._en_inicio_de_sesion() is False


def test_login_que_nadie_completa_es_servicio_caido(monkeypatch):
    nav, _ = navegador_falso(monkeypatch, [{**LISTO, "url": "https://login.microsoftonline.com/x"}],
                             espera_inicio_sesion_segundos=60)
    with pytest.raises(ErrorServicio, match="No se inició sesión"):
        nav._esperar_reporte()


def test_cada_lectura_recarga_la_pagina(monkeypatch):
    nav, _ = navegador_falso(monkeypatch, [LISTO])
    driver = nav._driver
    monkeypatch.setattr(nav, "_leer_tablas_completas", lambda visuales: None)
    driver.execute_script = (lambda original: lambda script, *a: [] if "estructuraReporte" in script
                             else original(script, *a))(driver.execute_script)
    nav.capturar()
    nav.capturar()
    assert driver.acciones == ["refresh", "refresh"]
    nav._driver = None
