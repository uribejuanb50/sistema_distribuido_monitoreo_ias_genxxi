"""Captura del reporte Power BI con Selenium. Es el ÚNICO módulo que usa el navegador.

El navegador siempre es VISIBLE por defecto: se ve cómo abre el reporte y cómo lo lee.

Sesión (el código NUNCA escribe usuarios ni contraseñas). Dos modos, en config.json:

- "modo_sesion": "perfil_propio" (por defecto). Selenium abre Edge con un perfil propio
  guardado en %LOCALAPPDATA%. La primera vez inicias sesión A MANO en esa ventana (con tu
  cuenta y tu MFA, como siempre) y Edge la recuerda: las siguientes veces entra directo.
- "modo_sesion": "conectar". Selenium se conecta a un Edge que TÚ abriste con
  abrir_edge_sesion.bat (puerto de depuración 9222) y en el que ya iniciaste sesión. Abre
  el reporte en una pestaña nueva y, al terminar, cierra solo esa pestaña.

Nota: desde la versión 136 de Edge/Chrome, el puerto de depuración NO funciona con el
perfil principal del navegador (el de todos los días). Por eso ambos modos usan una
carpeta de perfil aparte: inicias sesión ahí una vez y queda guardada.

Cada lectura RECARGA la página (el tablero solo se actualiza al recargar) y espera a que
el embed termine de cargar antes de inyectar el script:
  1. que existan los visuales ('visual-container'),
  2. que desaparezca la pantalla de carga de Power BI (el logo animado, 'pbi-svg-loading'),
  3. que ningún visual tenga su spinner "Cargando objetos visuales..." ('.powerbi-spinner.shown'),
  4. que ya aparezca el texto "Actualizado:" (la fecha de los datos),
  5. y que los textos no cambien durante 'segundos_estable'.

Cualquier problema del navegador (no abre, se cerró la ventana, la sesión venció, el
reporte no carga) se lanza como ErrorServicio: main.py avisa al padre y cierra el nodo.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import WebDriverException

from comun.contrato import ahora_iso
from comun.extractor_base import ErrorServicio

log = logging.getLogger(__name__)

SCRIPT_CAPTURA = (Path(__file__).resolve().parent / "script_captura.js").read_text(encoding="utf-8")
# Estado de carga del embed (marcadores tomados del HTML real del tablero).
SCRIPT_ESTADO_CARGA = """
const visuales = Array.from(document.querySelectorAll('visual-container'));
const cargador = document.querySelector('pbi-svg-loading');
let pantallaCarga = false;
if (cargador) {
    const estilo = getComputedStyle(cargador);
    pantallaCarga = estilo.visibility !== 'hidden' && estilo.display !== 'none';
}
const texto = arguments[0];
return {
    visuales: visuales.length,
    con_texto: visuales.filter((v) => (v.textContent || '').trim() !== '').length,
    pantalla_carga: pantallaCarga,
    visuales_cargando: document.querySelectorAll('visual-container .powerbi-spinner.shown').length,
    texto_listo: !texto || visuales.some((v) => (v.textContent || '').includes(texto)),
};
"""
# "Firma" del contenido: si no cambia durante unos segundos, el reporte terminó de cargar.
SCRIPT_FIRMA = ("const v = document.querySelectorAll('visual-container');"
                "return v.length + ':' + Array.from(v).map((x) => x.innerText).join('|');")
SCRIPT_DESPLAZAR = """
const visual = document.querySelectorAll('visual-container')[arguments[0] - 1];
if (!visual) return false;
const desplazable = Array.from(visual.querySelectorAll('*')).find((e) =>
    e.scrollHeight > e.clientHeight + 2 && ['auto', 'scroll'].includes(getComputedStyle(e).overflowY));
