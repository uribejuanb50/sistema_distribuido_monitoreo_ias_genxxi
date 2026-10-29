"""Pensamiento (el "cerebro") del nodo padre.

Decide si una observación nueva representa un cambio relevante comparándola con su
historial. Solo contiene funciones puras: recibe números y devuelve una Evaluacion.
No toca la base de datos ni la red, por eso es fácil de probar y de recalibrar.

Reglas (todas configurables en config.json -> "analisis"; umbrales EXPERIMENTALES):
A. Magnitud: variación = actual − anterior (pp si el KPI es porcentaje, o % relativo
   si el perfil usa modo "relativa"), clasificada en intervalos que no se solapan:
   [0,1), [1,2), [2,3), [3,4), [4,5), [5,∞).
B. Meta: ¿el valor actual cumple la meta? Se combina con la magnitud del deterioro en
   una matriz de decisión (fila "cumple" / "no_cumple").
C. Criticidad: ajusta la PRIORIDAD de revisión, no reemplaza el nivel de alerta.
D/E. Tendencia: N variaciones consecutivas del mismo signo (regla experimental, N=3).
F. Volatilidad: desviación estándar reciente vs histórica (exploratoria).
G. Estabilidad: N variaciones consecutivas con |variación| < umbral.
H. Patrón repetitivo: deterioros seguidos de recuperaciones (exploratorio, no confirmado).
I. Dato inusual: |z| > 2 frente a las variaciones históricas, solo si hay suficientes
   observaciones (exploratorio: sugiere revisar el dato, no afirma un error).
"""
from __future__ import annotations

import fnmatch
import statistics
from bisect import bisect_right
from dataclasses import asdict, dataclass, field
from itertools import pairwise
from typing import Any

NIVELES_ALERTA = ("sin_alerta", "baja", "media", "alta")
CRITICIDADES = ("critica", "importante", "seguimiento")
MODOS_VARIACION = ("absoluta", "relativa")
SENTIDOS = ("subir", "bajar")  # sentido en el que el KPI mejora

ACCIONES_RECOMENDADAS = {
    "sin_alerta": "Sin acción. Continuar el monitoreo habitual.",
    "baja": "Revisar en el próximo seguimiento periódico.",
    "media": "Revisar el KPI en los próximos días y validar la calidad del dato.",
    "alta": "Revisar de inmediato: validar el dato y escalar al responsable del KPI.",
}
PRIORIDAD_BASE = {"alta": 1, "media": 2, "baja": 3}
AJUSTE_CRITICIDAD = {"critica": -1, "importante": 0, "seguimiento": 1}


def _rango(nivel: str) -> int:
    return NIVELES_ALERTA.index(nivel)


def _mayor_nivel(a: str, b: str) -> str:
    return a if _rango(a) >= _rango(b) else b


def _limpiar(datos: dict[str, Any]) -> dict[str, Any]:
    return {clave: valor for clave, valor in datos.items() if not clave.startswith("_")}


# ---------------------------------------------------------------- configuración

@dataclass(frozen=True)
class ReglasGenerales:
    ventana_tendencia: int = 3
    umbral_estabilidad: float = 1.0
    ventana_volatilidad: int = 4
    factor_volatilidad_alta: float = 1.5
    min_observaciones_estadistica: int = 6
    umbral_z: float = 2.0
    min_variaciones_patron: int = 6
    min_repeticiones_patron: int = 2
    nivel_minimo_notificacion: str = "media"

    def __post_init__(self) -> None:
        if self.nivel_minimo_notificacion not in NIVELES_ALERTA:
            raise ValueError(f"nivel_minimo_notificacion inválido: {self.nivel_minimo_notificacion!r}")
        if self.ventana_tendencia < 2 or self.ventana_volatilidad < 2:
            raise ValueError("Las ventanas de tendencia y volatilidad deben ser >= 2")


