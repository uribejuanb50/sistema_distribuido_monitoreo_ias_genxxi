"""Nodo hijo: lee una fuente de datos y publica al nodo padre (ZeroMQ PUB).

Orquestación: config.json -> extractor (extraccion.py) -> socket PUB -> ciclo de lectura.

Qué envía al padre (todos con el mismo header del contrato, comun/contrato.py):
- "observaciones": los KPI interpretados. Solo cuando cambian, o cada
  'reenvio_forzado_segundos' por si el padre estaba caído (PUB/SUB no guarda mensajes).
- "captura": los números crudos de la fuente (si el extractor los ofrece, como el de
  Power BI). Se envían aunque la interpretación falle.
- "estado": cómo está el servicio del nodo: activo, degradado (hubo un problema con los
  datos, el nodo sigue), caido (error del servicio: el nodo se cierra) o detenido (Ctrl+C).

Si config.json tiene "cerrar_ante_error": true, ante un error del servicio (ErrorServicio)
o un error inesperado el nodo avisa al padre que se cayó y se CIERRA con código 1.

Uso (desde esta carpeta):
    python main.py                 # ciclo continuo
    python main.py --una-vez       # lee y publica una sola vez
    python main.py --solo-mostrar  # solo lee e imprime (para depurar la extracción)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DIR_NODO = Path(__file__).resolve().parent
RAIZ_PROYECTO = DIR_NODO.parents[1]
if str(RAIZ_PROYECTO) not in sys.path:
    sys.path.insert(0, str(RAIZ_PROYECTO))

import zmq  # noqa: E402

from comun.contrato import Encabezado, Mensaje, Observacion, huella_lote  # noqa: E402
from comun.extractor_base import ErrorExtraccion, ErrorServicio, ExtractorBase  # noqa: E402
from comun.red import cargar_config_red, crear_publicador, publicar  # noqa: E402
from comun.utilidades import cargar_json, configurar_logging  # noqa: E402
from extraccion import crear_extractor  # noqa: E402

log = logging.getLogger("nodo_hijo")


def parsear_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Nodo hijo: extrae datos y los publica al nodo padre.")
    parser.add_argument("--config", default=str(DIR_NODO / "config.json"), help="Ruta del config.json")
    parser.add_argument("--una-vez", action="store_true", help="Lee y publica una sola vez y termina")
    parser.add_argument("--solo-mostrar", action="store_true", help="Solo extrae e imprime, sin publicar")
    return parser.parse_args()


@dataclass(frozen=True)
class Lectura:
    observaciones: list[Observacion] | None
    error: str | None = None
    tipo_error: str | None = None
    fatal: bool = False  # True = el servicio se cayó y el nodo debe cerrarse


def extraer_seguro(extractor: ExtractorBase, cerrar_ante_error: bool = False) -> Lectura:
    """Extrae sin que una excepción tumbe el nodo; dice si el error es fatal."""
    try:
        return Lectura(extractor.extraer())
    except ErrorServicio as error:
        log.error("Falló el servicio de la fuente: %s", error)
        return Lectura(None, str(error), type(error).__name__, fatal=cerrar_ante_error)
    except ErrorExtraccion as error:
        log.warning("No se pudo leer la fuente: %s", error)
        return Lectura(None, str(error), type(error).__name__)
    except Exception as error:  # error inesperado: se registra completo
        log.exception("Error inesperado al extraer de %s", extractor.fuente)
        return Lectura(None, f"Error inesperado: {error!r}", type(error).__name__, fatal=cerrar_ante_error)


def mostrar(observaciones: list[Observacion]) -> None:
    ancho = max([len("kpi_id"), *(len(obs.kpi_id) for obs in observaciones)])
    print(f"{'kpi_id':{ancho}} {'fecha':10} {'valor':>14} {'meta':>12}")
    for obs in observaciones:
        meta = "" if obs.meta is None else f"{obs.meta:12.2f}"
        print(f"{obs.kpi_id:{ancho}} {obs.fecha_referencia:10} {obs.valor:14.2f} {meta:>12}")
    print(f"\nTotal: {len(observaciones)} observaciones")


def huella_captura(captura: dict[str, Any]) -> str:
    """Cambia solo si cambian los valores (no la hora de captura)."""
    return hashlib.sha1(json.dumps(captura["visuales"], sort_keys=True).encode("utf-8")).hexdigest()


class Emisor:
    """Arma los mensajes del contrato y los publica al padre con un número de secuencia."""

    def __init__(self, publicador: Any, topico: str, nodo_id: str, extractor: ExtractorBase) -> None:
        self.publicador = publicador
        self.topico = topico
        self.nodo_id = nodo_id
        self.extractor = extractor
        self.secuencia = 0
        self.estado: str | None = None
        self.motivo_estado: str | None = None

    def enviar(self, tipo: str, observaciones: tuple[Observacion, ...] = (),
               contenido: dict[str, Any] | None = None) -> Mensaje:
        self.secuencia += 1
        encabezado = Encabezado(nodo_id=self.nodo_id, tipo_fuente=self.extractor.tipo_fuente,
                                fuente=self.extractor.fuente, tipo_mensaje=tipo, secuencia=self.secuencia)
        mensaje = Mensaje(encabezado, observaciones, contenido or {})
        publicar(self.publicador, self.topico, mensaje.a_bytes())
        return mensaje

    def avisar_estado(self, estado: str, motivo: str, **detalle: Any) -> bool:
        """Envía el estado solo si cambió (para no llenar al padre de avisos repetidos)."""
        if estado == self.estado and motivo == self.motivo_estado:
            return False
        self.enviar("estado", contenido={"estado": estado, "motivo": motivo, "detalle": detalle})
        self.estado, self.motivo_estado = estado, motivo
        nivel = logging.ERROR if estado == "caido" else logging.WARNING if estado == "degradado" else logging.INFO
        log.log(nivel, "Estado enviado al padre: %s (%s)", estado.upper(), motivo)
        return True


def main() -> int:
    args = parsear_argumentos()
    config = cargar_json(args.config)
    nodo_id = config["nodo_id"]
    configurar_logging(nodo_id)
    cerrar_ante_error = bool(config.get("cerrar_ante_error", False))
    extractor = crear_extractor(config)

    if args.solo_mostrar:
        try:
            lectura = extraer_seguro(extractor, cerrar_ante_error)
        finally:
            extractor.cerrar()
        if lectura.observaciones is None:
            return 1
        mostrar(lectura.observaciones)
        return 0

    red = cargar_config_red()
    contexto = zmq.Context()
    publicador = crear_publicador(contexto, red.conexion_datos, enlazar=False)
    # ZeroMQ tarda un instante en conectar; sin esta espera se pierde el primer envío.
    time.sleep(config.get("espera_conexion_segundos", 1.0))
    log.info("Nodo %s publicando en %s (fuente: %s)", nodo_id, red.conexion_datos, extractor.fuente)
    emisor = Emisor(publicador, red.topico_datos, nodo_id, extractor)

    ultima_huella: str | None = None
    ultima_huella_captura: str | None = None
    ultimo_envio = ultimo_envio_captura = 0.0
    lecturas_ok = 0
    codigo_salida = 0
    numero_lectura = 0
    try:
        while True:
            inicio_ciclo = time.monotonic()
            numero_lectura += 1
            lectura = extraer_seguro(extractor, cerrar_ante_error)
            reenvio = config["reenvio_forzado_segundos"]

            captura = extractor.captura_cruda()  # números crudos: van aunque el mapa falle
            if captura is not None:
                huella = huella_captura(captura)
                if huella != ultima_huella_captura or time.monotonic() - ultimo_envio_captura >= reenvio:
                    emisor.enviar("captura", contenido=captura)
                    log.info("Enviada captura cruda #%d (%d visuales con datos)", emisor.secuencia,
                             len(captura["visuales"]))
                    ultima_huella_captura, ultimo_envio_captura = huella, time.monotonic()

            if lectura.fatal:
                emisor.avisar_estado("caido", lectura.error, tipo_error=lectura.tipo_error, fuente=extractor.fuente,
                                     lecturas_ok=lecturas_ok)
                log.error("SERVICIO CAÍDO: el nodo %s se cierra. Revisa el error de arriba y vuelve a "
                          "iniciarlo (python main.py).", nodo_id)
                codigo_salida = 1
                break

            if lectura.error is not None:
                emisor.avisar_estado("degradado", lectura.error, tipo_error=lectura.tipo_error,
                                     fuente=extractor.fuente)
            else:
                lecturas_ok += 1
                observaciones = lectura.observaciones
                huella = huella_lote(observaciones)
                forzar = time.monotonic() - ultimo_envio >= reenvio
                if huella != ultima_huella or forzar:
                    emisor.enviar("observaciones", tuple(observaciones))
                    motivo = "datos nuevos" if huella != ultima_huella else "reenvío periódico"
                    log.info("Enviado mensaje #%d (%d observaciones, %s)", emisor.secuencia, len(observaciones),
                             motivo)
                    ultima_huella, ultimo_envio = huella, time.monotonic()
                else:
                    log.info("Lectura #%d: sin cambios en la fuente (no se reenvía)", numero_lectura)
                emisor.avisar_estado("activo", "lectura correcta", fuente=extractor.fuente)

            if args.una_vez:
                codigo_salida = 0 if lectura.error is None else 1
                break
            # El intervalo se cuenta desde el INICIO de la lectura (cargar el reporte también cuenta).
            espera = max(0.0, config["intervalo_segundos"] - (time.monotonic() - inicio_ciclo))
            log.info("Próxima lectura en %.0f s (Ctrl+C para detener)", espera)
            time.sleep(espera)
    except KeyboardInterrupt:
        log.info("Detenido por el usuario (Ctrl+C)")
        emisor.avisar_estado("detenido", "detenido por el usuario", fuente=extractor.fuente)
    finally:
        extractor.cerrar()   # cierra el navegador, si lo hay
        publicador.close()   # LINGER: espera hasta 1 s a que salga el último aviso
        contexto.term()
    return codigo_salida


if __name__ == "__main__":
    sys.exit(main())