if (!desplazable) return false;
const antes = desplazable.scrollTop;
desplazable.scrollTop = antes + Math.max(desplazable.clientHeight * 0.8, 20);
return desplazable.scrollTop > antes;
"""
MAX_DESPLAZAMIENTOS = 40
MODOS_SESION = ("perfil_propio", "conectar")


def carpeta_perfil_por_defecto() -> Path:
    """Perfil del navegador en AppData local: guarda la sesión iniciada, no debe ir a OneDrive."""
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    return base / "sistema_recoleccion_xxi" / "perfil_navegador_powerbi"


class NavegadorPowerBI:
    def __init__(self, url: str, carpeta_perfil: Path | None = None, navegador: str = "edge",
                 visible: bool = True, espera_carga_segundos: float = 90, espera_inicio_sesion_segundos: float = 300,
                 segundos_estable: float = 4, minimo_visuales: int = 20, ancho: int = 1920, alto: int = 1080,
                 modo_sesion: str = "perfil_propio", puerto_depuracion: int = 9222,
                 ruta_driver: Path | None = None, texto_reporte_listo: str | None = "Actualizado") -> None:
        if navegador not in ("edge", "chrome"):
            raise ValueError(f"Navegador no soportado: {navegador!r} (usa 'edge' o 'chrome')")
        if modo_sesion not in MODOS_SESION:
            raise ValueError(f"modo_sesion no soportado: {modo_sesion!r} (usa {' o '.join(MODOS_SESION)})")
        self.url = url
        self.carpeta_perfil = carpeta_perfil or carpeta_perfil_por_defecto()
        self.navegador = navegador
        self.visible = visible
        self.espera_carga = espera_carga_segundos
        self.espera_inicio_sesion = espera_inicio_sesion_segundos
        self.segundos_estable = segundos_estable
        self.minimo_visuales = minimo_visuales
        self.tamano = (ancho, alto)
        self.modo_sesion = modo_sesion
        self.puerto_depuracion = puerto_depuracion
        self.ruta_driver = ruta_driver
        self.texto_listo = texto_reporte_listo
        self.lecturas = 0
        self.host_reporte = urlparse(url).hostname or ""   # app.powerbi.com ("" para páginas locales)
        self._driver: webdriver.Remote | None = None
        self._pestana: str | None = None                   # pestaña propia en el modo "conectar"

    def capturar(self) -> dict[str, Any]:
        """Abre (o recarga) el reporte y devuelve la captura cruda de todos los visuales."""
        try:
            self.lecturas += 1
            if self._driver is None:
                self._driver = self._crear_driver()
                log.info("Lectura #%d: abriendo el reporte de Power BI: %s", self.lecturas, self.url)
                self._driver.get(self.url)
            elif self._en_inicio_de_sesion() or not self._driver.current_url.startswith(("http", "file")):
                log.info("Lectura #%d: volviendo a abrir el reporte", self.lecturas)
                self._driver.get(self.url)
            else:
                log.info("Lectura #%d: recargando la página (el tablero solo se actualiza al recargar)", self.lecturas)
                self._driver.refresh()
            inicio = time.monotonic()
            self._esperar_reporte()
            log.info("Tablero cargado en %.1f s", time.monotonic() - inicio)
            log.info("Inyectando el script de captura en el reporte...")
            visuales = self._driver.execute_script(SCRIPT_CAPTURA, None)
            if not isinstance(visuales, list):
                raise ErrorServicio(f"La inyección JS no devolvió la lista de visuales (devolvió {type(visuales)})")
            self._leer_tablas_completas(visuales)
        except WebDriverException as error:
            self.cerrar()
            raise ErrorServicio(f"Error del navegador: {self._explicar(error)}") from error
        except ErrorServicio:
            raise  # el navegador queda abierto: la próxima lectura vuelve a recargar la página
        con_datos = sum(1 for visual in visuales if visual["textos"] or visual["filas"])
        log.info("Capturados %d visuales (%d con datos)", len(visuales), con_datos)
        return {"capturado_en": ahora_iso(), "origen": self.url, "visuales": visuales}

    def cerrar(self) -> None:
        """Cierra el navegador (o, en el modo "conectar", solo la pestaña que abrió el nodo)."""
        if self._driver is None:
            return
        try:
            if self.modo_sesion == "conectar":
                if self._pestana and self._pestana in self._driver.window_handles:
                    self._driver.switch_to.window(self._pestana)
                    self._driver.close()
            else:
                self._driver.quit()
        except WebDriverException:
            pass
        self._driver = None
        self._pestana = None

    # ------------------------------------------------------------ pasos internos

    def _crear_driver(self) -> webdriver.Remote:
        opciones = webdriver.EdgeOptions() if self.navegador == "edge" else webdriver.ChromeOptions()
        if self.modo_sesion == "conectar":
            direccion = f"127.0.0.1:{self.puerto_depuracion}"
            opciones.add_experimental_option("debuggerAddress", direccion)
            log.info("Conectándose al %s abierto con abrir_edge_sesion.bat (%s)...", self.navegador, direccion)
        else:
            self.carpeta_perfil.mkdir(parents=True, exist_ok=True)
            opciones.add_argument(f"--user-data-dir={self.carpeta_perfil}")
            opciones.add_argument(f"--window-size={self.tamano[0]},{self.tamano[1]}")
            opciones.add_argument("--no-first-run")
            opciones.add_argument("--no-default-browser-check")
            if not self.visible:
                opciones.add_argument("--headless=new")
            log.info("Iniciando %s (%s) con el perfil %s", self.navegador,
                     "visible" if self.visible else "oculto", self.carpeta_perfil)
        clase = webdriver.Edge if self.navegador == "edge" else webdriver.Chrome
        servicio_clase = (webdriver.EdgeService if self.navegador == "edge" else webdriver.ChromeService)
        servicio = servicio_clase(executable_path=str(self.ruta_driver)) if self.ruta_driver else servicio_clase()
        driver = clase(options=opciones, service=servicio)
        if self.modo_sesion == "conectar":
            driver.switch_to.new_window("tab")  # no se toca lo que tengas abierto en ese Edge
            self._pestana = driver.current_window_handle
        return driver

    def _explicar(self, error: WebDriverException) -> str:
        """Mensaje del error de Selenium con una pista de qué hacer."""
        texto = (error.msg or str(error)).strip().splitlines()[0]
        bajo = texto.lower()
        if self.modo_sesion == "conectar" and ("cannot connect" in bajo or "not reachable" in bajo):
            return (f"{texto}. ¿Está abierto el Edge de abrir_edge_sesion.bat (puerto {self.puerto_depuracion})?")
        if "user data directory is already in use" in bajo:
            return f"{texto}. Cierra la otra ventana de Edge que usa el perfil {self.carpeta_perfil}"
        if "unable to obtain driver" in bajo or ("msedgedriver" in bajo and "not found" in bajo):
            return (f"{texto}. Selenium no pudo descargar msedgedriver (¿red de la empresa?): descárgalo de "
                    "https://developer.microsoft.com/microsoft-edge/tools/webdriver con la MISMA versión de tu "
                    "Edge y pon su ruta en 'ruta_driver' de config.json")
        if "no such window" in bajo or "target window already closed" in bajo or "disconnected" in bajo:
            return f"{texto}. Se cerró la ventana del navegador"
        return texto

    def _en_inicio_de_sesion(self) -> bool:
        """True si el navegador NO está en el sitio del reporte (está en una página de login).

        Se mira el host y no una lista de páginas de login porque el login de la empresa
        puede ser federado (ADFS u otro dominio corporativo), no solo login.microsoftonline.com.
        """
        if not self.host_reporte:
            return False
        host = urlparse(self._driver.current_url).hostname or ""
        if not host:  # about:blank, data:, file:
            return False
        return not (host == self.host_reporte or host.endswith((".powerbi.com", "fabric.microsoft.com")))

    def _esperar_reporte(self) -> None:
        """Espera a que el embed termine de cargar (y, si hace falta, a que inicies sesión)."""
        limite = time.monotonic() + self.espera_carga
        aviso_login = False
        ultimo_aviso = 0.0
        firma_anterior, estable_desde = None, 0.0
        while True:
            ahora = time.monotonic()
            if self._en_inicio_de_sesion():
                if not self.visible:
                    raise ErrorServicio("El reporte pide iniciar sesión y el navegador está oculto: "
                                        "pon 'navegador_visible': true en config.json")
                if not aviso_login:
                    log.warning("El reporte pide iniciar sesión (%s): hazlo A MANO EN LA VENTANA DEL NAVEGADOR. "
                                "Esperando hasta %d s...", urlparse(self._driver.current_url).hostname,
                                self.espera_inicio_sesion)
                    limite = ahora + self.espera_inicio_sesion
                    aviso_login = True
                if ahora > limite:
                    raise ErrorServicio(f"No se inició sesión en {self.espera_inicio_sesion:.0f} s: la sesión venció "
                                        "o nadie la inició en la ventana del navegador")
                time.sleep(1)
                continue
            if aviso_login:
                log.info("Sesión iniciada: el perfil la recordará para las próximas lecturas")
                limite = ahora + self.espera_carga
                aviso_login = False

            estado = self._driver.execute_script(SCRIPT_ESTADO_CARGA, self.texto_listo)
            # "basico": hay visuales con datos (suficiente para leer si se acaba el tiempo).
            basico = estado["visuales"] >= self.minimo_visuales and estado["con_texto"] > 0
            listo = (basico and not estado["pantalla_carga"] and estado["visuales_cargando"] == 0
                     and estado["texto_listo"])
            if listo:
                firma = self._driver.execute_script(SCRIPT_FIRMA)
                if firma != firma_anterior:
                    firma_anterior, estable_desde = firma, ahora
                elif ahora - estable_desde >= self.segundos_estable:
                    return
            else:
                firma_anterior = None

            if ahora - ultimo_aviso >= 5:
                log.info("Esperando el tablero: %s | %d/%d visuales (%d con datos) | %d con 'Cargando...' | "
                         "texto '%s' %s", "pantalla de carga" if estado["pantalla_carga"] else "sin pantalla de carga",
                         estado["visuales"], self.minimo_visuales, estado["con_texto"], estado["visuales_cargando"],
                         self.texto_listo, "visible" if estado["texto_listo"] else "aún no aparece")
                ultimo_aviso = ahora
            if ahora > limite:
                if basico:  # la página está ahí con datos: se lee igual y se avisa por qué no estaba "lista"
                    pendientes = [m for m, activo in (("pantalla de carga visible", estado["pantalla_carga"]),
                                  (f"{estado['visuales_cargando']} visuales con 'Cargando...'",
                                   estado["visuales_cargando"] > 0),
                                  (f"sin el texto '{self.texto_listo}'", not estado["texto_listo"]),
                                  ("los textos siguen cambiando", listo)) if activo]
                    log.warning("Pasaron %d s y el tablero no quedó 100%% listo (%s): se lee de todos modos",
                                self.espera_carga, ", ".join(pendientes))
                    return
                self._guardar_pantallazo()
                raise ErrorServicio(
                    f"El tablero no terminó de cargar en {self.espera_carga:.0f} s (visuales: {estado['visuales']}, "
                    f"pantalla de carga: {'sí' if estado['pantalla_carga'] else 'no'}, visuales cargando: "
                    f"{estado['visuales_cargando']}, texto '{self.texto_listo}': "
                    f"{'sí' if estado['texto_listo'] else 'no'}; página '{self._driver.title}'). "
                    "¿Tienes acceso al reporte? Si tu red es lenta, sube 'espera_carga_segundos'")
            time.sleep(0.5)

    def _guardar_pantallazo(self) -> None:
        """Deja un pantallazo de lo que veía el navegador al fallar (logs/powerbi_error.png)."""
        try:
            from comun.utilidades import DIR_LOGS
            DIR_LOGS.mkdir(exist_ok=True)
            ruta = DIR_LOGS / "powerbi_error.png"
            self._driver.save_screenshot(str(ruta))
            log.error("Pantallazo del navegador al fallar: %s", ruta)
        except Exception:  # el pantallazo es solo ayuda: nunca debe tapar el error real
            pass

    def _leer_tablas_completas(self, visuales: list[dict[str, Any]]) -> None:
        """Desplaza cada tabla hasta el final y junta todas sus filas (sin repetir)."""
        for visual in visuales:
            if not visual["filas"]:
                continue
            filas = {tuple(fila): None for fila in visual["filas"]}
            for _ in range(MAX_DESPLAZAMIENTOS):
                if not self._driver.execute_script(SCRIPT_DESPLAZAR, visual["bloque"]):
                    break
                time.sleep(0.4)
                for fila in self._driver.execute_script(SCRIPT_CAPTURA, visual["bloque"]):
                    filas.setdefault(tuple(fila))
            visual["filas"] = [list(fila) for fila in filas]