@dataclass(frozen=True)
class PerfilKPI:
    """Cómo se analiza un KPI (o grupo de KPI)."""

    criticidad: str = "seguimiento"
    modo_variacion: str = "absoluta"
    sentido_favorable: str = "subir"
    limites_magnitud: tuple[float, ...] = (1.0, 2.0, 3.0, 4.0, 5.0)
    matriz_cumple: tuple[str, ...] = ("sin_alerta", "sin_alerta", "baja", "baja", "media", "media")
    matriz_no_cumple: tuple[str, ...] = ("baja", "baja", "media", "media", "alta", "alta")

    def __post_init__(self) -> None:
        if self.criticidad not in CRITICIDADES:
            raise ValueError(f"criticidad inválida: {self.criticidad!r}")
        if self.modo_variacion not in MODOS_VARIACION:
            raise ValueError(f"modo_variacion inválido: {self.modo_variacion!r}")
        if self.sentido_favorable not in SENTIDOS:
            raise ValueError(f"sentido_favorable inválido: {self.sentido_favorable!r}")
        limites = self.limites_magnitud
        if not limites or limites[0] <= 0 or any(b <= a for a, b in pairwise(limites)):
            raise ValueError(f"limites_magnitud deben ser positivos y crecientes: {limites}")
        for nombre in ("matriz_cumple", "matriz_no_cumple"):
            fila = getattr(self, nombre)
            if len(fila) != len(limites) + 1:
                raise ValueError(f"{nombre} debe tener {len(limites) + 1} niveles (uno por intervalo)")
            if any(nivel not in NIVELES_ALERTA for nivel in fila):
                raise ValueError(f"{nombre} contiene niveles inválidos: {fila}")

    @classmethod
    def desde_dict(cls, datos: dict[str, Any], base: PerfilKPI | None = None) -> PerfilKPI:
        """Crea un perfil a partir del JSON; lo que no se indique se toma de `base`."""
        valores = asdict(base or cls())
        for clave, valor in _limpiar(datos).items():
            if clave == "patron":
                continue
            if clave == "matriz_caidas":
                valores["matriz_cumple"] = tuple(valor["cumple"])
                valores["matriz_no_cumple"] = tuple(valor["no_cumple"])
            elif clave in valores:
                valores[clave] = tuple(valor) if isinstance(valor, list) else valor
            else:
                raise ValueError(f"Parámetro de perfil desconocido: {clave!r}")
        valores["limites_magnitud"] = tuple(float(x) for x in valores["limites_magnitud"])
        return cls(**valores)


@dataclass(frozen=True)
class ConfiguracionAnalisis:
    reglas: ReglasGenerales = field(default_factory=ReglasGenerales)
    perfil_por_defecto: PerfilKPI = field(default_factory=PerfilKPI)
    perfiles: tuple[tuple[str, PerfilKPI], ...] = ()  # (patrón de kpi_id, perfil)

    @classmethod
    def desde_dict(cls, datos: dict[str, Any]) -> ConfiguracionAnalisis:
        reglas = ReglasGenerales(**_limpiar(datos.get("reglas_generales", {})))
        por_defecto = PerfilKPI.desde_dict(datos.get("perfil_por_defecto", {}))
        perfiles = tuple(
            (perfil["patron"], PerfilKPI.desde_dict(perfil, base=por_defecto))
            for perfil in datos.get("perfiles", [])
        )
        return cls(reglas, por_defecto, perfiles)

    def perfil_para(self, kpi_id: str) -> PerfilKPI:
        """Primer perfil cuyo patrón coincide con el kpi_id (estilo comodín: '*.real_*')."""
        for patron, perfil in self.perfiles:
            if fnmatch.fnmatchcase(kpi_id, patron):
                return perfil
        return self.perfil_por_defecto


# ---------------------------------------------------------------- resultado

