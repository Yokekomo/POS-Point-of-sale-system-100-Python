// El tutorial guiado de cada pantalla.
//
// Esto vivía dentro del HTML de cada pantalla: se bajaba entero, otra vez, en
// cada pestaña que se toca. Aquí se baja una sola vez y el navegador lo
// guarda; lo que cambia de una pantalla a otra —los textos traducidos, el
// número de la sesión, los pasos del tutorial— llega en `window.GRILL`, que
// sí va en la página. Ver thegrill/web/estaticos.py.
// El tutorial de esta pantalla. Los pasos llegan ya traducidos y ya filtrados
// por nivel desde el servidor: aquí no se decide quién ve qué.
(function () {
  var PASOS = window.GRILL.tour.pasos;
  var PANTALLA = window.GRILL.tour.pantalla;
  var TEXTOS = {
    siguiente: window.GRILL.tour.textos.next,
    atras: window.GRILL.tour.textos.back,
    saltar: window.GRILL.tour.textos.skip,
    hecho: window.GRILL.tour.textos.done,
    paso: window.GRILL.tour.textos.step
  };
  // La biblioteca se cuelga de `window.driver.js` cuando se carga así, suelta
  // en la página. Se mira también `window.driver` por si cambia de sitio.
  var LIB = (window.driver && window.driver.js) || window.driver;
  if (!LIB || !LIB.driver || !PASOS.length) return;

  // Un paso cuyo elemento no está en la página se salta: una lista vacía —un
  // día sin piezas que pesar— no puede dejar el tutorial a medias.
  var pasos = PASOS.filter(function (p) {
    return document.querySelector('[data-tour="' + p.selector + '"]');
  }).map(function (p) {
    return {element: '[data-tour="' + p.selector + '"]',
            popover: {title: p.titulo, description: p.texto}};
  });
  // [01725] Si no hay ni un paso que enseñar, **no se apunta nada**. Antes aquí
  // ponía `apuntar(true)`, con la idea buena de que un día sin piezas que pesar
  // no dejara el tutorial a medias. El efecto era el contrario: el tutorial del
  // inventario se gastaba el día que no había inventario abierto, y faltaba el
  // día que lo había —que es la operación más cara de equivocar del programa—.
  // Sin apuntar nada, espera al día en que la pantalla tiene algo que enseñar.
  if (!pasos.length) return;

  var apuntado = false;
  function apuntar(completo) {
    // Una sola vez por carga: si el envío falla, no se vuelve a intentar ni
    // vuelve a salir el tutorial en esta sesión.
    if (apuntado) return;
    apuntado = true;
    var datos = {csrf: window.GRILL.csrf, pantalla: PANTALLA,
                 completo: completo ? "1" : "0",
                 // Cuántos se enseñaron de verdad, que no siempre son los que
                 // tiene el tutorial: los que no estaban en la página se
                 // quitaron arriba. Con este número el servidor sabe si le
                 // falta alguno por enseñar y lo saca otro día.
                 pasos: String(pasos.length)};
    if (!navigator.onLine && window.cola) { window.cola.apuntar("/tour/visto", datos); return; }
    var cuerpo = new FormData();
    Object.keys(datos).forEach(function (k) { cuerpo.append(k, datos[k]); });
    fetch("/tour/visto", {method: "POST", body: cuerpo, credentials: "same-origin"})
      .catch(function () { if (window.cola) window.cola.apuntar("/tour/visto", datos); });
  }

  var guia = LIB.driver({
    showProgress: true,
    progressText: TEXTOS.paso,            // «Paso {current} de {total}»
    nextBtnText: TEXTOS.siguiente,
    prevBtnText: TEXTOS.atras,
    doneBtnText: TEXTOS.hecho,
    showButtons: ["next", "previous", "close"],
    allowClose: true,
    steps: pasos,
    // La biblioteca deja el cerrar como una equis pequeña. Aquí «Saltar» va
    // con todas sus letras y en todos los pasos: quien no quiere el tutorial
    // tiene que poder salirse sin buscar nada.
    onPopoverRender: function (popover) {
      if (popover && popover.closeButton) {
        popover.closeButton.textContent = TEXTOS.saltar;
        popover.closeButton.classList.add("tour-saltar");
      }
    },
    // Se apunta cuando el tutorial se cierra, sea por «Saltar», por
    // «Entendido» o por tocar fuera. Con `onDestroyStarted` puesto, la
    // biblioteca deja el cierre en nuestras manos: por eso se llama a
    // `destroy()` aquí, que si no el globo se quedaría en la pantalla.
    // [01726] Saltar no es terminar, y hasta hoy se apuntaban igual: los dos
    // llamaban a `apuntar(true)`. La columna `completo` existe desde el
    // principio, con el comentario «falso: lo saltó» al lado, y nunca se
    // escribía en falso. Así el encargado no tenía forma de saber quién se
    // saltó el tutorial de la pantalla que más se equivoca su gente.
    //
    // Que quede un paso por delante es lo que distingue una cosa de la otra:
    // quien le da a «Entendido» en el último los ha visto todos.
    onDestroyStarted: function () { apuntar(!guia.hasNextStep()); guia.destroy(); },
    onDestroyed: function () { apuntar(!guia.hasNextStep()); }
  });
  guia.drive();
})();
