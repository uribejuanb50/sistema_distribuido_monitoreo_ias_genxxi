"""Extracción del reporte Power BI de VIKI.

Dos pasos separados:
1. CAPTURA (navegador.py): Selenium abre el reporte y ejecuta script_captura.js, que
   devuelve cada visual con su número de bloque y sus textos ("3.469", "8,6 %"...), pero
   SIN nombres. También se puede leer una captura guardada (modo sin conexión), incluido
   el .txt de la inyección por consola.
2. INTERPRETACIÓN (este módulo, funciones puras): mapa_reporte.json dice qué es cada
   número (bloque 59 = Usuarios del total, etc.) y se verifican las identidades contables
   del reporte (total = presencial + telefónico, acciones por sesión = acciones / sesiones,
   % usabilidad = sesiones con llamada / llamadas...). Si alguna falla, el mapa está
   desalineado (cambió el diseño del reporte) y NO se envía nada al nodo padre.

Para leer otro reporte se cambia mapa_reporte.json; main.py no cambia.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from comun.contrato import Observacion, ahora_iso
from comun.extractor_base import ErrorExtraccion, ExtractorBase
from comun.texto import a_slug, normalizar_texto
from comun.utilidades import cargar_json, resolver_ruta, sin_comentarios

log = logging.getLogger(__name__)
DIR_NODO = Path(__file__).resolve().parent

MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
         "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}
PATRON_FECHA = re.compile(r"(\d{1,2}) de ([a-z]+) de (\d{4})")
PATRON_TIEMPO = re.compile(r"^(-?)(\d{1,2}):(\d{2})(?::(\d{2}))?$")
PATRON_NUMERO = re.compile(r"^-?(\d{1,3}(\.\d{3})+|\d+)(,\d+)?$")
PATRON_BLOQUE = re.compile(r"Bloque (\d+)$")


# ---------------------------------------------------------------- números y textos

def a_numero(texto: str) -> float | None:
    """Convierte un texto del reporte en número (formato colombiano).

    '2.363.989' -> 2363989 · '8,6 %' -> 8.6 · '-5,2 %' -> -5.2 · '09:42' -> 582 (segundos).
    Devuelve None si no es un número ('Semana 1', '0 mil', 'Total'...).
    """
    limpio = texto.replace(" ", " ").strip()
    tiempo = PATRON_TIEMPO.match(limpio)
    if tiempo:
        signo, a, b, c = tiempo.groups()
        segundos = int(a) * 60 + int(b) if c is None else int(a) * 3600 + int(b) * 60 + int(c)
        return float(-segundos if signo else segundos)
    limpio = limpio.replace("%", "").replace(" ", "")
    if not PATRON_NUMERO.match(limpio):
        return None
    return float(limpio.replace(".", "").replace(",", "."))


def es_duplicado(texto: str) -> bool:
    """Las etiquetas de ejes de Power BI llegan repetidas: '20 mil20 mil', 'FECHAFECHA'."""
    mitad = len(texto) // 2
    return len(texto) >= 2 and len(texto) % 2 == 0 and texto[:mitad] == texto[mitad:]


def sin_duplicar(texto: str) -> str:
    return texto[:len(texto) // 2] if es_duplicado(texto) else texto


def leer_fecha(texto: str) -> date | None:
    """'Actualizado: miércoles, 28 de octubre de 2026' -> date(2026, 10, 28)."""
    coincidencia = PATRON_FECHA.search(normalizar_texto(texto))
    if coincidencia is None or coincidencia.group(2) not in MESES:
        return None
    dia, mes, anio = coincidencia.groups()
    return date(int(anio), MESES[mes], int(dia))


# ---------------------------------------------------------------- captura

@dataclass(frozen=True)
class Visual:
    bloque: int | None
    titulo: str | None
    textos: tuple[str, ...]
    filas: tuple[tuple[str, ...], ...] = ()

    @property
    def clave(self) -> str:
        return f"bloque {self.bloque}" if self.bloque is not None else f"'{self.titulo}'"

    @property
    def encabezados(self) -> set[str]:
        """Textos que podrían ser encabezados, normalizados (para identificar tablas)."""
        celdas = self.filas[0] if self.filas else self.textos
        return {normalizar_texto(celda) for celda in celdas}


@dataclass(frozen=True)
class Captura:
    capturado_en: str
    origen: str
    visuales: tuple[Visual, ...]


def normalizar_captura(datos: Any, origen: str) -> Captura:
    """Acepta el formato del nodo (dict con 'visuales') o el de la inyección por consola (lista)."""
    if isinstance(datos, dict) and isinstance(datos.get("visuales"), list):
        visuales = tuple(
            Visual(v.get("bloque"), v.get("titulo"), tuple(map(str, v.get("textos", []))),
                   tuple(tuple(map(str, fila)) for fila in v.get("filas", [])))
            for v in datos["visuales"]
        )
        return Captura(datos.get("capturado_en") or ahora_iso(), datos.get("origen") or origen, visuales)
    if isinstance(datos, list):
        visuales = []
        for elemento in datos:
            seccion = str(elemento.get("Sección / Visual", ""))
            numero = PATRON_BLOQUE.search(seccion)
            visuales.append(Visual(int(numero.group(1)) if numero else None, None if numero else seccion,
                                   tuple(map(str, elemento.get("Valores Extraídos", [])))))
        return Captura(ahora_iso(), origen, tuple(visuales))
    raise ErrorExtraccion("Formato de captura no reconocido (se espera la lista de la consola o el JSON del nodo)")


def cargar_captura(ruta: Path) -> Captura:
    """Lee una captura guardada. Admite texto extra después del JSON (como el .txt de la consola)."""
    try:
        texto = ruta.read_text(encoding="utf-8")
    except OSError as error:
        raise ErrorExtraccion(f"No se pudo leer la captura {ruta}: {error}") from error
    inicios = [posicion for posicion in (texto.find("["), texto.find("{")) if posicion >= 0]
    try:
        datos, _ = json.JSONDecoder().raw_decode(texto[min(inicios):]) if inicios else (None, 0)
    except ValueError as error:
        raise ErrorExtraccion(f"La captura {ruta.name} no tiene un JSON válido: {error}") from error
    return normalizar_captura(datos, ruta.name)


# ---------------------------------------------------------------- interpretación

@dataclass
class Resultado:
    fecha_actualizacion: date | None = None
    observaciones: list[Observacion] = field(default_factory=list)
    asignaciones: dict[str, str] = field(default_factory=dict)            # visual -> cómo se interpretó
    validaciones: list[tuple[str, bool, str]] = field(default_factory=list)  # (regla, ok, detalle)
    avisos: list[str] = field(default_factory=list)

    @property
    def errores(self) -> list[str]:
        return [f"{regla} ({detalle})" for regla, ok, detalle in self.validaciones if not ok]


def interpretar(captura: Captura, mapa: dict[str, Any]) -> Resultado:
    """Convierte la captura en observaciones con nombre, según el mapa, y las valida."""
    return _Interprete(captura, mapa).ejecutar()


class _Interprete:
    def __init__(self, captura: Captura, mapa: dict[str, Any]) -> None:
        self.captura = captura
        self.mapa = mapa
        self.metricas = sin_comentarios(mapa["metricas"])
        self.segmentos = mapa["segmentos"]
        self.metas = sin_comentarios(mapa.get("metas", {}))
        excluir = mapa.get("excluir_encabezados", {}).get("encabezados", [])
        self.excluidos = {normalizar_texto(e) for e in excluir}
        self.valores: dict[str, float] = {}  # "total.usuarios" -> valor (para las validaciones)
        self.r = Resultado()

    def ejecutar(self) -> Resultado:
        self.r.fecha_actualizacion = self._fecha_actualizacion()
        self._marcar_excluidos()
        self._tarjetas()
        self._validar_tarjetas()
        for especificacion in self.mapa.get("tablas", []):
            self._tabla(especificacion)
        for especificacion in self.mapa.get("graficos", []):
            self._grafico(especificacion)
        return self.r

    # ------------------------------------------------------------ utilidades

    def _tolerancia(self, metrica: str) -> float:
        """Media unidad del último decimal mostrado: lo que puede variar por redondeo."""
        return 0.5 * 10 ** -self.metricas[metrica].get("decimales", 0) + 1e-6

    def _comprobar(self, regla: str, observado: float, esperado: float, metrica: str) -> None:
        def fmt(x: float) -> str:
            return f"{x:,.0f}" if float(x).is_integer() else f"{x:,.3f}"
        ok = abs(observado - esperado) <= self._tolerancia(metrica)
        self.r.validaciones.append((regla, ok, f"{fmt(observado)} vs {fmt(esperado)}"))

    def _agregar(self, ruta: str, metrica: str, valor: float, tipo_periodo: str, fecha: date,
                 origen: str, texto: str, contexto: str, **atributos: Any) -> None:
        definicion = self.metricas[metrica]
        kpi_id = f"{self.mapa['prefijo_kpi']}.{ruta}.{metrica}"
        self.r.observaciones.append(Observacion(
            kpi_id=kpi_id,
            canal=self.mapa["canal"],
            nombre_kpi=f"{definicion['nombre']} · {contexto}",
            fecha_referencia=fecha.isoformat(),
            tipo_periodo=tipo_periodo,
            valor=valor,
            unidad=definicion["unidad"],
            meta=self.metas.get(kpi_id),
            fecha_captura=self.captura.capturado_en,
            atributos={"fuente": "powerbi", "origen": origen, "texto_original": texto,
                       "fecha_actualizacion_reporte": self.r.fecha_actualizacion.isoformat(), **atributos},
        ))

    def _es_excluido(self, visual: Visual) -> bool:
        return bool(self.excluidos) and self.excluidos <= visual.encabezados

    # ------------------------------------------------------------ partes del reporte

    def _fecha_actualizacion(self) -> date:
        for visual in self.captura.visuales:
            for texto in visual.textos:
                if "actualizado" in normalizar_texto(texto) and (fecha := leer_fecha(texto)):
                    self.r.asignaciones[visual.clave] = f"fecha de actualización = {fecha.isoformat()}"
                    return fecha
        raise ErrorExtraccion("No se encontró el texto 'Actualizado: <fecha>' en la captura: sin él no se sabe "
                              "a qué fecha corresponden los datos")

    def _marcar_excluidos(self) -> None:
        for visual in self.captura.visuales:
            if self._es_excluido(visual):
                self.r.asignaciones[visual.clave] = "EXCLUIDO: datos por persona (no se leen)"

    def _tarjetas(self) -> None:
        por_bloque = {visual.bloque: visual for visual in self.captura.visuales if visual.bloque is not None}
        for bloque_texto, (segmento, metrica) in sin_comentarios(self.mapa["tarjetas"]).items():
            regla = f"tarjeta bloque {bloque_texto} = {segmento}.{metrica}"
            visual = por_bloque.get(int(bloque_texto))
            texto = next((t for t in visual.textos if a_numero(t) is not None), None) if visual else None
            if texto is None:
                self.r.validaciones.append((regla, False, "el bloque no está en la captura o no trae un número"))
                continue
            espera_porcentaje = self.metricas[metrica]["unidad"] == "porcentaje"
            if ("%" in texto) != espera_porcentaje:
                esperado = "un porcentaje" if espera_porcentaje else "un número sin %"
                self.r.validaciones.append((regla, False, f"trae '{texto}' y se esperaba {esperado}"))
                continue
            valor = a_numero(texto)
            self.valores[f"{segmento}.{metrica}"] = valor
            self.r.asignaciones[visual.clave] = f"{self.segmentos[segmento]} · {self.metricas[metrica]['nombre']}"
            self._agregar(f"mtd.{segmento}", metrica, valor, "acumulado", self.r.fecha_actualizacion,
                          visual.clave, texto, f"{self.segmentos[segmento]} (mes a la fecha)", segmento=segmento)

    def _validar_tarjetas(self) -> None:
        reglas = self.mapa.get("validaciones", {})
        suma = reglas.get("suma_segmentos")
        if suma:
            for metrica in suma["metricas"]:
                total = self.valores.get(f"{suma['total']}.{metrica}")
                partes = [self.valores.get(f"{parte}.{metrica}") for parte in suma["partes"]]
                if total is not None and None not in partes:
                    self._comprobar(f"{metrica}: {suma['total']} = {' + '.join(suma['partes'])}",
                                    total, sum(partes), metrica)
        cocientes = reglas.get("cocientes", {})
        for segmento in cocientes.get("segmentos", []):
            for regla in cocientes.get("reglas", []):
                resultado = self.valores.get(f"{segmento}.{regla['resultado']}")
                numerador = self.valores.get(f"{segmento}.{regla['numerador']}")
                denominador = self.valores.get(f"{segmento}.{regla['denominador']}")
                if None in (resultado, numerador, denominador) or not denominador:
                    continue
                self._comprobar(f"{segmento}: {regla['resultado']} = {regla['numerador']} / {regla['denominador']}",
                                resultado, regla["factor"] * numerador / denominador, regla["resultado"])

    # ------------------------------------------------------------ tablas

    def _tabla(self, especificacion: dict[str, Any]) -> None:
        nombre = especificacion["nombre"]
        requeridos = {normalizar_texto(e) for e in especificacion["encabezados_requeridos"]}
        visual = next((v for v in self.captura.visuales
                       if requeridos <= v.encabezados and not self._es_excluido(v)), None)
        if visual is None:
            self.r.avisos.append(f"Tabla '{nombre}': no aparece en la captura")
            return
        filas = self._filas(visual, especificacion)
        if filas is None:
            return
        if not any(valores for _, valores in filas):
            self.r.asignaciones[visual.clave] = f"tabla '{nombre}' SIN VALORES (solo etiquetas)"
            self.r.avisos.append(f"Tabla '{nombre}' ({visual.clave}): se capturaron las etiquetas pero no los "
                                 "números (la tabla se lee por sus roles de accesibilidad al capturar con Selenium)")
            return
        if especificacion["tipo"] == "semanal":
            self._tabla_semanal(especificacion, visual, filas)
        else:
            self._tabla_dimension(especificacion, visual, filas)

    def _filas(self, visual: Visual, especificacion: dict[str, Any]
               ) -> list[tuple[str, dict[str, tuple[float, str]]]] | None:
        """Filas de la tabla como (etiqueta, {métrica: (valor, texto)})."""
        columnas = {normalizar_texto(k): v for k, v in sin_comentarios(especificacion["columnas"]).items()}
        if visual.filas:  # captura con roles de accesibilidad: filas ya separadas
            encabezados, datos = list(visual.filas[0]), [(fila[0], list(fila[1:])) for fila in visual.filas[1:]]
        else:  # captura plana (consola): N encabezados y luego etiqueta + números de cada fila
            n = 1 + len(columnas)
            encabezados, datos = list(visual.textos[:n]), []
            for texto in visual.textos[n:]:
                if a_numero(texto) is None:
                    datos.append((texto, []))
                elif datos:
                    datos[-1][1].append(texto)
        esperados = set(columnas) | {normalizar_texto(especificacion["encabezados_requeridos"][0])}
        if {normalizar_texto(e) for e in encabezados} != esperados:
            self.r.validaciones.append((f"tabla '{especificacion['nombre']}': encabezados", False,
                                        f"se encontraron {encabezados}"))
            return None
        metricas_por_posicion = [columnas.get(normalizar_texto(e)) for e in encabezados[1:]]
        filas = []
        for etiqueta, celdas in datos:
            valores = {}
            for metrica, celda in zip(metricas_por_posicion, celdas):
                if metrica is not None and (valor := a_numero(celda)) is not None:
                    valores[metrica] = (valor, celda)
            filas.append((etiqueta.strip(), valores))
        return filas

    def _tabla_semanal(self, especificacion: dict[str, Any], visual: Visual,
                       filas: list[tuple[str, dict[str, tuple[float, str]]]]) -> None:
        actualizacion = self.r.fecha_actualizacion
        inicio_mes = actualizacion.replace(day=1)
        conteos = set(especificacion.get("metricas_conteo", []))
        sumas: dict[str, float] = defaultdict(float)
        semanas, incompletas, total = 0, [], None
        for etiqueta, valores in filas:
            if normalizar_texto(etiqueta) == "total":
                total = valores
                continue
            numero = re.search(r"\d+", etiqueta)
            if numero is None:
                self.r.avisos.append(f"Tabla semanal: fila no reconocida '{etiqueta}'")
                continue
            n = int(numero.group())
            fecha = inicio_mes + timedelta(days=7 * (n - 1))
            completa = actualizacion.day >= 7 * n
            semanas += 1
            if not completa:
                incompletas.append(etiqueta)
            for metrica, (valor, texto) in valores.items():
                if metrica in conteos:
                    sumas[metrica] += valor
                    if not completa:  # un conteo parcial parecería una caída falsa
                        continue
                self._agregar("semanal", metrica, valor, "semanal", fecha, f"{visual.clave} · {etiqueta}", texto,
                              f"{etiqueta} de {inicio_mes:%Y-%m}", semana_mes=n, semana_completa=completa)
        nota = f"; sin terminar: {', '.join(incompletas)} (solo razones)" if incompletas else ""
        self.r.asignaciones[visual.clave] = f"tabla semanal: {semanas} semanas{nota}"
        if total is None:
            return
        for metrica in conteos:
            if metrica in total:
                self._comprobar(f"semanas: suma de {metrica} = fila Total", sumas[metrica], total[metrica][0], metrica)
        for metrica in especificacion.get("total_a_mes", []):
            if metrica in total:
                valor, texto = total[metrica]
                self.valores[f"total.{metrica}"] = valor
                self._agregar("mtd.total", metrica, valor, "acumulado", actualizacion, f"{visual.clave} · Total",
                              texto, f"{self.segmentos['total']} (mes a la fecha)", segmento="total")
        self._validar_total_contra_tarjetas(especificacion["nombre"], total)

    def _tabla_dimension(self, especificacion: dict[str, Any], visual: Visual,
                         filas: list[tuple[str, dict[str, tuple[float, str]]]]) -> None:
        dimension = especificacion["dimension"]
        emitidas = 0
        for etiqueta, valores in filas:
            if not etiqueta:
                continue
            if normalizar_texto(etiqueta) == "total":
                self._validar_total_contra_tarjetas(especificacion["nombre"], valores)
                continue
            for metrica, (valor, texto) in valores.items():
                self._agregar(f"mtd.{dimension}.{a_slug(etiqueta)}", metrica, valor, "acumulado",
                              self.r.fecha_actualizacion, f"{visual.clave} · {etiqueta}", texto,
                              f"{etiqueta} (mes a la fecha)", dimension=dimension, valor_dimension=etiqueta)
                emitidas += 1
        self.r.asignaciones[visual.clave] = f"tabla '{especificacion['nombre']}': {emitidas} valores por {dimension}"

    def _validar_total_contra_tarjetas(self, nombre: str, total: dict[str, tuple[float, str]]) -> None:
        """La fila Total de una tabla debe coincidir con las tarjetas del total."""
        for metrica, (valor, _) in total.items():
            if f"total.{metrica}" in self.valores:
                self._comprobar(f"tabla '{nombre}': Total {metrica} = tarjeta", valor,
                                self.valores[f"total.{metrica}"], metrica)

    # ------------------------------------------------------------ gráficos

    def _grafico(self, especificacion: dict[str, Any]) -> None:
        patron = re.compile(especificacion["patron_categoria"])
        for visual in self.captura.visuales:
            categorias = list(dict.fromkeys(sin_duplicar(t) for t in visual.textos if patron.search(sin_duplicar(t))))
            if categorias:
                break
        else:
            self.r.avisos.append(f"Gráfico '{especificacion['nombre']}': no aparece en la captura")
            return
        etiquetas = [t for t in visual.textos if not es_duplicado(t) and a_numero(t) is not None]
        series, n = especificacion["series"], len(categorias)
        if len(etiquetas) != n * len(series):
            self.r.avisos.append(f"Gráfico '{especificacion['nombre']}': se esperaban {n * len(series)} valores y "
                                 f"hay {len(etiquetas)}; no se interpreta")
            return
        enviadas = []
        for indice, metrica in enumerate(series):
            textos = etiquetas[indice * n:(indice + 1) * n]
            if metrica is None:
                continue
            valores = [a_numero(t) for t in textos]
            if (contra := especificacion.get("validar_suma", {}).get(metrica)) and contra in self.valores:
                self._comprobar(f"gráfico '{especificacion['nombre']}': suma de {metrica} = {contra}",
                                sum(valores), self.valores[contra], metrica)
            for categoria, valor, texto in zip(categorias, valores, textos):
                self._agregar(f"mtd.{especificacion['dimension']}.{a_slug(categoria)}", metrica, valor, "acumulado",
                              self.r.fecha_actualizacion, f"{visual.clave} · {categoria}", texto,
                              f"{categoria} (mes a la fecha)", dimension=especificacion["dimension"],
                              valor_dimension=categoria)
            enviadas.append(metrica)
        sin_identificar = sum(1 for metrica in series if metrica is None)
        self.r.asignaciones[visual.clave] = (f"gráfico '{especificacion['nombre']}': {', '.join(enviadas)} por "
                                             f"{n} categorías; {sin_identificar} serie(s) sin identificar")


# ---------------------------------------------------------------- extractor del nodo

class ExtractorPowerBI(ExtractorBase):
    tipo_fuente = "powerbi"

    def __init__(self, mapa: dict[str, Any], navegador: Any = None, ruta_captura: Path | None = None,
                 ruta_guardado: Path | None = None) -> None:
        if navegador is None and ruta_captura is None:
            raise ValueError("Se necesita un navegador o una captura guardada")
        self.mapa = mapa
        self.navegador = navegador
        self.ruta_captura = ruta_captura
        self.ruta_guardado = ruta_guardado
        self._ultimos_avisos: list[str] | None = None

    @property
    def fuente(self) -> str:
        return f"captura {self.ruta_captura.name}" if self.ruta_captura else "reporte Power BI VIKI"

    def obtener_captura(self) -> Captura:
        if self.ruta_captura is not None:
            return cargar_captura(self.ruta_captura)
        datos = self.navegador.capturar()
        if self.ruta_guardado is not None:  # deja la última captura para revisarla con calibrar.py
            self.ruta_guardado.parent.mkdir(parents=True, exist_ok=True)
            self.ruta_guardado.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
        return normalizar_captura(datos, self.navegador.url)

    def extraer(self) -> list[Observacion]:
        resultado = interpretar(self.obtener_captura(), self.mapa)
        if resultado.avisos != self._ultimos_avisos:
            self._ultimos_avisos = resultado.avisos
            for aviso in resultado.avisos:
                log.warning(aviso)
        if resultado.errores:
            raise ErrorExtraccion(f"La captura no pasó {len(resultado.errores)} validaciones (¿cambió el diseño del "
                                  f"reporte?). No se envía nada. Revisa con calibrar.py. Primeras: "
                                  + "; ".join(resultado.errores[:3]))
        log.info("Reporte actualizado al %s: %d observaciones, %d validaciones OK",
                 resultado.fecha_actualizacion, len(resultado.observaciones), len(resultado.validaciones))
        return resultado.observaciones

    def cerrar(self) -> None:
        if self.navegador is not None:
            self.navegador.cerrar()


def crear_extractor(config: dict[str, Any]) -> ExtractorPowerBI:
    """Punto de entrada que usa main.py: construye el extractor desde config.json."""
    mapa = cargar_json(config.get("ruta_mapa") or DIR_NODO / "mapa_reporte.json")
    if config.get("captura_sin_conexion"):
        return ExtractorPowerBI(mapa, ruta_captura=resolver_ruta(config["captura_sin_conexion"]))
    from navegador import NavegadorPowerBI  # Selenium solo se carga si se usa el navegador

    carpeta_perfil = config.get("carpeta_perfil_navegador")
    navegador = NavegadorPowerBI(
        url=config["url_reporte"],
        carpeta_perfil=resolver_ruta(carpeta_perfil) if carpeta_perfil else None,
        navegador=config.get("navegador", "edge"),
        visible=config.get("navegador_visible", True),
        espera_carga_segundos=config.get("espera_carga_segundos", 90),
        espera_inicio_sesion_segundos=config.get("espera_inicio_sesion_segundos", 300),
        segundos_estable=config.get("segundos_estable", 4),
        minimo_visuales=config.get("minimo_visuales", 20),
    )
    guardado = config.get("guardar_ultima_captura")
    return ExtractorPowerBI(mapa, navegador=navegador, ruta_guardado=resolver_ruta(guardado) if guardado else None)
