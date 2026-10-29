"""Figuras Plotly del dashboard (sin Streamlit, para poder probarlas por separado).

Reglas de color:
- La serie del KPI usa un solo azul; la meta es una línea gris discontinua (umbral).
- Subidas/caídas usan el par divergente azul/rojo (validado para daltonismo).
- Los niveles de alerta usan colores de ESTADO reservados, siempre con etiqueta y
  símbolo distinto (el color nunca es la única pista).
- Cada color tiene su versión para modo claro y oscuro.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

PALETA = {
    "claro": {"serie": "#2a78d6", "subida": "#2a78d6", "caida": "#e34948", "neutro": "#898781",
              "rejilla": "#e1e0d9", "eje": "#c3c2b7", "texto": "#52514e", "superficie": "#fcfcfb"},
    "oscuro": {"serie": "#3987e5", "subida": "#3987e5", "caida": "#e66767", "neutro": "#898781",
               "rejilla": "#2c2c2a", "eje": "#383835", "texto": "#c3c2b7", "superficie": "#1a1a19"},
}
# Colores de estado (iguales en ambos modos) + símbolo y etiqueta para no depender del color.
NIVELES = {
    "alta": {"color": "#d03b3b", "simbolo": "diamond", "etiqueta": "Alta", "icono": "🔴"},
    "media": {"color": "#ec835a", "simbolo": "square", "etiqueta": "Media", "icono": "🟠"},
    "baja": {"color": "#fab219", "simbolo": "triangle-up", "etiqueta": "Baja", "icono": "🟡"},
    "sin_alerta": {"color": "#0ca30c", "simbolo": "circle", "etiqueta": "Sin alerta", "icono": "🟢"},
}
TENDENCIAS = {
    "descendente_sostenida": "Descendente sostenida",
    "ascendente_sostenida": "Ascendente sostenida",
    "caida_aislada": "Caída aislada",
    "subida_aislada": "Subida aislada",
    "sin_cambio": "Sin cambio",
    "datos_insuficientes": "Datos insuficientes",
}
ORDEN_INTERVALOS = ["<1", "[1, 2)", "[2, 3)", "[3, 4)", "[4, 5)", ">=5"]


def etiqueta_nivel(nivel: str | None) -> str:
    """'alta' -> '🔴 Alta' (icono + texto)."""
    if nivel not in NIVELES:
        return "Sin evaluar"
    return f"{NIVELES[nivel]['icono']} {NIVELES[nivel]['etiqueta']}"


def _estilo_base(figura: go.Figure, modo: str, titulo_y: str, alto: int = 360) -> go.Figure:
    c = PALETA[modo]
    figura.update_layout(
        height=alto,
        margin=dict(l=8, r=8, t=76, b=8),  # título arriba y leyenda debajo, sin solaparse
        font=dict(family='system-ui, -apple-system, "Segoe UI", sans-serif', color=c["texto"]),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        hoverlabel=dict(font_family='system-ui, -apple-system, "Segoe UI", sans-serif'),
        barcornerradius=4,
    )
    figura.update_layout(title=dict(yref="container", y=1, yanchor="top", pad=dict(t=12)))
    # automargin: deja espacio a las etiquetas de los ejes (el Plotly.js de Streamlit no lo activa solo).
    figura.update_xaxes(showgrid=False, linecolor=c["eje"], ticks="", automargin=True)
    figura.update_yaxes(title_text=titulo_y, gridcolor=c["rejilla"], zeroline=True, automargin=True,
                        zerolinecolor=c["eje"], zerolinewidth=1)
    return figura


def figura_evolucion(historial: pd.DataFrame, unidad: str, modo: str = "claro") -> go.Figure:
    """Valor del KPI vs meta, con los periodos que generaron alerta marcados."""
    c = PALETA[modo]
    figura = go.Figure()
    figura.add_trace(go.Scatter(
        x=historial["fecha_referencia"], y=historial["valor"], name="Valor", mode="lines+markers",
        line=dict(color=c["serie"], width=2), marker=dict(size=8, color=c["serie"]),
        hovertemplate="%{y:,.2f}<extra>Valor</extra>",
    ))
    if historial["meta"].notna().any():
        figura.add_trace(go.Scatter(
            x=historial["fecha_referencia"], y=historial["meta"], name="Meta", mode="lines",
            line=dict(color=c["neutro"], width=2, dash="dash", shape="hv"),
            hovertemplate="%{y:,.2f}<extra>Meta</extra>",
        ))
    for nivel in ("baja", "media", "alta"):
        puntos = historial[historial["nivel_alerta"] == nivel]
        if puntos.empty:
            continue
        estilo = NIVELES[nivel]
        figura.add_trace(go.Scatter(
            x=puntos["fecha_referencia"], y=puntos["valor"], name=f"Alerta {estilo['etiqueta'].lower()}",
            mode="markers",
            marker=dict(size=13, color=estilo["color"], symbol=estilo["simbolo"],
                        line=dict(width=2, color=c["superficie"])),  # anillo que lo separa de la línea
            customdata=puntos["notificada"].map({True: "notificada", False: "registrada"}),
            hovertemplate=f"Alerta {estilo['etiqueta'].lower()} (%{{customdata}})<extra></extra>",
        ))
    figura.update_layout(hovermode="x unified", title=dict(text="Valor frente a la meta", x=0, font_size=15))
    return _estilo_base(figura, modo, unidad)


def figura_variaciones(historial: pd.DataFrame, unidad_variacion: str, modo: str = "claro") -> go.Figure:
    """Variación entre observaciones consecutivas: azul = subida, rojo = caída."""
    c = PALETA[modo]
    datos = historial.dropna(subset=["variacion"])
    colores = [c["subida"] if v > 0 else c["caida"] if v < 0 else c["neutro"] for v in datos["variacion"]]
    figura = go.Figure(go.Bar(
        x=datos["fecha_referencia"], y=datos["variacion"], marker_color=colores, name="Variación",
        customdata=datos["nivel_alerta"].map(etiqueta_nivel),
        hovertemplate="%{y:+,.2f} " + unidad_variacion + "<br>Nivel: %{customdata}<extra></extra>",
    ))
    figura.update_layout(bargap=0.35, showlegend=False,
                         title=dict(text="Variación frente al periodo anterior (azul sube · rojo baja)", x=0,
                                    font_size=15))
    return _estilo_base(figura, modo, unidad_variacion, alto=300)


def figura_frecuencias(simulacion: pd.DataFrame, observado: pd.DataFrame | None, modo: str = "claro") -> go.Figure:
    """Frecuencia por intervalo de magnitud: simulación Monte Carlo y, si existe, lo observado."""
    c = PALETA[modo]
    fuentes = [("Simulación Monte Carlo", simulacion)]
    if observado is not None:
        fuentes.append(("Observado en el KPI", observado))
    figura = go.Figure()
    for columna, (titulo, datos) in enumerate(fuentes, start=1):
        for direccion, nombre in (("caida", "Caídas"), ("subida", "Subidas")):
            filas = datos[datos["direccion"] == direccion].set_index("intervalo").reindex(ORDEN_INTERVALOS)
            figura.add_trace(go.Bar(
                x=ORDEN_INTERVALOS, y=filas["porcentaje"], name=nombre, marker_color=c[direccion],
                legendgroup=direccion, showlegend=columna == 1, xaxis=f"x{columna}", yaxis=f"y{columna}",
                hovertemplate=f"{nombre} %{{x}}: %{{y:.2f}} %<extra>{titulo}</extra>",
            ))
    columnas = len(fuentes)
    ancho = 1 / columnas
    for indice, (titulo, _) in enumerate(fuentes):
        sufijo = "" if indice == 0 else str(indice + 1)
        figura.update_layout({
            f"xaxis{sufijo}": dict(domain=[indice * ancho + 0.03, (indice + 1) * ancho - 0.03],
                                   title_text=f"Magnitud del cambio · {titulo}"),
            f"yaxis{sufijo}": dict(anchor=f"x{indice + 1}", matches="y" if indice else None),
        })
    figura.update_layout(barmode="group", bargap=0.3, bargroupgap=0.08,
                         title=dict(text="Frecuencia por intervalo de magnitud (%)", x=0, font_size=15))
    return _estilo_base(figura, modo, "% de casos")


def figura_tendencias(estado: pd.DataFrame, modo: str = "claro") -> go.Figure:
    """Cuántos KPI hay en cada estado de tendencia."""
    c = PALETA[modo]
    conteo = estado["tendencia"].map(TENDENCIAS).fillna("Sin evaluar").value_counts().sort_values()
    figura = go.Figure(go.Bar(
        x=conteo.values, y=conteo.index, orientation="h", marker_color=c["serie"],
        text=conteo.values, textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: %{x} KPI<extra></extra>",
    ))
    figura.update_layout(bargap=0.4, showlegend=False,
                         title=dict(text="KPI por tendencia detectada", x=0, font_size=15))
    figura = _estilo_base(figura, modo, "", alto=max(220, 60 + 40 * len(conteo)))
    figura.update_xaxes(showticklabels=False, showgrid=False, zeroline=False)
    figura.update_yaxes(showgrid=False, zeroline=False)
    return figura