@dataclass(frozen=True)
class Evaluacion:
    fecha_referencia: str
    valor_actual: float
    valor_anterior: float | None
    meta: float | None
    variacion: float | None
    unidad_variacion: str
    direccion: str               # subida | caida | sin_cambio | sin_referencia
    deterioro: bool              # el cambio va en contra del sentido favorable
    intervalo_magnitud: str | None
    cumple_meta: bool | None
    tendencia: str
    estable: bool | None
    volatilidad: dict[str, Any]
    dato_inusual: dict[str, Any]
    patron_repetitivo: dict[str, Any]
    nivel_alerta: str
    criticidad: str
    prioridad_revision: str | None
    motivos: tuple[str, ...]
    accion_recomendada: str
    es_significativo: bool

    @property
    def senales(self) -> dict[str, Any]:
        """Señales de comportamiento: 'regla' = experimental confirmada; 'exploratoria' = indicio."""
        return {
            "tendencia": {"tipo": "regla", "valor": self.tendencia},
            "estabilidad": {"tipo": "regla", "valor": self.estable},
            "volatilidad": self.volatilidad,
            "dato_inusual": self.dato_inusual,
            "patron_repetitivo": self.patron_repetitivo,
        }

    def a_dict(self) -> dict[str, Any]:
        datos = asdict(self)
        datos["motivos"] = list(self.motivos)
        return datos


# ---------------------------------------------------------------- reglas individuales

def calcular_variacion(actual: float, anterior: float, modo: str) -> float | None:
    if modo == "absoluta":
        return round(actual - anterior, 6)
    if anterior == 0:
        return None  # variación relativa indefinida
    return round((actual - anterior) / abs(anterior) * 100, 6)


def direccion_de(variacion: float | None) -> str:
    if variacion is None:
        return "sin_referencia"
    if variacion > 0:
        return "subida"
    if variacion < 0:
        return "caida"
    return "sin_cambio"


def es_deterioro(variacion: float | None, sentido: str) -> bool:
    if variacion is None:
        return False
    return variacion < 0 if sentido == "subir" else variacion > 0


def es_mejora(variacion: float | None, sentido: str) -> bool:
    if variacion is None or variacion == 0:
        return False
    return not es_deterioro(variacion, sentido)


def cumple_meta(valor: float, meta: float | None, sentido: str) -> bool | None:
    if meta is None:
        return None
    return valor >= meta if sentido == "subir" else valor <= meta


def etiquetas_intervalos(limites: tuple[float, ...]) -> list[str]:
    """(1,2,3) -> ['<1', '[1, 2)', '[2, 3)', '>=3']"""
    def fmt(x: float) -> str:
        return f"{x:g}"
    return ([f"<{fmt(limites[0])}"]
            + [f"[{fmt(a)}, {fmt(b)})" for a, b in pairwise(limites)]
            + [f">={fmt(limites[-1])}"])


def indice_magnitud(valor_absoluto: float, limites: tuple[float, ...]) -> int:
    """Índice del intervalo: los límites pertenecen al intervalo superior (1 -> [1, 2))."""
    return bisect_right(limites, valor_absoluto)


def detectar_tendencia(variaciones: list[float], ventana: int) -> str:
    if not variaciones:
        return "datos_insuficientes"
    ultimas = variaciones[-ventana:]
    if len(ultimas) == ventana and all(v < 0 for v in ultimas):
        return "descendente_sostenida"
    if len(ultimas) == ventana and all(v > 0 for v in ultimas):
        return "ascendente_sostenida"
    if variaciones[-1] < 0:
        return "caida_aislada"
    if variaciones[-1] > 0:
        return "subida_aislada"
    return "sin_cambio"


def detectar_estabilidad(variaciones: list[float], ventana: int, umbral: float) -> bool | None:
    if len(variaciones) < ventana:
        return None
    return all(abs(v) < umbral for v in variaciones[-ventana:])


