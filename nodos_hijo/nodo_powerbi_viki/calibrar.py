"""Calibración del nodo Power BI: muestra QUÉ se capturó y CÓMO se interpretó cada valor.

Úsalo la primera vez y cada vez que cambie el diseño del reporte. No envía nada al nodo padre.

Uso (desde esta carpeta):
    python calibrar.py                    # abre el reporte con Selenium (según config.json)
    python calibrar.py captura.json       # usa una captura guardada o el .txt de la consola
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

DIR_NODO = Path(__file__).resolve().parent
RAIZ_PROYECTO = DIR_NODO.parents[1]
if str(RAIZ_PROYECTO) not in sys.path:
    sys.path.insert(0, str(RAIZ_PROYECTO))

from comun.extractor_base import ErrorExtraccion  # noqa: E402
from comun.utilidades import cargar_json, configurar_logging, resolver_ruta  # noqa: E402
from extraccion import Captura, Resultado, cargar_captura, crear_extractor, interpretar  # noqa: E402


def resumir(textos: tuple[str, ...], filas: tuple[tuple[str, ...], ...], maximo: int = 60) -> str:
    contenido = " | ".join(textos) if textos else f"{len(filas)} filas: " + " | ".join(filas[0]) if filas else ""
    return contenido if len(contenido) <= maximo else contenido[:maximo - 3] + "..."


def imprimir(captura: Captura, resultado: Resultado) -> None:
    print(f"\nFuente: {captura.origen} · capturado {captura.capturado_en}")
    print(f"Fecha de actualización del reporte: {resultado.fecha_actualizacion}")

    print("\nVISUALES INTERPRETADOS")
    for visual in captura.visuales:
        if visual.clave in resultado.asignaciones:
            print(f"  {visual.clave:12} {resumir(visual.textos, visual.filas, 40):40} -> "
                  f"{resultado.asignaciones[visual.clave]}")

    ok = sum(1 for _, correcta, _ in resultado.validaciones if correcta)
    print(f"\nVALIDACIONES ({ok} correctas, {len(resultado.validaciones) - ok} con error)")
    for regla, correcta, detalle in resultado.validaciones:
        print(f"  {'OK   ' if correcta else 'ERROR'} {regla}  [{detalle}]")

    if resultado.avisos:
        print("\nAVISOS")
        for aviso in resultado.avisos:
            print(f"  - {aviso}")

    sin_interpretar = [v for v in captura.visuales
                       if (v.textos or v.filas) and v.clave not in resultado.asignaciones]
    if sin_interpretar:
        print("\nVISUALES SIN INTERPRETAR (no se envían)")
        for visual in sin_interpretar:
            print(f"  {visual.clave:12} {resumir(visual.textos, visual.filas)}")

    por_tipo = Counter(obs.tipo_periodo for obs in resultado.observaciones)
    detalle = ", ".join(f"{tipo}: {n}" for tipo, n in sorted(por_tipo.items()))
    estado = "LISTAS para enviar" if not resultado.errores else "NO se enviarían (hay validaciones con error)"
    print(f"\nRESUMEN: {len(resultado.observaciones)} observaciones ({detalle}) -> {estado}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Muestra cómo se interpreta el reporte Power BI.")
    parser.add_argument("captura", nargs="?", help="Captura guardada (JSON del nodo o .txt de la consola)")
    parser.add_argument("--config", default=str(DIR_NODO / "config.json"))
    args = parser.parse_args()
    config = cargar_json(args.config)
    configurar_logging("calibracion_powerbi")
    if args.captura:
        config["captura_sin_conexion"] = str(resolver_ruta(Path(args.captura).resolve()))
    extractor = crear_extractor(config)
    try:
        captura = extractor.obtener_captura()
        resultado = interpretar(captura, extractor.mapa)
    except ErrorExtraccion as error:
        print(f"\nERROR: {error}")
        return 1
    finally:
        extractor.cerrar()
    imprimir(captura, resultado)
    return 0 if not resultado.errores else 2


if __name__ == "__main__":
    sys.exit(main())
