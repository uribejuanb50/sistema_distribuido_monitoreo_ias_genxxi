"""Contrato de mensajes compartido por TODOS los nodos (versión 1.0).

Cualquier nodo hijo, lea la fuente que lea (Excel, CSV, base de datos...), envía al
nodo padre exactamente este formato JSON:

{
  "header": {
    "version_esquema": "1.0",
    "id_mensaje": "5f2c...",            <- único por mensaje (evita procesar duplicados)
    "nodo_id": "nodo_excel_cumplimiento", <- QUIÉN genera la información
    "tipo_fuente": "excel",
    "fuente": "Libro1.xlsx",
    "tipo_mensaje": "observaciones",
    "enviado_en": "2026-10-22T16:00:00-05:00",
    "secuencia": 3                      <- contador de envíos del nodo
  },
  "payload": {
    "observaciones": [ { ...Observacion... }, ... ]
  }
}

Ver comun/plantilla_mensaje.json para un ejemplo completo.

Además de "observaciones" hay dos tipos de mensaje más (mismo header, otro payload):

- "captura": los números CRUDOS que leyó el nodo, bloque por bloque, tal como salen de
  la inyección JS en Power BI. Llegan aunque el mapa no los haya podido interpretar.
    "payload": {"capturado_en": "...", "origen": "<url>",
                "visuales": [{"bloque": 59, "seccion": "Visual / Bloque 59",
                              "valores": ["3.469"], "numeros": [3469.0]}, ...]}

- "estado": cómo está el servicio del nodo. "caido" = el nodo se cerró por un error.
    "payload": {"estado": "caido", "motivo": "El reporte pide iniciar sesión...",
                "detalle": {...}}
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime
from typing import Any

VERSION_ESQUEMA = "1.0"
TIPOS_PERIODO = frozenset({"diario", "semanal", "mensual", "acumulado"})
UNIDADES = frozenset({"porcentaje", "puntos", "unidades", "segundos"})
TIPOS_MENSAJE = frozenset({"observaciones", "captura", "estado"})
# activo: leyendo bien · degradado: lee pero hay un problema con los datos (sigue vivo)
# caido: error del servicio, el nodo se cerró · detenido: lo cerró el usuario (Ctrl+C)
ESTADOS_NODO = frozenset({"activo", "degradado", "caido", "detenido"})


class ErrorContrato(ValueError):
    """El mensaje no cumple el contrato compartido."""


def ahora_iso() -> str:
    """Fecha y hora local con zona horaria, en formato ISO 8601."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _exigir_texto(nombre: str, valor: Any) -> None:
    if not isinstance(valor, str) or not valor.strip():
        raise ErrorContrato(f"'{nombre}' debe ser un texto no vacío (recibido: {valor!r})")


def _exigir_numero(nombre: str, valor: Any, opcional: bool = False) -> None:
    if valor is None and opcional:
        return
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
        raise ErrorContrato(f"'{nombre}' debe ser un número finito (recibido: {valor!r})")


def _construir(clase: type, datos: Any, nombre: str):
    """Crea una dataclass desde un dict, convirtiendo los errores en ErrorContrato."""
    if not isinstance(datos, dict):
        raise ErrorContrato(f"'{nombre}' debe ser un objeto JSON")
    permitidos = {campo.name for campo in fields(clase)}
    try:
        return clase(**{clave: valor for clave, valor in datos.items() if clave in permitidos})
    except TypeError as error:  # faltan campos obligatorios
        raise ErrorContrato(f"'{nombre}' incompleto: {error}") from error