def evaluar_volatilidad(variaciones: list[float], ventana: int, factor: float) -> dict[str, Any]:
    """Compara la desviación estándar de las últimas `ventana` variaciones con la anterior."""
    resultado: dict[str, Any] = {"tipo": "exploratoria", "ventana": ventana, "desviacion_reciente": None,
                                 "desviacion_historica": None, "razon": None,
                                 "clasificacion": "datos_insuficientes"}
    if len(variaciones) < ventana + 2:  # se necesitan >= 2 variaciones históricas
        return resultado
    reciente = statistics.stdev(variaciones[-ventana:])
    historica = statistics.stdev(variaciones[:-ventana])
    resultado.update(desviacion_reciente=round(reciente, 4), desviacion_historica=round(historica, 4))
    if historica == 0:
        resultado["clasificacion"] = "alta" if reciente > 0 else "normal"
        return resultado
    razon = reciente / historica
    resultado["razon"] = round(razon, 3)
    if razon >= factor:
        resultado["clasificacion"] = "alta"
    elif razon <= 1 / factor:
        resultado["clasificacion"] = "baja"
    else:
        resultado["clasificacion"] = "normal"
    return resultado


def evaluar_dato_inusual(variaciones: list[float], min_observaciones: int, umbral_z: float) -> dict[str, Any]:
    """z-score de la última variación frente a las anteriores (señal exploratoria)."""
    historicas = variaciones[:-1]
    resultado: dict[str, Any] = {"tipo": "exploratoria", "es_inusual": None, "z": None,
                                 "n_historico": len(historicas), "umbral_z": umbral_z}
    if not variaciones or len(historicas) < min_observaciones:
        resultado["nota"] = f"Datos insuficientes (se requieren {min_observaciones} variaciones previas)"
        return resultado
    actual = variaciones[-1]
    media = statistics.fmean(historicas)
    desviacion = statistics.stdev(historicas)
    if desviacion == 0:
        resultado["es_inusual"] = actual != media
        return resultado
    z = (actual - media) / desviacion
    resultado.update(z=round(z, 3), es_inusual=abs(z) > umbral_z)
    return resultado


def detectar_patron_repetitivo(variaciones: list[float], sentido: str,
                               min_variaciones: int, min_repeticiones: int) -> dict[str, Any]:
    """Cuenta deterioros seguidos inmediatamente de una recuperación."""
    resultado: dict[str, Any] = {"tipo": "exploratoria", "detectado": False, "repeticiones": 0,
                                 "n_variaciones": len(variaciones), "descripcion": None}
    if len(variaciones) < min_variaciones:
        resultado["descripcion"] = f"Datos insuficientes (se requieren {min_variaciones} variaciones)"
        return resultado
    repeticiones = sum(1 for a, b in pairwise(variaciones) if es_deterioro(a, sentido) and es_mejora(b, sentido))
    resultado["repeticiones"] = repeticiones
    if repeticiones >= min_repeticiones:
        resultado["detectado"] = True
        resultado["descripcion"] = (f"{repeticiones} deterioros seguidos de recuperación en "
                                    f"{len(variaciones)} variaciones. Posible patrón, NO confirmado.")
    return resultado


def calcular_prioridad(nivel: str, criticidad: str) -> str | None:
    """P1 (más urgente) a P4. La criticidad sube o baja un escalón la prioridad."""
    if nivel == "sin_alerta":
        return None
    prioridad = PRIORIDAD_BASE[nivel] + AJUSTE_CRITICIDAD[criticidad]
    return f"P{min(4, max(1, prioridad))}"


# ---------------------------------------------------------------- evaluación completa

