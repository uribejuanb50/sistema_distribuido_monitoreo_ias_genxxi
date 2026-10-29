"""Extracción del Excel de cumplimiento de metas (formato "matricial" por bloques).

Cómo viene el archivo (NO es una tabla limpia):

    A (segmento)  B (métrica)               C      D       E      F       G      H      ...
    Pospago       Meta Driver               1827   1500*   1759   1715*   3586   3215*
                  Linea Base                1615           1488           3103
                  Meta Jugada/Cumplimiento  1772   0.85    1645   1.04    3417   0.94
                  Unidades Adicionales      157    10      12     12      3      4
    Tv + Voz      (mismo bloque de 4 filas)
                                            └─ mes 1 ┘     └─ mes 2 ┘     └ acum 1-2 ┘

- Cada periodo ocupa un PAR de columnas: columna META (tiene Linea Base) y columna
  REAL (sin Linea Base). En la columna REAL:
    fila "Meta Driver"    -> valor real logrado (* celda combinada con la fila de Linea Base)
    fila "Meta Jugada..." -> cumplimiento = real / meta jugada (fracción: 1.0 = 100 %)
    fila "Unidades Ad..." -> real − linea base
- Los pares alternan meses y acumulados: M1, M2, Acum(1-2), M3, Acum(1-3), ..., M11,
  Acum(1-11), M12. Un par es ACUMULADO si su Meta Driver = suma de los meses anteriores.
- El archivo no trae fechas: el mes se deduce por la posición (config: anio, mes_inicial).

Para leer un archivo con otro formato se modifica SOLO este módulo; main.py no cambia.
"""
from __future__ import annotations

import logging
import math
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

from comun.contrato import Observacion, ahora_iso
from comun.extractor_base import ErrorExtraccion, ExtractorBase
from comun.utilidades import resolver_ruta

log = logging.getLogger(__name__)

METRICAS_REQUERIDAS = ("meta_driver", "linea_base", "meta_jugada", "unidades_adicionales")
ETIQUETAS_POR_DEFECTO = {
    "Meta Driver": "meta_driver",
    "Linea Base": "linea_base",
    "Meta Jugada/Cumplimiento": "meta_jugada",
    "Unidades Adicionales": "unidades_adicionales",
}
TOLERANCIA_SUMA = 0.5            # unidades; para comparar acumulados con la suma de meses
TOLERANCIA_CUMPLIMIENTO = 0.001  # fracción; para comparar el % del Excel con el calculado

Celdas = list[list[Any]]


@dataclass(frozen=True)
class Bloque:
    """Grupo de filas de un segmento (p. ej. 'Pospago') y la fila de cada métrica."""

    segmento: str
    filas: dict[str, int]  # métrica -> número de fila (1 = primera fila)


@dataclass(frozen=True)
class ParColumnas:
    """Columnas (meta, real) de un periodo."""

    col_meta: int
    col_real: int | None   # None si el periodo aún no tiene columna de real
    tipo_periodo: str      # mensual | acumulado
    numero_mes: int        # mes del periodo; en acumulados, último mes incluido


@dataclass(frozen=True)
class Registro:
    """Una fila "ordenada" (tidy): un segmento en un periodo."""

    segmento: str
    tipo_periodo: str
    numero_mes: int
    meta_driver: float | None
    linea_base: float | None
    meta_jugada: float | None
    unidades_adicionales_meta: float | None
    real: float | None
    cumplimiento: float | None  # fracción tal como viene en el Excel
    unidades_adicionales_real: float | None


# ---------------------------------------------------------------- utilidades de texto/número

def normalizar_texto(texto: Any) -> str:
    """'  LÍNEA   Base ' -> 'linea base' (sin tildes, minúsculas, espacios simples)."""
    sin_tildes = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sin_tildes).strip().lower()


def a_slug(texto: str) -> str:
    """'Tv + Voz' -> 'tv_voz' (para construir kpi_id estables)."""
    return re.sub(r"[^a-z0-9]+", "_", normalizar_texto(texto)).strip("_")