@dataclass(frozen=True)
class Observacion:
    """Un valor de un KPI en un periodo concreto."""

    kpi_id: str               # identificador estable, p. ej. "pospago.cumplimiento_mensual"
    canal: str                # canal / segmento al que pertenece el KPI
    nombre_kpi: str
    fecha_referencia: str     # fecha A LA QUE CORRESPONDE el dato (YYYY-MM-DD)
    tipo_periodo: str         # diario | semanal | mensual | acumulado
    valor: float
    unidad: str               # porcentaje | puntos | unidades | segundos
    meta: float | None = None
    fecha_captura: str = field(default_factory=ahora_iso)  # CUÁNDO lo leyó el nodo hijo
    atributos: dict[str, Any] = field(default_factory=dict)  # datos extra propios de la fuente

    def __post_init__(self) -> None:
        for nombre in ("kpi_id", "canal", "nombre_kpi", "fecha_captura"):
            _exigir_texto(nombre, getattr(self, nombre))
        try:
            date.fromisoformat(self.fecha_referencia)
        except (TypeError, ValueError) as error:
            raise ErrorContrato(
                f"'fecha_referencia' debe ser YYYY-MM-DD (recibido: {self.fecha_referencia!r})"
            ) from error
        if self.tipo_periodo not in TIPOS_PERIODO:
            raise ErrorContrato(f"'tipo_periodo' inválido: {self.tipo_periodo!r}")
        if self.unidad not in UNIDADES:
            raise ErrorContrato(f"'unidad' inválida: {self.unidad!r}")
        _exigir_numero("valor", self.valor)
        _exigir_numero("meta", self.meta, opcional=True)
        if not isinstance(self.atributos, dict):
            raise ErrorContrato("'atributos' debe ser un objeto JSON")

    def huella(self) -> str:
        """Resume el CONTENIDO del dato (sin la fecha de captura) para detectar repetidos."""
        meta = None if self.meta is None else round(self.meta, 6)
        base = json.dumps([self.kpi_id, self.fecha_referencia, round(self.valor, 6), meta])
        return hashlib.sha1(base.encode("utf-8")).hexdigest()

    def a_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def desde_dict(cls, datos: Any) -> Observacion:
        return _construir(cls, datos, "observacion")


@dataclass(frozen=True)
class Encabezado:
    """Header del mensaje: identifica qué nodo generó la información."""

    nodo_id: str
    tipo_fuente: str
    fuente: str
    tipo_mensaje: str = "observaciones"
    version_esquema: str = VERSION_ESQUEMA
    id_mensaje: str = field(default_factory=lambda: uuid.uuid4().hex)
    enviado_en: str = field(default_factory=ahora_iso)
    secuencia: int = 0

    def __post_init__(self) -> None:
        for nombre in ("nodo_id", "tipo_fuente", "fuente", "id_mensaje", "enviado_en"):
            _exigir_texto(nombre, getattr(self, nombre))
        if self.tipo_mensaje not in TIPOS_MENSAJE:
            raise ErrorContrato(f"'tipo_mensaje' inválido: {self.tipo_mensaje!r}")
        if str(self.version_esquema).split(".")[0] != VERSION_ESQUEMA.split(".")[0]:
            raise ErrorContrato(
                f"Versión de esquema incompatible: {self.version_esquema!r} (se espera {VERSION_ESQUEMA})"
            )
        if isinstance(self.secuencia, bool) or not isinstance(self.secuencia, int) or self.secuencia < 0:
            raise ErrorContrato(f"'secuencia' debe ser un entero >= 0 (recibido: {self.secuencia!r})")


