"""Comunicación PUB/SUB con ZeroMQ, compartida por todos los nodos.

Topología:
    nodos hijos (PUB, connect) ── topico "datos" ──▶ nodo padre (SUB, bind :5555)
    nodo padre  (PUB, bind :5556) ── "latido"/"evento" ──▶ healthchecker (SUB, connect)

El padre hace bind (dirección fija) y los demás se conectan: así un hijo puede
arrancar antes que el padre y ZeroMQ reconecta solo si el padre se reinicia.
PUB/SUB no garantiza entrega: si el padre está caído, los mensajes se pierden. Es
aceptable porque los hijos reenvían su información periódicamente.
"""
from __future__ import annotations

from dataclasses import dataclass

import zmq

from comun.utilidades import cargar_json, sin_comentarios

RUTA_CONFIG_RED = "comun/config_red.json"
ESPERA_CIERRE_MS = 1000  # tiempo para vaciar mensajes pendientes al cerrar un PUB


@dataclass(frozen=True)
class ConfigRed:
    host_enlace: str   # interfaz donde escucha el padre ("127.0.0.1" local, "*" en red)
    host_padre: str    # dirección a la que se conectan hijos y healthchecker
    puerto_datos: int
    puerto_estado: int
    topico_datos: str
    topico_latido: str
    topico_evento: str

    @property
    def enlace_datos(self) -> str:
        return f"tcp://{self.host_enlace}:{self.puerto_datos}"

    @property
    def conexion_datos(self) -> str:
        return f"tcp://{self.host_padre}:{self.puerto_datos}"

    @property
    def enlace_estado(self) -> str:
        return f"tcp://{self.host_enlace}:{self.puerto_estado}"

    @property
    def conexion_estado(self) -> str:
        return f"tcp://{self.host_padre}:{self.puerto_estado}"


def cargar_config_red(ruta: str = RUTA_CONFIG_RED) -> ConfigRed:
    return ConfigRed(**sin_comentarios(cargar_json(ruta)))


def crear_publicador(contexto: zmq.Context, endpoint: str, enlazar: bool) -> zmq.Socket:
    socket = contexto.socket(zmq.PUB)
    socket.setsockopt(zmq.LINGER, ESPERA_CIERRE_MS)
    if enlazar:
        socket.bind(endpoint)
    else:
        socket.connect(endpoint)
    return socket


def crear_suscriptor(
    contexto: zmq.Context, endpoint: str, topicos: list[str], enlazar: bool
) -> zmq.Socket:
    socket = contexto.socket(zmq.SUB)
    socket.setsockopt(zmq.LINGER, 0)
    for topico in topicos:
        socket.setsockopt_string(zmq.SUBSCRIBE, topico)
    if enlazar:
        socket.bind(endpoint)
    else:
        socket.connect(endpoint)
    return socket


def publicar(socket: zmq.Socket, topico: str, cuerpo: bytes) -> None:
    """Envía [tópico, cuerpo]. Si nadie escucha, ZeroMQ descarta el mensaje."""
    socket.send_multipart([topico.encode("utf-8"), cuerpo])


def recibir(socket: zmq.Socket, espera_ms: int) -> tuple[str, bytes] | None:
    """Espera hasta `espera_ms` un mensaje. Devuelve (tópico, cuerpo) o None."""
    if socket.poll(espera_ms, zmq.POLLIN) == 0:
        return None
    partes = socket.recv_multipart()
    if len(partes) < 2:
        return None
    return partes[0].decode("utf-8", errors="replace"), partes[-1]
