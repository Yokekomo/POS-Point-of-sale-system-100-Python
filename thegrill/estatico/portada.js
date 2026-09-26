// Los guiones de la portada y de las páginas públicas.
//
// Esto vivía dentro del HTML de cada página: se bajaba entero, otra vez, en
// cada una. Aquí se baja una sola vez y el navegador lo guarda.
// Ver thegrill/web/estaticos.py.
// Quien llega a la portada o a la pantalla de entrar no tiene sesión: si el
// aparato guardaba copias de las pantallas de otro, se borran aquí. Un móvil
// de cocina lo usan cuatro personas.
//
// Solo el almacén con datos dentro. El armazón —el guion del tutorial, su
// hoja de estilo, el icono— no lleva nada de nadie y se queda: borrarlo
// también era lo que dejaba la cámara sin pantallas cada mañana, porque el
// ayudante no cambia y no vuelve a rellenarlo por su cuenta.
if (window.caches) {
  caches.keys().then(function (ks) {
    ks.forEach(function (k) { if (k.indexOf("armazon") < 0) caches.delete(k); });
  });
}

// Lo que flota abajo tapa el final de la página, así que hay que reservarle
// su hueco. En la hoja de estilo hay un número escrito para quien llegue sin
// guiones, pero el alto de verdad depende del idioma y del ancho: el mismo
// aviso mide 79 px en un ordenador y 206 en un teléfono en alemán. Aquí se
// mide y se apunta en `--flotante`, y la hoja de estilo lo usa.
(function () {
  var cajas = ["cookiebar", "pidebarra"]
    .map(function (id) { return document.getElementById(id); })
    .filter(Boolean);
  if (!cajas.length) return;
  // Y la cabecera va pegada arriba, así que lo mismo por ese lado: se mide
  // para que el navegador no deje nada justo debajo de ella al acercarlo.
  var cabecera = document.querySelector("header");
  function reservar() {
    // Cada barra que se ve, más los 24 px que se deja con la siguiente y con
    // el final de la página. Las que están escondidas no ocupan nada.
    var alto = 0;
    cajas.forEach(function (c) {
      if (!c.hidden) alto += c.getBoundingClientRect().height + 24;
    });
    document.documentElement.style.setProperty(
      "--flotante", alto ? alto + "px" : "0px");
    if (cabecera) {
      document.documentElement.style.setProperty(
        "--cabecera", Math.round(cabecera.getBoundingClientRect().height) + 12 + "px");
    }
  }
  reservar();
  window.addEventListener("resize", reservar);
  if (window.ResizeObserver) {
    var ro = new ResizeObserver(reservar);
    cajas.forEach(function (c) { ro.observe(c); });
  }
  if (window.MutationObserver) {
    var mo = new MutationObserver(reservar);
    cajas.forEach(function (c) {
      mo.observe(c, {attributes: true, attributeFilter: ["hidden"]});
    });
  }
})();

// La barra de pedir acceso sale cuando el botón de la portada se va de la
// pantalla al bajar, y se esconde cuando vuelve. Mientras se ve el de arriba,
// dos botones iguales a la vez no ayudan: estorban.
//
// Se mira con `IntersectionObserver`, que es el navegador quien avisa cuando
// algo entra o sale de la vista. La alternativa —escuchar cada scroll y medir
// posiciones— hace cuentas sesenta veces por segundo mientras alguien baja con
// el dedo, y eso en un teléfono de cocina se nota.
(function () {
  var barra = document.getElementById("pidebarra");
  if (!barra) return;
  var aviso = document.getElementById("cookiebar");

  // Dónde se pone. El aviso de cookies vive abajo del todo también, y cambia
  // de alto con el idioma y con lo ancha que sea la pantalla: en alemán ocupa
  // una línea más que en inglés. Un número escrito a mano acierta en un sitio
  // y se solapa en los otros seis, así que lo mide el navegador.
  function sitio() {
    if (barra.hidden) return;
    var alto = (aviso && !aviso.hidden) ? aviso.getBoundingClientRect().height : 0;
    barra.style.bottom = alto ? (alto + 24) + "px" : "";
  }
  function enseñar(si) { barra.hidden = !si; sitio(); }

  window.addEventListener("resize", sitio);
  // Y se vuelve a medir al cerrar el aviso, que es cuando la barra baja a su
  // sitio de siempre.
  if (aviso && window.MutationObserver) {
    new MutationObserver(sitio).observe(aviso, {attributes: true,
                                                attributeFilter: ["hidden"]});
  }

  var arriba = document.querySelector(".hero .cta-row");
  // En precios y en cookies no hay botón arriba: la barra se queda puesta.
  if (!arriba || !window.IntersectionObserver) { enseñar(true); return; }
  new IntersectionObserver(function (entradas) {
    enseñar(!entradas[0].isIntersecting);
  }, {threshold: 0}).observe(arriba);
})();

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