def _validar_captura(datos: Any) -> dict[str, Any]:
    if not isinstance(datos, dict) or not isinstance(datos.get("visuales"), list):
        raise ErrorContrato("'payload.visuales' debe ser una lista")
    visuales = []
    for posicion, visual in enumerate(datos["visuales"], start=1):
        nombre = f"visuales[{posicion}]"
        if not isinstance(visual, dict):
            raise ErrorContrato(f"'{nombre}' debe ser un objeto JSON")
        bloque = visual.get("bloque")
        if isinstance(bloque, bool) or not isinstance(bloque, int) or bloque < 1:
            raise ErrorContrato(f"'{nombre}.bloque' debe ser un entero >= 1 (recibido: {bloque!r})")
        _exigir_texto(f"{nombre}.seccion", visual.get("seccion"))
        valores = visual.get("valores")
        if not isinstance(valores, list) or not all(isinstance(v, str) for v in valores):
            raise ErrorContrato(f"'{nombre}.valores' debe ser una lista de textos")
        numeros = visual.get("numeros", [])
        if not isinstance(numeros, list):
            raise ErrorContrato(f"'{nombre}.numeros' debe ser una lista")
        for numero in numeros:
            _exigir_numero(f"{nombre}.numeros", numero, opcional=True)
        visuales.append({"bloque": bloque, "seccion": visual["seccion"], "valores": valores, "numeros": numeros})
    return {"capturado_en": str(datos.get("capturado_en") or ahora_iso()),
            "origen": str(datos.get("origen") or ""), "visuales": visuales}


def _validar_estado(datos: Any) -> dict[str, Any]:
    if not isinstance(datos, dict):
        raise ErrorContrato("'payload' de un estado debe ser un objeto JSON")
    if datos.get("estado") not in ESTADOS_NODO:
        raise ErrorContrato(f"'estado' inválido: {datos.get('estado')!r} (se espera {sorted(ESTADOS_NODO)})")
    _exigir_texto("motivo", datos.get("motivo"))
    detalle = datos.get("detalle", {})
    if not isinstance(detalle, dict):
        raise ErrorContrato("'detalle' debe ser un objeto JSON")
    return {"estado": datos["estado"], "motivo": datos["motivo"], "detalle": detalle}


@dataclass(frozen=True)
class Mensaje:
    """Mensaje completo: header + payload.

    - tipo "observaciones": `observaciones` trae los datos y `contenido` va vacío.
    - tipos "captura" y "estado": `observaciones` va vacío y `contenido` es el payload.
    """

    encabezado: Encabezado
    observaciones: tuple[Observacion, ...] = ()
    contenido: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        tipo = self.encabezado.tipo_mensaje
        if tipo == "captura":
            object.__setattr__(self, "contenido", _validar_captura(self.contenido))
        elif tipo == "estado":
            object.__setattr__(self, "contenido", _validar_estado(self.contenido))

    @property
    def tipo(self) -> str:
        return self.encabezado.tipo_mensaje

    def a_dict(self) -> dict[str, Any]:
        if self.tipo == "observaciones":
            payload = {"observaciones": [obs.a_dict() for obs in self.observaciones]}
        else:
            payload = dict(self.contenido)
        return {"header": asdict(self.encabezado), "payload": payload}

    def a_bytes(self) -> bytes:
        return json.dumps(self.a_dict(), ensure_ascii=False).encode("utf-8")

    @classmethod
    def desde_bytes(cls, crudo: bytes) -> Mensaje:
        """Convierte los bytes recibidos por la red en un Mensaje validado."""
        try:
            datos = json.loads(crudo.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ErrorContrato(f"El mensaje no es JSON válido: {error}") from error
        if not isinstance(datos, dict):
            raise ErrorContrato("El mensaje debe ser un objeto JSON")
        encabezado = _construir(Encabezado, datos.get("header"), "header")
        payload = datos.get("payload")
        if encabezado.tipo_mensaje != "observaciones":
            return cls(encabezado, (), payload if isinstance(payload, dict) else {"_invalido": payload})
        if not isinstance(payload, dict) or not isinstance(payload.get("observaciones"), list):
            raise ErrorContrato("'payload.observaciones' debe ser una lista")
        observaciones = tuple(Observacion.desde_dict(obs) for obs in payload["observaciones"])
        return cls(encabezado, observaciones)


def huella_lote(observaciones: list[Observacion] | tuple[Observacion, ...]) -> str:
    """Huella de un conjunto de observaciones: cambia solo si cambia algún dato."""
    huellas = sorted(obs.huella() for obs in observaciones)
    return hashlib.sha1("|".join(huellas).encode("utf-8")).hexdigest()
