"""Capturas FICTICIAS con la misma estructura del reporte Power BI de VIKI (solo para pruebas).

Los números son inventados pero cumplen las mismas identidades del reporte real
(total = presencial + telefónico, acciones por sesión = acciones / sesiones, etc.).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MAPA = json.loads((Path(__file__).resolve().parents[1] / "nodos_hijo" / "nodo_powerbi_viki"
                   / "mapa_reporte.json").read_text(encoding="utf-8"))

CONTEOS = ("usuarios", "sesiones", "acciones", "usuarios_llamada", "llamadas_turnos", "sesiones_llamada",
           "acciones_llamada")
SEGMENTOS = {
    "presencial": dict(usuarios=1000, sesiones=20000, acciones=42000, antiguedad_meses=50.4, usuarios_llamada=900,
                       llamadas_turnos=500000, sesiones_llamada=15000, acciones_llamada=40000,
                       antiguedad_meses_llamada=61.2),
    "telefonico": dict(usuarios=800, sesiones=100000, acciones=130000, antiguedad_meses=15.1, usuarios_llamada=700,
                       llamadas_turnos=600000, sesiones_llamada=90000, acciones_llamada=210000,
                       antiguedad_meses_llamada=14.8),
}
SEMANAS = [  # acciones, sesiones, %usabilidad, TMO, %FCR, %NPS, Var_Sesiones
    ("64.000", "27.000", "9,0 %", "10:10", "85,0 %", "60,0 %", "0,0 %"),
    ("62.000", "26.000", "9,5 %", "09:50", "86,0 %", "61,0 %", "-3,7 %"),
    ("66.000", "28.000", "10,0 %", "09:30", "87,0 %", "62,0 %", "7,7 %"),
    ("58.000", "24.000", "9,6 %", "09:20", "88,0 %", "63,0 %", "-14,3 %"),
]


def valores() -> dict[str, float]:
    """'segmento.metrica' -> valor, para los 3 segmentos y la licencia."""
    resultado = {f"{seg}.{m}": v for seg, datos in SEGMENTOS.items() for m, v in datos.items()}
    for metrica in CONTEOS:
        resultado[f"total.{metrica}"] = sum(SEGMENTOS[s][metrica] for s in SEGMENTOS)
    resultado["total.antiguedad_meses"] = 30.2
    resultado["total.antiguedad_meses_llamada"] = 35.5
    for seg in ("total", *SEGMENTOS):
        resultado[f"{seg}.acciones_x_sesion"] = round(resultado[f"{seg}.acciones"] / resultado[f"{seg}.sesiones"], 1)
        resultado[f"{seg}.acciones_x_sesion_llamada"] = round(
            resultado[f"{seg}.acciones_llamada"] / resultado[f"{seg}.sesiones_llamada"], 1)
        resultado[f"{seg}.usabilidad"] = round(
            resultado[f"{seg}.sesiones_llamada"] / resultado[f"{seg}.llamadas_turnos"] * 100, 1)
    resultado["general.licencias_asignadas"] = 2000
    return resultado


def formatear(valor: float, decimales: int, porcentaje: bool) -> str:
    """1234567.8 -> '1.234.567,8' (formato del reporte)."""
    texto = f"{valor:,.{decimales}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return texto + (" %" if porcentaje else "")


def textos_tarjetas(cambios: dict[str, str] | None = None) -> dict[int, str]:
    """bloque -> texto de la tarjeta. `cambios` permite alterar bloques para las pruebas."""
    datos = valores()
    textos = {}
    for bloque, asignacion in MAPA["tarjetas"].items():
        if bloque.startswith("_"):
            continue
        segmento, metrica = asignacion
        definicion = MAPA["metricas"][metrica]
        textos[int(bloque)] = formatear(datos[f"{segmento}.{metrica}"], definicion["decimales"],
                                        definicion["unidad"] == "porcentaje")
    textos.update({int(k): v for k, v in (cambios or {}).items()})
    return textos


def tokens_semanas(dia_actualizacion: int = 28) -> list[str]:
    tokens = ["Semana Mes", "Acciones", "Sesiones", "%Usabilidad", "TMO/TPA", "%FCR", "%NPS", "Var_Sesiones %"]
    for numero, fila in enumerate(SEMANAS, start=1):
        tokens += [f"Semana {numero}", *fila]
    datos = valores()
    tokens += ["Total", "250.000", "105.000", formatear(datos["total.usabilidad"], 1, True), "09:43", "86,5 %",
               "61,5 %"]
    return tokens


def captura_consola(dia_actualizacion: int = 28, cambios: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Captura en el formato de la inyección por consola (lista de visuales sin nombres)."""
    def visual(nombre: str, textos: list[str]) -> dict[str, Any]:
        return {"Sección / Visual": nombre, "Valores Extraídos": textos}

    captura = [visual("Visual / Bloque 4", [f"Actualizado: miércoles, {dia_actualizacion} de octubre de 2026"])]
    captura += [visual(f"Visual / Bloque {b}", [t]) for b, t in sorted(textos_tarjetas(cambios).items())]
    captura.append(visual("Visual / Bloque 114", ["Gerencia", "Usuarios", "Sesiones", "Acciones", "Acc_x_Sesion",
                                                   "Gerencia Norte", "Gerencia Sur", "Total"]))
    captura.append(visual("Visual / Bloque 119", tokens_semanas(dia_actualizacion)))
    captura.append(visual("0,0 mill.", ["0,0 mill.0,0 mill.", "1,31,3", "Grupo_AntiguedadGrupo_Antiguedad",
                                        "1) 0 - 3 meses1) 0 - 3 meses", "2) 3 - 6 meses2) 3 - 6 meses",
                                        "3) Más de 6 meses3) Más de 6 meses", "5.000", "30.000", "137.000",
                                        "4.000", "50.000", "196.000", "1,6", "1,4", "1,3"]))
    captura.append(visual("Visual / Bloque 127", ["2,9 %", "9,1 %", "9,4 %"]))
    captura.append(visual("Visual / Bloque 128", ["Canal", "Usuario", "Nombre", "Sesiones"]))
    return captura


