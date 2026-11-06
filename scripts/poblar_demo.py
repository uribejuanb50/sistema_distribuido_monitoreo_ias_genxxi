"""Crea una base SQLite de DEMOSTRACIÓN para el dashboard, solo con datos ficticios.

Usa el mismo procesamiento del nodo padre (guardado + pensamiento), pero sin red:
1. Carga inicial: Excel ficticio (2 segmentos, 10 meses) + NPS VIKI ficticio (12 meses).
2. Llega noviembre: el Segmento Norte cae fuerte y el NPS sigue bajando.

No toca la base real ni salida/ (el JSON de la demo queda en datos/registro/).

Uso (desde la raíz del proyecto):
    python scripts/poblar_demo.py
    streamlit run dashboard/app.py -- --bd datos/registro/demo.sqlite3
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

RAIZ_PROYECTO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(RAIZ_PROYECTO / "scripts"), str(RAIZ_PROYECTO / "nodo_padre" / "nodo_padre"),
                str(RAIZ_PROYECTO)]

from comun.contrato import Encabezado, Mensaje, Observacion  # noqa: E402
from comun.utilidades import cargar_json, resolver_ruta  # noqa: E402
from generar_excel_ficticio import escribir_libro, generar_datos  # noqa: E402
from guardado import Almacen  # noqa: E402
from pensamiento import ConfiguracionAnalisis  # noqa: E402

RUTA_BD = resolver_ruta("datos/registro/demo.sqlite3")
RUTA_JSON = resolver_ruta("datos/registro/demo_alertas.json")


def cargar_modulo(nombre: str, ruta: Path):
    especificacion = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    sys.modules[nombre] = modulo
    especificacion.loader.exec_module(modulo)
    return modulo


padre = cargar_modulo("main_nodo_padre", RAIZ_PROYECTO / "nodo_padre" / "nodo_padre" / "main.py")
excel = cargar_modulo("extraccion_excel", RAIZ_PROYECTO / "nodos_hijo" / "nodo_excel_cumplimiento" / "extraccion.py")
csv = cargar_modulo("extraccion_csv", RAIZ_PROYECTO / "nodos_hijo" / "_plantilla_nodo" / "extraccion.py")


def mensaje(nodo_id: str, tipo_fuente: str, fuente: str, observaciones: list[Observacion], secuencia: int) -> bytes:
    encabezado = Encabezado(nodo_id=nodo_id, tipo_fuente=tipo_fuente, fuente=fuente, secuencia=secuencia)
    return Mensaje(encabezado, tuple(observaciones)).a_bytes()


def main() -> None:
    for ruta in (RUTA_BD, RUTA_JSON):
        ruta.unlink(missing_ok=True)
    configuracion = ConfiguracionAnalisis.desde_dict(
        cargar_json(RAIZ_PROYECTO / "nodo_padre" / "nodo_padre" / "config.json")["analisis"])
    almacen = Almacen(RUTA_BD)
    ctx = padre.ContextoPadre(almacen=almacen, configuracion=configuracion, ruta_json=RUTA_JSON)

    with tempfile.TemporaryDirectory() as temporal:
        ruta_excel = Path(temporal) / "cumplimiento_ficticio.xlsx"
        extractor_excel = excel.ExtractorExcelCumplimiento(ruta_excel, hoja=None, anio=2026)
        extractor_csv = csv.ExtractorCSV(RAIZ_PROYECTO / "datos" / "ejemplo" / "nps_viki_ficticio.csv")

        # 1) Carga inicial
        datos = generar_datos()
        escribir_libro(ruta_excel, datos)
        padre.procesar_mensaje(mensaje("nodo_excel_demo", "excel", ruta_excel.name, extractor_excel.extraer(), 1), ctx)
        nps = extractor_csv.extraer()
        padre.procesar_mensaje(mensaje("nodo_csv_demo", "csv", "nps_viki_ficticio.csv", nps, 1), ctx)

        # 2) Llega noviembre
        norte, sur = datos["Segmento Norte"][10], datos["Segmento Sur"][10]
        norte["real"] = int(norte["meta_jugada"] * 0.62)  # caída fuerte, bajo la meta
        sur["real"] = int(sur["meta_jugada"] * 1.05)      # cumple
        escribir_libro(ruta_excel, datos)
        padre.procesar_mensaje(mensaje("nodo_excel_demo", "excel", ruta_excel.name, extractor_excel.extraer(), 2), ctx)
        ultimo = nps[-1]
        noviembre = Observacion(**{**ultimo.a_dict(), "fecha_referencia": "2026-11-01", "valor": 39.5})
        padre.procesar_mensaje(mensaje("nodo_csv_demo", "csv", "nps_viki_ficticio.csv", nps + [noviembre], 2), ctx)

    almacen.cerrar()
    print(f"Base de demostración creada: {RUTA_BD}")
    print(f"Observaciones nuevas: {ctx.estadisticas.observaciones_nuevas} · "
          f"alertas notificadas: {ctx.estadisticas.alertas_emitidas}")
    print("Abre el dashboard con:  streamlit run dashboard/app.py -- --bd datos/registro/demo.sqlite3")


if __name__ == "__main__":
    main()
