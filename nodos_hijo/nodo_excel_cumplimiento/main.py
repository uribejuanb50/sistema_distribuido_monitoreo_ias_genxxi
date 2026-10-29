"""Nodo hijo: lee una fuente de datos y publica observaciones al nodo padre (ZeroMQ PUB).

Orquestación: config.json -> extractor (extraccion.py) -> socket PUB -> ciclo de lectura.
Solo publica cuando los datos cambian, o cada 'reenvio_forzado_segundos' por si el
padre estaba caído y se perdió algún mensaje (PUB/SUB no guarda mensajes).

Uso (desde esta carpeta):
    python main.py                 # ciclo continuo
    python main.py --una-vez       # lee y publica una sola vez
    python main.py --solo-mostrar  # solo lee e imprime (para depurar la extracción)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

DIR_NODO = Path(__file__).resolve().parent
RAIZ_PROYECTO = DIR_NODO.parents[1]
if str(RAIZ_PROYECTO) not in sys.path:
    sys.path.insert(0, str(RAIZ_PROYECTO))

import zmq  # noqa: E402

from comun.contrato import Encabezado, Mensaje, Observacion, huella_lote  # noqa: E402
from comun.extractor_base import ErrorExtraccion, ExtractorBase  # noqa: E402
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


def extraer_seguro(extractor: ExtractorBase) -> list[Observacion] | None:
    """Extrae sin detener el nodo si la fuente falla; se reintenta en el siguiente ciclo."""
    try:
        return extractor.extraer()
    except ErrorExtraccion as error:
        log.warning("No se pudo leer la fuente: %s", error)
    except Exception:  # error inesperado: se registra completo pero el nodo sigue vivo
        log.exception("Error inesperado al extraer de %s", extractor.fuente)
    return None


def mostrar(observaciones: list[Observacion]) -> None:
    print(f"{'kpi_id':40} {'fecha':10} {'valor':>12} {'meta':>12}")
    for obs in observaciones:
        meta = "" if obs.meta is None else f"{obs.meta:12.2f}"
        print(f"{obs.kpi_id:40} {obs.fecha_referencia:10} {obs.valor:12.2f} {meta:>12}")
    print(f"\nTotal: {len(observaciones)} observaciones")


def main() -> int:
    args = parsear_argumentos()
    config = cargar_json(args.config)
    nodo_id = config["nodo_id"]
    configurar_logging(nodo_id)
    extractor = crear_extractor(config)

    if args.solo_mostrar:
        observaciones = extraer_seguro(extractor)
        if observaciones is None:
            return 1
        mostrar(observaciones)
        return 0

    red = cargar_config_red()
    contexto = zmq.Context()
    publicador = crear_publicador(contexto, red.conexion_datos, enlazar=False)
    # ZeroMQ tarda un instante en conectar; sin esta espera se pierde el primer envío.
    time.sleep(config.get("espera_conexion_segundos", 1.0))
    log.info("Nodo %s publicando en %s (fuente: %s)", nodo_id, red.conexion_datos, extractor.fuente)

    ultima_huella: str | None = None
    ultimo_envio = 0.0
    secuencia = 0
    try:
        while True:
            observaciones = extraer_seguro(extractor)
            if observaciones is not None:
                huella = huella_lote(observaciones)
                forzar = time.monotonic() - ultimo_envio >= config["reenvio_forzado_segundos"]
                if huella != ultima_huella or forzar:
                    secuencia += 1
                    encabezado = Encabezado(nodo_id=nodo_id, tipo_fuente=extractor.tipo_fuente,
                                            fuente=extractor.fuente, secuencia=secuencia)
                    publicar(publicador, red.topico_datos, Mensaje(encabezado, tuple(observaciones)).a_bytes())
                    motivo = "datos nuevos" if huella != ultima_huella else "reenvío periódico"
                    log.info("Enviado mensaje #%d (%d observaciones, %s)", secuencia, len(observaciones), motivo)
                    ultima_huella, ultimo_envio = huella, time.monotonic()
                else:
                    log.debug("Sin cambios en la fuente")
            if args.una_vez:
                break
            time.sleep(config["intervalo_segundos"])
    except KeyboardInterrupt:
        log.info("Detenido por el usuario (Ctrl+C)")
    finally:
        publicador.close()
        contexto.term()
    return 0


if __name__ == "__main__":
    sys.exit(main())
