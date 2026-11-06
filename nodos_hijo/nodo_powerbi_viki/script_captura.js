// Inyección JS que lee el reporte Power BI. La ejecuta navegador.py con Selenium
// (driver.execute_script), que la envuelve en una función: por eso usa `return` en vez de
// console.log y recibe parámetros en `arguments`.
//
// Es la misma lógica de la inyección que se usaba directo en la consola del navegador:
//   - recorre cada 'visual-container' del tablero,
//   - toma el título del visual (o "Visual / Bloque N" si no tiene),
//   - extrae los textos de 'svg text, .pivotTableCellWrap, .tablixCellContent, .value'.
// Y agrega lo que Python necesita para interpretar los números:
//   - bloque: la posición del visual desde 1 (la misma "N" de "Visual / Bloque N"),
//   - posicion: dónde está el visual en la página,
//   - filas: las tablas modernas de Power BI marcan sus celdas con roles de accesibilidad
//     (columnheader / rowheader / gridcell); el selector original no alcanza esos números.
//
// Uso desde Python:
//   execute_script(SCRIPT, null) -> todos los visuales (incluso los vacíos, para no
//                                   perder la numeración de bloques)
//   execute_script(SCRIPT, 7)    -> solo las filas de la tabla del bloque 7 (al desplazarla)
//
// Para probarla a mano en la consola del navegador: (function () { ...este código... })(null)

const SELECTOR_TEXTOS = 'svg text, .pivotTableCellWrap, .tablixCellContent, .value';
const SELECTOR_TITULO = '.visualTitle, .header-text, title';

const leerFilas = (visual) => Array.from(visual.querySelectorAll('[role="row"]'))
    .map((fila) => Array.from(fila.querySelectorAll('[role="columnheader"], [role="rowheader"], [role="gridcell"]'))
        .map((celda) => celda.textContent.trim()))
    .filter((celdas) => celdas.length > 0);

const contenedores = Array.from(document.querySelectorAll('visual-container'));
const soloBloque = arguments.length > 0 ? arguments[0] : null;
if (soloBloque !== null && soloBloque !== undefined) {
    const visual = contenedores[soloBloque - 1];
    return visual ? leerFilas(visual) : [];
}

let estructuraReporte = [];
contenedores.forEach((visual, index) => {
    // Intenta obtener el título del gráfico o tabla
    const tituloElem = visual.querySelector(SELECTOR_TITULO);
    const tituloReal = tituloElem ? tituloElem.textContent.trim() : '';
    const titulo = tituloReal || `Visual / Bloque ${index + 1}`;

    // Extrae los textos únicamente dentro de este contenedor
    const textos = Array.from(visual.querySelectorAll(SELECTOR_TEXTOS))
        .map((nodo) => nodo.textContent.trim())
        .filter((texto) => texto !== '');

    const caja = visual.getBoundingClientRect();
    estructuraReporte.push({
        'Sección / Visual': titulo,      // mismos nombres que la inyección por consola
        'Valores Extraídos': textos,
        bloque: index + 1,
        titulo: tituloReal || null,
        posicion: {x: Math.round(caja.left), y: Math.round(caja.top),
                   ancho: Math.round(caja.width), alto: Math.round(caja.height)},
        textos: textos,
        filas: leerFilas(visual),
    });
});
return estructuraReporte;
