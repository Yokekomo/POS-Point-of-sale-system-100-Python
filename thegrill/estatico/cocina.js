// Los guiones de las pantallas de la edición de cocina.
//
// Esto vivía dentro del HTML de cada página: se bajaba entero, otra vez, en
// cada una. Aquí se baja una sola vez y el navegador lo guarda.
// Ver thegrill/web/estaticos.py.
// Refresca el contador de avisos sin recargar la página. Si la persona ha
// autorizado los avisos del navegador, una alerta crítica salta a su pantalla.
(function () {
  var badge = document.getElementById("navbadge");
  if (!badge) return;
  function remember(key, value) { try { localStorage.setItem(key, value); } catch (e) {} }
  function recall(key) { try { return Number(localStorage.getItem(key) || 0); } catch (e) { return 0; } }
  var lastSeen = recall("grill_last_notif");
  // [01833] El aviso grave, por el camino que el teléfono deja abierto.
  //
  // Esto era `new Notification(...)` a pelo. En el ordenador va; en el móvil
  // —que es donde está el que trabaja— **revienta**: Chrome de Android no deja
  // construirla así y contesta «Illegal constructor», y el Safari del iPhone ni
  // la tiene si la web no está instalada en la pantalla de inicio. Y como la
  // llamada iba dentro del `then`, el error caía en el `.catch` vacío de abajo:
  // se tragaba el aviso, se saltaba los que venían detrás en la misma tanda y ni
  // siquiera apuntaba por dónde iba. O sea, justo el aviso que no puede
  // perderse —una cámara a nueve grados, una pieza caducando— no llegaba a
  // ningún teléfono y no dejaba rastro de no haber llegado.
  //
  // En el móvil la avisa el trabajador de la web, que sí puede; en el
  // ordenador, la de siempre; y si no se puede ninguna de las dos, no pasa
  // nada: el número de la barra y la columna de novedades siguen ahí. Lo que no
  // puede es volver a tumbar el resto de la vuelta.
  function avisar(it) {
    try {
      if (!window.Notification || Notification.permission !== "granted") return;
      var comoEs = { body: it.body, tag: "grill-" + it.id };
      if (navigator.serviceWorker && navigator.serviceWorker.ready) {
        navigator.serviceWorker.ready.then(function (reg) {
          return reg.showNotification(it.title, comoEs);
        }).catch(function () { try { new Notification(it.title, comoEs); } catch (e) {} });
        return;
      }
      new Notification(it.title, comoEs);
    } catch (e) { /* este aparato no deja: el número y la columna siguen ahí */ }
  }
  function poll() {
    fetch("/api/notificaciones", { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        badge.textContent = d.unread > 99 ? "99+" : d.unread;
        badge.hidden = d.unread === 0;
        var top = lastSeen;
        d.items.forEach(function (it) {
          if (it.id > top) top = it.id;
          if (it.id > lastSeen && it.severity === "CRITICAL") avisar(it);
        });
        if (top !== lastSeen) { lastSeen = top; remember("grill_last_notif", top); }
      })
      .catch(function () {});
  }
  poll();
  setInterval(poll, 30000);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) poll(); });
})();

// El eco de las casillas de peso: se escriben gramos y debajo aparecen los
// kilos. Quien está delante teclea 9435 y lee «9,435 kg», que es la cifra que
// sabe de memoria; un 94350 por un cero de más canta solo.
//
// Se hace aquí y no con el `pattern` del navegador porque no es una
// validación: es una segunda lectura. El navegador puede decir «esto no vale»
// y aun así dejar pasar un peso diez veces mayor, que es un número
// perfectamente válido y perfectamente equivocado. Lo único que lo caza es
// verlo en la unidad de la báscula.
(function () {
  var COMA = {es: 1, fr: 1, de: 1, nl: 1, hu: 1};
  var idioma = (document.documentElement.lang || "en").slice(0, 2);
  var sep = COMA[idioma] ? "," : ".";
  var textos = {kg: window.GRILL.textos.kg, mal: window.GRILL.textos.malgramos};

  function kilos(gramos) {
    // Tres decimales siempre: «9,400» y no «9,4». Una báscula da tres cifras
    // y quitarle los ceros hace dudar de si faltan dígitos.
    var s = (gramos / 1000).toFixed(3);
    return s.replace(".", sep) + " " + textos.kg;
  }

  function pintar(casilla) {
    var eco = casilla.parentNode && casilla.parentNode.querySelector("[data-eco]");
    if (!eco) return;
    // La casilla pudo dejar de ser de peso desde que se enganchó —en las
    // recetas cambia según el ingrediente—: entonces no hay nada que decir.
    if (!casilla.hasAttribute("data-peso")) {
      eco.textContent = ""; eco.removeAttribute("data-mal"); return;
    }
    var escrito = (casilla.value || "").replace(/[\s.  ]/g, "");
    if (!escrito) { eco.textContent = ""; eco.removeAttribute("data-mal"); return; }
    // Una coma en gramos casi siempre son kilos mal puestos. Se dice aquí,
    // antes de darle al botón, y no después de haber perdido la hoja.
    if (!/^\d+$/.test(escrito)) {
      eco.textContent = textos.mal;
      eco.setAttribute("data-mal", "1");
      return;
    }
    eco.removeAttribute("data-mal");
    eco.textContent = kilos(parseInt(escrito, 10));
  }

  function enganchar(raiz) {
    (raiz || document).querySelectorAll("input[data-peso]").forEach(function (c) {
      if (c.getAttribute("data-eco-puesto")) return;
      c.setAttribute("data-eco-puesto", "1");
      c.addEventListener("input", function () { pintar(c); });
      pintar(c);                       // y al cargar, que puede venir con valor
    });
  }

  enganchar(document);
  // Las filas de los cortes y de las piezas se añaden con un botón: el eco
  // tiene que aparecer también en las que no existían al cargar la pantalla.
  if (window.MutationObserver) {
    new MutationObserver(function () { enganchar(document); })
      .observe(document.body, {childList: true, subtree: true});
  }
  window.eco = {enganchar: enganchar};
})();

// [01738] El cartel rojo se lleva el foco, y por eso se oye.
//
// Hasta hoy, al fallar algo salía el cartel arriba y el foco se iba —por el
// `autofocus` de la plantilla— al primer campo, que está **por debajo** del
// cartel. Quien no ve la pantalla no se enteraba de nada: volvía a teclear lo
// mismo sin saber qué había pasado. Y el `role="alert"` por sí solo no basta,
// porque un cartel que ya está en la página cuando se carga no siempre se
// anuncia.
//
// Solo el rojo, y solo el primero: el verde se anuncia con `role="status"` y no
// tiene por qué robarle el sitio a nadie.
(function () {
  var malo = document.querySelector(".banner.bad[tabindex]");
  if (!malo || malo.hidden) return;
  try { malo.focus({preventScroll: false}); } catch (e) { malo.focus(); }
})();
