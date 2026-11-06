"""Captura del reporte Power BI con Selenium. Es el ÚNICO módulo que usa el navegador.

- Usa un perfil PROPIO del navegador (fuera de OneDrive): la primera vez inicias sesión
  a mano en la ventana que se abre y, después, la sesión se recuerda. El código nunca
  escribe usuarios ni contraseñas.
- Espera a que el reporte termine de dibujarse (los textos dejan de cambiar) y ejecuta
  script_captura.js: cada visual con su número de bloque, posición, título y textos.
- Las tablas de Power BI solo dibujan las filas visibles: se desplazan para leerlas todas.
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
from comun.extractor_base import ErrorExtraccion

log = logging.getLogger(__name__)

SCRIPT_CAPTURA = (Path(__file__).resolve().parent / "script_captura.js").read_text(encoding="utf-8")
SCRIPT_CONTAR = "return document.querySelectorAll('visual-container').length;"
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
HOSTS_INICIO_SESION = ("login.microsoftonline.com", "login.live.com", "login.windows.net")
MAX_DESPLAZAMIENTOS = 40


def carpeta_perfil_por_defecto() -> Path:
    """Perfil del navegador en AppData local: guarda la sesión iniciada, no debe ir a OneDrive."""
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    return base / "sistema_recoleccion_xxi" / "perfil_navegador_powerbi"


class NavegadorPowerBI:
    def __init__(self, url: str, carpeta_perfil: Path | None = None, navegador: str = "edge",
                 visible: bool = True, espera_carga_segundos: float = 90, espera_inicio_sesion_segundos: float = 300,
                 segundos_estable: float = 4, minimo_visuales: int = 20, ancho: int = 1920, alto: int = 1080) -> None:
        if navegador not in ("edge", "chrome"):
            raise ValueError(f"Navegador no soportado: {navegador!r} (usa 'edge' o 'chrome')")
        self.url = url
        self.carpeta_perfil = carpeta_perfil or carpeta_perfil_por_defecto()
        self.navegador = navegador
        self.visible = visible
        self.espera_carga = espera_carga_segundos
        self.espera_inicio_sesion = espera_inicio_sesion_segundos
        self.segundos_estable = segundos_estable
        self.minimo_visuales = minimo_visuales
        self.tamano = (ancho, alto)
        self._driver: webdriver.Remote | None = None

    def capturar(self) -> dict[str, Any]:
        """Abre (o recarga) el reporte y devuelve la captura cruda de todos los visuales."""
        try:
            if self._driver is None:
                self._driver = self._crear_driver()
            log.info("Abriendo el reporte de Power BI...")
            self._driver.get(self.url)
            self._esperar_reporte()
            visuales = self._driver.execute_script(SCRIPT_CAPTURA, None)
            self._leer_tablas_completas(visuales)
        except WebDriverException as error:
            self.cerrar()  # p. ej. se cerró la ventana: en el próximo ciclo se abre otra
            raise ErrorExtraccion(f"Error del navegador: {error.msg or error}") from error
        con_datos = sum(1 for visual in visuales if visual["textos"] or visual["filas"])
        log.info("Capturados %d visuales (%d con datos)", len(visuales), con_datos)
        return {"capturado_en": ahora_iso(), "origen": self.url, "visuales": visuales}

    def cerrar(self) -> None:
        if self._driver is not None:
            try:
                self._driver.quit()
            except WebDriverException:
                pass
            self._driver = None

    # ------------------------------------------------------------ pasos internos

    def _crear_driver(self) -> webdriver.Remote:
        self.carpeta_perfil.mkdir(parents=True, exist_ok=True)
        opciones = webdriver.EdgeOptions() if self.navegador == "edge" else webdriver.ChromeOptions()
        opciones.add_argument(f"--user-data-dir={self.carpeta_perfil}")
        opciones.add_argument(f"--window-size={self.tamano[0]},{self.tamano[1]}")
        opciones.add_argument("--no-first-run")
        opciones.add_argument("--no-default-browser-check")
        if not self.visible:
            opciones.add_argument("--headless=new")
        log.info("Iniciando %s (%s) con el perfil %s", self.navegador,
                 "visible" if self.visible else "oculto", self.carpeta_perfil)
        if self.navegador == "edge":
            return webdriver.Edge(options=opciones)
        return webdriver.Chrome(options=opciones)

    def _en_inicio_de_sesion(self) -> bool:
        host = urlparse(self._driver.current_url).hostname or ""
        return any(host.endswith(dominio) for dominio in HOSTS_INICIO_SESION)

    def _esperar_reporte(self) -> None:
        """Espera a que aparezcan los visuales (y, si hace falta, a que inicies sesión)."""
        limite = time.monotonic() + self.espera_carga
        aviso_dado = False
        while (cantidad := self._driver.execute_script(SCRIPT_CONTAR)) < self.minimo_visuales:
            if self._en_inicio_de_sesion() and not aviso_dado:
                if not self.visible:
                    raise ErrorExtraccion("El reporte pide iniciar sesión y el navegador está oculto: "
                                          "pon 'navegador_visible': true en config.json")
                log.warning("El reporte pide iniciar sesión: hazlo EN LA VENTANA DEL NAVEGADOR (solo la primera "
                            "vez). Esperando hasta %d s...", self.espera_inicio_sesion)
                limite = time.monotonic() + self.espera_inicio_sesion
                aviso_dado = True
            if time.monotonic() > limite:
                raise ErrorExtraccion(f"El reporte no cargó a tiempo: {cantidad} visuales de "
                                      f"{self.minimo_visuales} esperados (sube 'espera_carga_segundos')")
            time.sleep(1)

        log.info("Reporte visible (%d visuales). Esperando a que terminen de cargar los datos...", cantidad)
        limite = time.monotonic() + self.espera_carga
        firma_anterior, estable_desde = None, time.monotonic()
        while True:
            firma = self._driver.execute_script(SCRIPT_FIRMA)
            ahora = time.monotonic()
            if firma != firma_anterior:
                firma_anterior, estable_desde = firma, ahora
            elif ahora - estable_desde >= self.segundos_estable:
                return
            if ahora > limite:
                log.warning("Los datos siguen cambiando tras %d s: se captura de todos modos", self.espera_carga)
                return
            time.sleep(0.5)

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