def evaluar(serie: list[tuple[str, float]], meta: float | None, unidad: str,
            perfil: PerfilKPI, reglas: ReglasGenerales) -> Evaluacion:
    """Evalúa el ÚLTIMO punto de `serie` (lista ordenada de (fecha, valor)) contra su historial."""
    if not serie:
        raise ValueError("La serie está vacía")
    valores = [valor for _, valor in serie]
    fecha, actual = serie[-1]
    anterior = valores[-2] if len(valores) > 1 else None
    sentido = perfil.sentido_favorable

    todas = [calcular_variacion(b, a, perfil.modo_variacion) for a, b in pairwise(valores)]
    variacion = todas[-1] if todas else None
    variaciones = [v for v in todas if v is not None]
    if perfil.modo_variacion == "relativa":
        unidad_variacion = "%"
    else:
        unidad_variacion = "pp" if unidad == "porcentaje" else unidad

    deterioro = es_deterioro(variacion, sentido)
    cumple = cumple_meta(actual, meta, sentido)
    etiquetas = etiquetas_intervalos(perfil.limites_magnitud)
    indice = indice_magnitud(abs(variacion), perfil.limites_magnitud) if variacion is not None else None
    intervalo = etiquetas[indice] if indice is not None else None

    if variacion is None:
        tendencia = "datos_insuficientes"
    else:
        tendencia = detectar_tendencia(variaciones, reglas.ventana_tendencia)
    estable = detectar_estabilidad(variaciones, reglas.ventana_tendencia, reglas.umbral_estabilidad)
    volatilidad = evaluar_volatilidad(variaciones, reglas.ventana_volatilidad, reglas.factor_volatilidad_alta)
    inusual = (evaluar_dato_inusual(variaciones, reglas.min_observaciones_estadistica, reglas.umbral_z)
               if variacion is not None else evaluar_dato_inusual([], reglas.min_observaciones_estadistica,
                                                                   reglas.umbral_z))
    patron = detectar_patron_repetitivo(variaciones, sentido, reglas.min_variaciones_patron,
                                        reglas.min_repeticiones_patron)

    # --- decisión del nivel de alerta
    nivel = "sin_alerta"
    motivos: list[str] = []
    texto_meta = {True: "cumpliendo la meta", False: "sin cumplir la meta", None: "sin meta definida"}[cumple]
    if anterior is None:
        motivos.append("Primera observación del KPI: no hay valor anterior para comparar.")
    if deterioro:
        fila = perfil.matriz_no_cumple if cumple is False else perfil.matriz_cumple
        nivel = fila[indice]
        motivos.append(f"Deterioro de {abs(variacion):.2f} {unidad_variacion} (intervalo {intervalo}), "
                       f"{texto_meta}.")
    elif cumple is False:
        nivel = "baja"
        motivos.append(f"Sigue por debajo de la meta ({actual:g} vs {meta:g}): se mantiene el seguimiento.")

    tendencia_negativa = "descendente_sostenida" if sentido == "subir" else "ascendente_sostenida"
    if tendencia == tendencia_negativa:
        nivel = _mayor_nivel(nivel, "media")
        motivos.append(f"Deterioro sostenido: {reglas.ventana_tendencia} variaciones consecutivas en contra "
                       f"(regla experimental).")
    if inusual["es_inusual"]:
        if deterioro:
            nivel = _mayor_nivel(nivel, "media")
        else:
            nivel = _mayor_nivel(nivel, "baja")
        z = f"z={inusual['z']}" if inusual["z"] is not None else "historial sin variación"
        motivos.append(f"Cambio inusual frente al historial ({z}, señal exploratoria): "
                       f"verificar la calidad del dato antes de concluir.")
    if estable and cumple is False:
        motivos.append("Estable pero por debajo de la meta: la estabilidad no implica buen desempeño.")
    if not motivos:
        motivos.append("Sin cambios relevantes.")

    return Evaluacion(
        fecha_referencia=fecha,
        valor_actual=actual,
        valor_anterior=anterior,
        meta=meta,
        variacion=variacion,
        unidad_variacion=unidad_variacion,
        direccion=direccion_de(variacion),
        deterioro=deterioro,
        intervalo_magnitud=intervalo,
        cumple_meta=cumple,
        tendencia=tendencia,
        estable=estable,
        volatilidad=volatilidad,
        dato_inusual=inusual,
        patron_repetitivo=patron,
        nivel_alerta=nivel,
        criticidad=perfil.criticidad,
        prioridad_revision=calcular_prioridad(nivel, perfil.criticidad),
        motivos=tuple(motivos),
        accion_recomendada=ACCIONES_RECOMENDADAS[nivel],
        es_significativo=_rango(nivel) >= _rango(reglas.nivel_minimo_notificacion),
    )
