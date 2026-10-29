"""Healthchecker del nodo padre.

- Escucha (SUB) los latidos y eventos que publica el nodo padre y los imprime.
- Si no llegan latidos durante 'timeout_latido_segundos', considera que el padre
  está caído: si el proceso sigue vivo pero colgado lo termina, y lo vuelve a lanzar
  con el comando de consola `python main.py` dentro de nodo_padre/nodo_padre/.
- Entre relanzamientos espera cada vez más (backoff) para no entrar en un bucle si
  el padre falla al arrancar.

Uso (desde la carpeta nodo_padre/):
    python healthchecker.py                    # el padre se abre en una consola nueva
    python healthchecker.py --misma-consola    # el padre comparte esta consola
    python healthchecker.py --solo-monitorear  # imprime estado, nunca relanza
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

DIR_ESTE = Path(__file__).resolve().parent
DIR_PADRE = DIR_ESTE / "nodo_padre"
RAIZ_PROYECTO = DIR_ESTE.parent
if str(RAIZ_PROYECTO) not in sys.path:
    sys.path.insert(0, str(RAIZ_PROYECTO))

import psutil  # noqa: E402
import zmq  # noqa: E402

from comun.red import cargar_config_red, crear_suscriptor, recibir  # noqa: E402
from comun.utilidades import cargar_json, configurar_logging, resolver_ruta  # noqa: E402

log = logging.getLogger("healthchecker")


def parsear_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Vigila el nodo padre y lo relanza si se cae.")
    parser.add_argument("--config", default=str(DIR_ESTE / "config_healthchecker.json"))
    parser.add_argument("--misma-consola", action="store_true", help="Lanza el padre en esta misma consola")
    parser.add_argument("--solo-monitorear", action="store_true", help="Solo imprime el estado, no relanza")
    return parser.parse_args()


def buscar_proceso_padre(ruta_pid: Path) -> psutil.Process | None:
    """Devuelve el proceso del padre según su archivo PID, si sigue vivo."""
    try:
        pid = int(ruta_pid.read_text(encoding="utf-8").strip())
        proceso = psutil.Process(pid)
        if proceso.is_running() and any("main.py" in parte for parte in proceso.cmdline()):
            return proceso
    except (OSError, ValueError, psutil.Error):
        pass
    return None


def terminar_proceso(proceso: psutil.Process, espera_segundos: float = 5) -> None:
    try:
        proceso.terminate()
        proceso.wait(timeout=espera_segundos)
    except psutil.TimeoutExpired:
        proceso.kill()
    except psutil.NoSuchProcess:
        pass


def lanzar_padre(nueva_consola: bool) -> subprocess.Popen:
    """Ejecuta `python main.py` en la carpeta del nodo padre."""
    comando = [sys.executable, "main.py"]
    banderas = subprocess.CREATE_NEW_CONSOLE if (nueva_consola and os.name == "nt") else 0
    log.warning("Lanzando nodo padre: %s (en %s)", " ".join(comando), DIR_PADRE)
    return subprocess.Popen(comando, cwd=DIR_PADRE, creationflags=banderas)


def describir_latido(latido: dict[str, Any]) -> str:
    nodos = ", ".join(latido.get("nodos_vistos", {})) or "ninguno"
    return (f"Padre OK | pid {latido.get('pid')} | activo {latido.get('segundos_activo')} s | "
            f"mensajes {latido.get('mensajes_recibidos')} | obs. nuevas {latido.get('observaciones_nuevas')} | "
            f"alertas {latido.get('alertas_emitidas')} (pendientes {latido.get('alertas_pendientes')}) | "
            f"nodos: {nodos}")


def main() -> int:
    args = parsear_argumentos()
    config = cargar_json(args.config)
    config_padre = cargar_json(DIR_PADRE / "config.json")
    configurar_logging("healthchecker")
    ruta_pid = resolver_ruta(config_padre["ruta_pid"])
    timeout = config["timeout_latido_segundos"]
    gracia = config["gracia_arranque_segundos"]
    espera_maxima = config["espera_maxima_reintento_segundos"]
    nueva_consola = config["abrir_en_nueva_consola"] and not args.misma_consola

    red = cargar_config_red()
    contexto = zmq.Context()
    suscriptor = crear_suscriptor(contexto, red.conexion_estado, [red.topico_latido, red.topico_evento],
                                  enlazar=False)
    log.info("Healthchecker escuchando %s (timeout de latido: %s s)", red.conexion_estado, timeout)

    ultima_senal = time.monotonic()       # al inicio se da un margen de 'gracia'
    proximo_chequeo = ultima_senal + gracia
    espera_reintento = gracia
    proceso_lanzado: subprocess.Popen | None = None
    reinicios = 0
    try:
        while True:
            recibido = recibir(suscriptor, espera_ms=500)
            ahora = time.monotonic()
            if recibido is not None:
                topico, cuerpo = recibido
                try:
                    datos = json.loads(cuerpo)
                except json.JSONDecodeError:
                    log.warning("Mensaje de estado ilegible en el tópico %r", topico)
                    continue
                if topico == red.topico_latido:
                    ultima_senal = ahora
                    espera_reintento = gracia
                    if config["imprimir_latidos"]:
                        log.info(describir_latido(datos))
                else:
                    log.info("EVENTO del padre: %s", json.dumps(datos, ensure_ascii=False))

            if proceso_lanzado is not None and proceso_lanzado.poll() is not None:
                log.warning("El nodo padre lanzado terminó con código %s", proceso_lanzado.returncode)
                proceso_lanzado = None

            silencio = ahora - ultima_senal
            if ahora < proximo_chequeo or silencio <= timeout:
                continue

            log.warning("Sin latidos del nodo padre desde hace %.0f s", silencio)
            if args.solo_monitorear:
                proximo_chequeo = ahora + timeout
                continue
            colgado = buscar_proceso_padre(ruta_pid)
            if colgado is not None:
                log.warning("El proceso %d sigue vivo pero no responde: se termina", colgado.pid)
                terminar_proceso(colgado)
            proceso_lanzado = lanzar_padre(nueva_consola)
            reinicios += 1
            log.warning("Relanzamiento #%d. Próxima verificación en %.0f s", reinicios, espera_reintento)
            ultima_senal = ahora
            proximo_chequeo = ahora + espera_reintento
            espera_reintento = min(espera_reintento * 2, espera_maxima)
    except KeyboardInterrupt:
        log.info("Healthchecker detenido (el nodo padre sigue en ejecución)")
    finally:
        suscriptor.close()
        contexto.term()
    return 0


if __name__ == "__main__":
    sys.exit(main())
