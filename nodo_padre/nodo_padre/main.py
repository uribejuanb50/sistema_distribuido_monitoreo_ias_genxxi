"""Nodo padre: recibe observaciones de los nodos hijos, las guarda, las analiza y escribe
en un JSON las alertas significativas (ese archivo dispara el flujo de Power Automate).

Orquestación (este archivo): red ⇄ guardado ⇄ pensamiento.
- guardado.py    -> historial en SQLite y archivo JSON de salida.
- pensamiento.py -> reglas y estadística para decidir si un cambio es significativo.

Además publica un "latido" cada pocos segundos para que el healthchecker sepa que
está vivo.

Uso (desde esta carpeta):
    python main.py
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

DIR_NODO = Path(__file__).resolve().parent
RAIZ_PROYECTO = DIR_NODO.parents[1]
if str(RAIZ_PROYECTO) not in sys.path:
    sys.path.insert(0, str(RAIZ_PROYECTO))

import zmq  # noqa: E402

import guardado  # noqa: E402
import pensamiento  # noqa: E402
from comun.contrato import ErrorContrato, Mensaje, ahora_iso  # noqa: E402
from comun.red import cargar_config_red, crear_publicador, crear_suscriptor, publicar, recibir  # noqa: E402
from comun.utilidades import cargar_json, configurar_logging, resolver_ruta  # noqa: E402

log = logging.getLogger("nodo_padre")


@dataclass
class Estadisticas:
    inicio: float = field(default_factory=time.monotonic)
    mensajes_recibidos: int = 0
    mensajes_invalidos: int = 0
    observaciones_nuevas: int = 0
    alertas_emitidas: int = 0
    nodos_vistos: dict[str, str] = field(default_factory=dict)  # nodo_id -> último envío


@dataclass
class ContextoPadre:
    """Todo lo que necesita el procesamiento de un mensaje (facilita las pruebas)."""

    almacen: guardado.Almacen
    configuracion: pensamiento.ConfiguracionAnalisis
    ruta_json: Path
    estadisticas: Estadisticas = field(default_factory=Estadisticas)
    notificar_evento: Callable[[dict[str, Any]], None] = lambda evento: None
    pendientes: list[dict[str, Any]] = field(default_factory=list)  # alertas aún no escritas


def procesar_mensaje(cuerpo: bytes, ctx: ContextoPadre) -> None:
    """Valida, guarda y analiza un mensaje de un nodo hijo."""
    try:
        mensaje = Mensaje.desde_bytes(cuerpo)
    except ErrorContrato as error:
        ctx.estadisticas.mensajes_invalidos += 1
        log.warning("Mensaje descartado por no cumplir el contrato: %s", error)
        return
    enc = mensaje.encabezado
    ctx.estadisticas.mensajes_recibidos += 1
    ctx.estadisticas.nodos_vistos[enc.nodo_id] = enc.enviado_en
    if not ctx.almacen.registrar_mensaje(mensaje):
        log.info("Mensaje %s de %s ya procesado: se ignora", enc.id_mensaje, enc.nodo_id)
        return

    resultados = ctx.almacen.guardar_observaciones(mensaje)
    cambios = [r for r in resultados if r.estado != "repetida"]
    ctx.estadisticas.observaciones_nuevas += len(cambios)
    log.info("Mensaje #%d de %s: %d observaciones (%d nuevas o revisadas)",
             enc.secuencia, enc.nodo_id, len(resultados), len(cambios))

    for resultado in sorted(cambios, key=lambda r: (r.observacion.kpi_id, r.observacion.fecha_referencia)):
        alerta = analizar_observacion(resultado, enc.nodo_id, ctx)
        if alerta is not None:
            ctx.pendientes.append(alerta)
    if ctx.pendientes:
        vaciar_pendientes(ctx)


def analizar_observacion(resultado: guardado.ResultadoGuardado, nodo_id: str,
                         ctx: ContextoPadre) -> dict[str, Any] | None:
    """Evalúa una observación. Devuelve la alerta a notificar, o None."""
    obs = resultado.observacion
    serie = ctx.almacen.obtener_serie(obs.kpi_id, hasta=obs.fecha_referencia)
    perfil = ctx.configuracion.perfil_para(obs.kpi_id)
    evaluacion = pensamiento.evaluar(serie, obs.meta, obs.unidad, perfil, ctx.configuracion.reglas)
    id_evaluacion = ctx.almacen.guardar_evaluacion(obs, evaluacion, resultado.id_observacion)

    if not evaluacion.es_significativo:
        return None
    if obs.fecha_referencia != ctx.almacen.ultima_fecha(obs.kpi_id):
        # En la carga inicial llega todo el historial: solo se notifica el periodo más reciente.
        log.info("%s %s: nivel %s en un periodo histórico (registrado, no se notifica)",
                 obs.kpi_id, obs.fecha_referencia, evaluacion.nivel_alerta)
        return None

    alerta = guardado.construir_alerta(obs, evaluacion, nodo_id, id_evaluacion)
    clave = alerta["clave_notificacion"]
    if ctx.almacen.notificacion_existe(clave) or any(p["clave_notificacion"] == clave for p in ctx.pendientes):
        log.info("%s %s: alerta ya notificada, no se duplica", obs.kpi_id, obs.fecha_referencia)
        return None
    log.warning("ALERTA %s | %s | %s | %s", evaluacion.nivel_alerta.upper(), obs.kpi_id,
                obs.fecha_referencia, " ".join(evaluacion.motivos))
    return alerta


def vaciar_pendientes(ctx: ContextoPadre) -> bool:
    """Escribe las alertas pendientes en el JSON. Si falla, se reintenta en el próximo latido."""
    try:
        id_lote = guardado.escribir_json_salida(ctx.ruta_json, ctx.pendientes)
    except OSError as error:
        log.error("No se pudo escribir %s (%s). %d alertas quedan pendientes.",
                  ctx.ruta_json, error, len(ctx.pendientes))
        return False
    ctx.almacen.registrar_notificaciones(ctx.pendientes, id_lote, str(ctx.ruta_json))
    total = len(ctx.pendientes)
    ctx.estadisticas.alertas_emitidas += total
    log.info("JSON de salida actualizado: %s (%d alertas, lote %s)", ctx.ruta_json, total, id_lote)
    ctx.notificar_evento({"tipo": "json_actualizado", "id_lote": id_lote, "total_alertas": total,
                          "kpis": sorted({a["kpi_id"] for a in ctx.pendientes}), "enviado_en": ahora_iso()})
    ctx.pendientes.clear()
    return True


def crear_latido(ctx: ContextoPadre) -> dict[str, Any]:
    est = ctx.estadisticas
    return {
        "tipo": "latido",
        "pid": os.getpid(),
        "segundos_activo": round(time.monotonic() - est.inicio),
        "mensajes_recibidos": est.mensajes_recibidos,
        "mensajes_invalidos": est.mensajes_invalidos,
        "observaciones_nuevas": est.observaciones_nuevas,
        "alertas_emitidas": est.alertas_emitidas,
        "alertas_pendientes": len(ctx.pendientes),
        "nodos_vistos": est.nodos_vistos,
        "enviado_en": ahora_iso(),
    }


def a_bytes(datos: dict[str, Any]) -> bytes:
    return json.dumps(datos, ensure_ascii=False).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Nodo padre: recibe, guarda y analiza observaciones.")
    parser.add_argument("--config", default=str(DIR_NODO / "config.json"), help="Ruta del config.json")
    args = parser.parse_args()

    config = cargar_json(args.config)
    configurar_logging(config["nodo_id"])
    red = cargar_config_red()
    configuracion = pensamiento.ConfiguracionAnalisis.desde_dict(config["analisis"])
    almacen = guardado.Almacen(resolver_ruta(config["ruta_base_datos"]))
    ruta_pid = resolver_ruta(config["ruta_pid"])

    contexto = zmq.Context()
    try:
        suscriptor = crear_suscriptor(contexto, red.enlace_datos, [red.topico_datos], enlazar=True)
        publicador_estado = crear_publicador(contexto, red.enlace_estado, enlazar=True)
    except zmq.ZMQError as error:
        log.error("No se pudieron abrir los puertos (%s). ¿Ya hay otro nodo padre en ejecución?", error)
        almacen.cerrar()
        contexto.destroy(linger=0)
        return 2

    ruta_pid.parent.mkdir(parents=True, exist_ok=True)
    ruta_pid.write_text(str(os.getpid()), encoding="utf-8")

    ctx = ContextoPadre(
        almacen=almacen,
        configuracion=configuracion,
        ruta_json=resolver_ruta(config["ruta_json_salida"]),
        notificar_evento=lambda evento: publicar(publicador_estado, red.topico_evento, a_bytes(evento)),
    )
    intervalo_latido = config["intervalo_latido_segundos"]
    proximo_latido = 0.0
    log.info("Nodo padre (pid %d) escuchando datos en %s y publicando estado en %s",
             os.getpid(), red.enlace_datos, red.enlace_estado)
    try:
        while True:
            recibido = recibir(suscriptor, espera_ms=500)
            if recibido is not None:
                try:
                    procesar_mensaje(recibido[1], ctx)
                except Exception:  # un mensaje problemático no debe tumbar al padre
                    log.exception("Error inesperado procesando un mensaje")
            ahora = time.monotonic()
            if ahora >= proximo_latido:
                if ctx.pendientes:
                    vaciar_pendientes(ctx)
                publicar(publicador_estado, red.topico_latido, a_bytes(crear_latido(ctx)))
                proximo_latido = ahora + intervalo_latido
    except KeyboardInterrupt:
        log.info("Detenido por el usuario (Ctrl+C)")
    finally:
        suscriptor.close()
        publicador_estado.close()
        contexto.term()
        almacen.cerrar()
        if ruta_pid.exists() and ruta_pid.read_text(encoding="utf-8").strip() == str(os.getpid()):
            ruta_pid.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
