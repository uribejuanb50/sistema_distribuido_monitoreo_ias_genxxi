// Captura del reporte Power BI. Lo ejecuta navegador.py con Selenium (execute_script).
//
// - Sin argumento (null): devuelve TODOS los visuales con su bloque, título, posición,
//   textos y filas de tabla.
// - Con un número de bloque: devuelve solo las filas de esa tabla (se usa al desplazarla).
//
// "bloque" es la posición del 'visual-container' en la página, desde 1: la misma
// numeración de la inyección por consola original, para que el mapa siga siendo válido.
// Las tablas modernas de Power BI marcan sus celdas con roles de accesibilidad
// (columnheader / rowheader / gridcell): así se leen sus números, que el selector de
// textos original no alcanzaba.
// Para probarlo en la consola del navegador, envuélvelo en (function () { ... })(null).

const leerFilas = (visual) => Array.from(visual.querySelectorAll('[role="row"]'))
    .map((fila) => Array.from(fila.querySelectorAll('[role="columnheader"], [role="rowheader"], [role="gridcell"]'))
        .map((celda) => celda.textContent.trim()))
    .filter((celdas) => celdas.length > 0);

const contenedores = Array.from(document.querySelectorAll('visual-container'));
const soloBloque = arguments.length > 0 ? arguments[0] : null;
if (soloBloque !== null) {
    const visual = contenedores[soloBloque - 1];
    return visual ? leerFilas(visual) : [];
}
return contenedores.map((visual, indice) => {
    const caja = visual.getBoundingClientRect();
    const titulo = visual.querySelector('.visualTitle, .header-text, title');
    return {
        bloque: indice + 1,
        titulo: titulo ? titulo.textContent.trim() : null,
        posicion: {x: Math.round(caja.left), y: Math.round(caja.top),
                   ancho: Math.round(caja.width), alto: Math.round(caja.height)},
        textos: Array.from(visual.querySelectorAll('svg text, .pivotTableCellWrap, .tablixCellContent, .value'))
            .map((nodo) => nodo.textContent.trim())
            .filter((texto) => texto !== ''),
        filas: leerFilas(visual),
    };
});
