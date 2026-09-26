// Lo que se hace al final del todo: el aviso de cookies y el cartel rojo.
//
// Esto vivía dentro del HTML de cada pantalla: se bajaba entero, otra vez, en
// cada pestaña que se toca. Aquí se baja una sola vez y el navegador lo
// guarda; lo que cambia de una pantalla a otra —los textos traducidos, el
// número de la sesión, los pasos del tutorial— llega en `window.GRILL`, que
// sí va en la página. Ver thegrill/web/estaticos.py.
(function () {
  var barra = document.getElementById("cookiebar");

  function esconder() {
    if (barra) barra.hidden = true;
    // La ventana de las cookies se cierra con el ancla vacía, como su equis.
    if (location.hash === "#cookies-info") { cerrar_ventana(); }
  }

  function cerrar_ventana() {
    var y = window.scrollY;
    location.hash = "";
    requestAnimationFrame(function () { window.scrollTo(0, y); });
  }

  var avisos = document.querySelectorAll('form[action="/cookies/visto"]');
  if (avisos.length && window.fetch) {
    Array.prototype.forEach.call(avisos, function (f) {
      f.addEventListener("submit", function (e) {
        e.preventDefault();
        fetch(f.action, {method: "POST", body: new FormData(f),
                         credentials: "same-origin"})
          .then(esconder)
          // Sin red, se manda como se mandaba: mejor recargar que no cerrarlo.
          .catch(function () { f.submit(); });
      });
    });
  }

  // La equis y el fondo de cualquier ventana: cierran sin moverse del sitio.
  document.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest("a.close, a.veil") : null;
    if (!a || a.getAttribute("href") !== "#") { return; }
    e.preventDefault();
    cerrar_ventana();
  });
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