def a_numero(valor: Any) -> float | None:
    """Convierte el contenido de una celda en número; None si está vacía o no es numérica."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor) if math.isfinite(valor) else None
    if isinstance(valor, str):
        texto = valor.strip().replace(" ", "")
        for candidato in (texto, texto.replace(",", ".")):
            try:
                return float(candidato)
            except ValueError:
                continue
    return None


def valor_celda(celdas: Celdas, fila: int, col: int) -> Any:
    """Valor de la celda (fila, col) con índices desde 1, o None si está fuera de rango."""
    if 1 <= fila <= len(celdas) and 1 <= col <= len(celdas[fila - 1]):
        return celdas[fila - 1][col - 1]
    return None


def numero_celda(celdas: Celdas, fila: int, col: int) -> float | None:
    return a_numero(valor_celda(celdas, fila, col))


# ---------------------------------------------------------------- pasos de la extracción

def leer_celdas(ruta: Path, hoja: str | None = None) -> Celdas:
    """Paso 1: lee todos los valores de la hoja como una matriz (lista de filas)."""
    if not ruta.exists():
        raise ErrorExtraccion(f"No existe el archivo: {ruta}")
    try:
        libro = openpyxl.load_workbook(ruta, data_only=True)
    except PermissionError as error:
        raise ErrorExtraccion(f"El archivo está bloqueado (¿abierto en otro programa?): {ruta}") from error
    except (InvalidFileException, zipfile.BadZipFile, OSError) as error:
        raise ErrorExtraccion(f"No se pudo abrir el Excel {ruta}: {error}") from error
    try:
        if hoja is not None and hoja not in libro.sheetnames:
            raise ErrorExtraccion(f"La hoja {hoja!r} no existe. Hojas: {libro.sheetnames}")
        hoja_excel = libro[hoja] if hoja else libro.worksheets[0]
        return [list(fila) for fila in hoja_excel.iter_rows(values_only=True)]
    finally:
        libro.close()


def detectar_bloques(celdas: Celdas, etiquetas: dict[str, str]) -> list[Bloque]:
    """Paso 2: encuentra los bloques de segmentos (columna A) y sus métricas (columna B)."""
    etiquetas_norm = {normalizar_texto(texto): metrica for texto, metrica in etiquetas.items()}
    bloques: list[Bloque] = []
    segmento: str | None = None
    filas: dict[str, int] = {}

    for numero_fila in range(1, len(celdas) + 1):
        nombre_segmento = valor_celda(celdas, numero_fila, 1)
        etiqueta = valor_celda(celdas, numero_fila, 2)
        if nombre_segmento is not None and str(nombre_segmento).strip():
            if segmento is not None:
                bloques.append(Bloque(segmento, filas))
            segmento, filas = str(nombre_segmento).strip(), {}
        if etiqueta is None or not str(etiqueta).strip():
            continue
        metrica = etiquetas_norm.get(normalizar_texto(etiqueta))
        if metrica is None:
            log.warning("Fila %d: etiqueta no reconocida %r (se ignora)", numero_fila, etiqueta)
            continue
        if segmento is None:
            raise ErrorExtraccion(f"Fila {numero_fila}: métrica {etiqueta!r} sin segmento en la columna A")
        filas[metrica] = numero_fila
    if segmento is not None:
        bloques.append(Bloque(segmento, filas))

    if not bloques:
        raise ErrorExtraccion("No se encontró ningún bloque (segmento en columna A + métricas en columna B)")
    for bloque in bloques:
        faltantes = [m for m in METRICAS_REQUERIDAS if m not in bloque.filas]
        if faltantes:
            raise ErrorExtraccion(f"Al bloque {bloque.segmento!r} le faltan las métricas: {faltantes}")
    return bloques


def detectar_columnas(celdas: Celdas, bloque: Bloque, columna_inicio: int = 3) -> list[ParColumnas]:
    """Paso 3: recorre las columnas y arma los pares (meta, real) de cada periodo.

    Usa el primer bloque como referencia; los demás bloques comparten la misma estructura.
    """
    fila_md = bloque.filas["meta_driver"]
    fila_lb = bloque.filas["linea_base"]
    filas_real = (fila_md, bloque.filas["meta_jugada"], bloque.filas["unidades_adicionales"])
    ultima_col = max((len(fila) for fila in celdas), default=0)

    pares: list[ParColumnas] = []
    meses = 0
    suma_metas_mensuales = 0.0
    col = columna_inicio
    while col <= ultima_col:
        if numero_celda(celdas, fila_lb, col) is None:  # sin Linea Base => no es columna de meta
            col += 1
            continue
        siguiente = col + 1
        tiene_real = (
            siguiente <= ultima_col
            and numero_celda(celdas, fila_lb, siguiente) is None
            and any(valor_celda(celdas, fila, siguiente) is not None for fila in filas_real)
        )
        meta_driver = numero_celda(celdas, fila_md, col)
        es_acumulado = (
            meses >= 2
            and meta_driver is not None
            and abs(meta_driver - suma_metas_mensuales) <= TOLERANCIA_SUMA
        )
        if es_acumulado:
            pares.append(ParColumnas(col, siguiente if tiene_real else None, "acumulado", meses))
        else:
            meses += 1
            suma_metas_mensuales += meta_driver or 0.0
            pares.append(ParColumnas(col, siguiente if tiene_real else None, "mensual", meses))
        col = siguiente + 1 if tiene_real else siguiente

    if not pares:
        raise ErrorExtraccion("No se encontraron columnas de periodos (revisa 'columna_inicio')")
    return pares


def construir_registros(celdas: Celdas, bloques: list[Bloque], pares: list[ParColumnas]) -> list[Registro]:
    """Paso 4: convierte la matriz en registros ordenados (una fila por segmento y periodo)."""
    registros = []
    for bloque in bloques:
        f = bloque.filas
        for par in pares:
            def real(fila: int) -> float | None:
                return numero_celda(celdas, fila, par.col_real) if par.col_real else None

            registros.append(Registro(
                segmento=bloque.segmento,
                tipo_periodo=par.tipo_periodo,
                numero_mes=par.numero_mes,
                meta_driver=numero_celda(celdas, f["meta_driver"], par.col_meta),
                linea_base=numero_celda(celdas, f["linea_base"], par.col_meta),
                meta_jugada=numero_celda(celdas, f["meta_jugada"], par.col_meta),
                unidades_adicionales_meta=numero_celda(celdas, f["unidades_adicionales"], par.col_meta),
                real=real(f["meta_driver"]),
                cumplimiento=real(f["meta_jugada"]),
                unidades_adicionales_real=real(f["unidades_adicionales"]),
            ))
    return registros


def revisar_calidad(registros: list[Registro]) -> dict[tuple[str, str, int], str]:
    """Paso 5: detecta registros que NO deben enviarse. Devuelve {(segmento, tipo, mes): motivo}."""
    problemas: dict[tuple[str, str, int], str] = {}
    mensuales = {(r.segmento, r.numero_mes): r for r in registros if r.tipo_periodo == "mensual"}

    for r in registros:
        clave = (r.segmento, r.tipo_periodo, r.numero_mes)
        if r.real is None:
            problemas[clave] = "sin valor real (periodo aún no reportado)"
            continue
        if not r.meta_jugada:
            problemas[clave] = "sin meta jugada: no se puede calcular el cumplimiento"
            continue
        if r.tipo_periodo == "acumulado":
            meses = [mensuales.get((r.segmento, mes)) for mes in range(1, r.numero_mes + 1)]
            if any(m is None or m.real is None for m in meses):
                problemas[clave] = "acumulado incompleto: falta el real de algún mes incluido"
                continue
            suma_real = sum(m.real for m in meses)
            if abs(suma_real - r.real) > TOLERANCIA_SUMA:
                problemas[clave] = f"real acumulado ({r.real:g}) distinto de la suma de meses ({suma_real:g})"
                continue
        if r.cumplimiento is not None and abs(r.cumplimiento - r.real / r.meta_jugada) > TOLERANCIA_CUMPLIMIENTO:
            log.warning("%s %s mes %d: el %% de cumplimiento del Excel (%.4f) no coincide con real/meta (%.4f); "
                        "se usa el calculado", r.segmento, r.tipo_periodo, r.numero_mes,
                        r.cumplimiento, r.real / r.meta_jugada)
    return problemas


def fecha_del_mes(anio: int, mes_inicial: int, numero_mes: int) -> str:
    """Mes 1 = (anio, mes_inicial). Devuelve el primer día del mes en formato YYYY-MM-DD."""
    indice = (mes_inicial - 1) + (numero_mes - 1)
    return date(anio + indice // 12, indice % 12 + 1, 1).isoformat()


def a_observaciones(
    registros: list[Registro],
    problemas: dict[tuple[str, str, int], str],
    anio: int,
    mes_inicial: int,
    fecha_captura: str,
) -> list[Observacion]:
    """Paso 6: convierte los registros válidos al contrato común (2 KPI por registro)."""
    observaciones = []
    for r in registros:
        if (r.segmento, r.tipo_periodo, r.numero_mes) in problemas:
            continue
        segmento = a_slug(r.segmento)
        fecha = fecha_del_mes(anio, mes_inicial, r.numero_mes)
        atributos = {
            "segmento": r.segmento,
            "numero_mes": r.numero_mes,
            "periodo": fecha[:7],
            "meta_driver": r.meta_driver,
            "linea_base": r.linea_base,
            "meta_jugada": r.meta_jugada,
            "real": r.real,
            "unidades_adicionales_meta": r.unidades_adicionales_meta,
            "unidades_adicionales_real": r.unidades_adicionales_real,
            "cumplimiento_excel": r.cumplimiento,
        }
        comunes = dict(canal=r.segmento, fecha_referencia=fecha, tipo_periodo=r.tipo_periodo,
                       fecha_captura=fecha_captura, atributos=atributos)
        observaciones.append(Observacion(
            kpi_id=f"{segmento}.cumplimiento_{r.tipo_periodo}",
            nombre_kpi=f"Cumplimiento {r.tipo_periodo} vs meta jugada",
            valor=round(r.real / r.meta_jugada * 100, 4),
            unidad="porcentaje",
            meta=100.0,
            **comunes,
        ))
        observaciones.append(Observacion(
            kpi_id=f"{segmento}.real_{r.tipo_periodo}",
            nombre_kpi=f"Real {r.tipo_periodo}",
            valor=r.real,
            unidad="unidades",
            meta=r.meta_jugada,
            **comunes,
        ))
    return observaciones


# ---------------------------------------------------------------- extractor del nodo

class ExtractorExcelCumplimiento(ExtractorBase):
    """Aplica los pasos 1-6 sobre el Excel configurado."""

    tipo_fuente = "excel"

    def __init__(self, ruta: Path, hoja: str | None, anio: int, mes_inicial: int = 1,
                 columna_inicio: int = 3, etiquetas: dict[str, str] | None = None) -> None:
        self.ruta = ruta
        self.hoja = hoja
        self.anio = anio
        self.mes_inicial = mes_inicial
        self.columna_inicio = columna_inicio
        self.etiquetas = etiquetas or ETIQUETAS_POR_DEFECTO
        self._ultimos_problemas: dict[tuple[str, str, int], str] | None = None

    @property
    def fuente(self) -> str:
        return self.ruta.name

    def extraer(self) -> list[Observacion]:
        celdas = leer_celdas(self.ruta, self.hoja)
        bloques = detectar_bloques(celdas, self.etiquetas)
        pares = detectar_columnas(celdas, bloques[0], self.columna_inicio)
        registros = construir_registros(celdas, bloques, pares)
        problemas = revisar_calidad(registros)
        self._informar_problemas(problemas)
        return a_observaciones(registros, problemas, self.anio, self.mes_inicial, ahora_iso())

    def _informar_problemas(self, problemas: dict[tuple[str, str, int], str]) -> None:
        """Registra los periodos omitidos solo cuando cambian (evita repetir el log en cada ciclo)."""
        if problemas == self._ultimos_problemas:
            return
        self._ultimos_problemas = problemas
        for (segmento, tipo, mes), motivo in sorted(problemas.items()):
            log.info("Omitido %s | %s mes %d: %s", segmento, tipo, mes, motivo)


def crear_extractor(config: dict[str, Any]) -> ExtractorExcelCumplimiento:
    """Punto de entrada que usa main.py: construye el extractor desde config.json."""
    return ExtractorExcelCumplimiento(
        ruta=resolver_ruta(config["ruta_archivo"]),
        hoja=config.get("hoja"),
        anio=int(config["anio"]),
        mes_inicial=int(config.get("mes_inicial", 1)),
        columna_inicio=int(config.get("columna_inicio", 3)),
        etiquetas=config.get("etiquetas_filas"),
    )