def captura_nodo(dia_actualizacion: int = 28) -> dict[str, Any]:
    """Captura en el formato del nodo (Selenium): bloques numerados y tablas con filas."""
    visuales = [{"bloque": int(v["Sección / Visual"].split()[-1]) if "Bloque" in v["Sección / Visual"] else 125,
                 "titulo": None if "Bloque" in v["Sección / Visual"] else v["Sección / Visual"],
                 "textos": v["Valores Extraídos"], "filas": []}
                for v in captura_consola(dia_actualizacion)]
    gerencias = next(v for v in visuales if v["bloque"] == 114)
    gerencias["textos"] = []
    gerencias["filas"] = [
        ["Gerencia", "Usuarios", "Sesiones", "Acciones", "Acc_x_Sesion"],
        ["Gerencia Norte", "1.100", "70.000", "100.000", "1,4"],
        ["Gerencia Sur", "700", "50.000", "72.000", "1,4"],
        ["", "0", "0", "0", "0,0"],
        ["Total", "1.800", "120.000", "172.000", "1,4"],
    ]
    return {"capturado_en": "2026-10-28T08:00:00-05:00", "origen": "prueba", "visuales": visuales}


def html_reporte(captura: dict[str, Any]) -> str:
    """Página local que imita el DOM de Power BI (visual-container, svg text, roles ARIA)."""
    por_bloque = {v["bloque"]: v for v in captura["visuales"]}
    partes = ["<!doctype html><html><head><meta charset='utf-8'><title>Reporte de prueba</title></head><body>"]
    for bloque in range(1, max(por_bloque) + 1):
        visual = por_bloque.get(bloque)
        if visual is None:
            partes.append("<visual-container></visual-container>")
            continue
        titulo = f"<title>{visual['titulo']}</title>" if visual["titulo"] else ""
        textos = "".join(f"<text>{t}</text>" for t in visual["textos"])
        tabla = ""
        if visual["filas"]:
            filas = []
            for i, fila in enumerate(visual["filas"]):
                celdas = "".join(
                    f"<div role='{'columnheader' if i == 0 else 'rowheader' if j == 0 else 'gridcell'}'>{c}</div>"
                    for j, c in enumerate(fila))
                filas.append(f"<div role='row' style='height:30px'>{celdas}</div>")
            tabla = f"<div style='height:60px;overflow:auto'><div role='grid'>{''.join(filas)}</div></div>"
        partes.append(f"<visual-container><svg>{titulo}{textos}</svg>{tabla}</visual-container>")
    partes.append("</body></html>")
    return "".join(partes)
