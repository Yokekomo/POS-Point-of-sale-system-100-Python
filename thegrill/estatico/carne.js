// Los guiones de las pantallas de trabajo de la edición de carne.
//
// Esto vivía dentro del HTML de cada pantalla: se bajaba entero, otra vez, en
// cada pestaña que se toca. Aquí se baja una sola vez y el navegador lo
// guarda; lo que cambia de una pantalla a otra —los textos traducidos, el
// número de la sesión, los pasos del tutorial— llega en `window.GRILL`, que
// sí va en la página. Ver thegrill/web/estaticos.py.
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

// La columna de avisos, compartida. Dos sitios ponen carteles ahí arriba —la
// cola cuando no hay cobertura, y lo que acaba de pasar en la casa—, y si cada
// uno se pinta por su cuenta en el mismo sitio se tapan el uno al otro y no se
// lee ninguno. Se apilan; y el hueco que hay que dejarle al contenido lo mide
// la propia columna, que no siempre lleva lo mismo dentro.
window.avisos = (function () {
  var pila = document.getElementById("pilavisos");
  function medir() {
    if (!pila) return;
    // La barra de arriba del teléfono, si la hay: en horizontal y en el
    // ordenador no existe, y entonces mide cero y la columna va arriba del todo.
    var barra = document.querySelector(".topbar");
    var alto_barra = barra ? barra.offsetHeight : 0;
    document.body.style.setProperty("--alto-barra", alto_barra + "px");
    document.body.classList.toggle("con-barra", alto_barra > 0);
    var alto = 0;
    pila.querySelectorAll(".aviso-red[data-fijo]").forEach(function (el) {
      if (el.hidden) return;
      alto = Math.max(alto, el.offsetTop + el.offsetHeight);
    });
    // Solo apartan el contenido los que se quedan puestos. Los que se van solos
    // en dos segundos flotan por encima: bajar la pantalla entera y devolverla
    // al sitio da un salto peor que el propio cartel.
    // El hueco es lo que miden los carteles, más los ocho de separación con la
    // barra y los veinte que la pantalla tiene siempre arriba.
    document.body.style.setProperty("--alto-avisos", alto ? (alto + 28) + "px" : "0px");
    document.body.classList.toggle("con-aviso", alto > 0);
  }
  // Al girar el teléfono, la barra de arriba aparece o desaparece y el cartel
  // cambia de ancho: si no se vuelve a medir, se queda flotando en el sitio de
  // antes o tapando el título.
  window.addEventListener("resize", medir);
  window.addEventListener("orientationchange", medir);
  medir();

  return {
    medir: medir,
    nuevo: function (clase) {
      var el = document.createElement("div");
      el.className = "aviso-red " + (clase || "");
      el.hidden = true;
      if (pila) pila.appendChild(el);
      return el;
    },
    ver: function (el, si) {
      if (!el) return;
      el.hidden = !si;
      // Enseñarlo y pintarlo de golpe se salta la transición: se pide la
      // altura en medio para que el navegador dibuje el primer estado.
      if (si) { void el.offsetHeight; el.classList.add("se-ve"); }
      else { el.classList.remove("se-ve"); }
      medir();
    }
  };
})();

// El ayudante que guarda copia de las pantallas visitadas, para que dentro de
// la cámara se abran igual. El navegador solo lo permite con certificado o en
// el propio ordenador; donde no lo permita, no pasa nada: lo que se escribe
// se sigue guardando en el teléfono.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", function () {
    // El idioma viaja en la dirección: así la pantalla de «sin conexión» que
    // guarda este ayudante está escrita en el de la casa y no en el que
    // resulte que pida el navegador.
    navigator.serviceWorker.register(window.GRILL.ayudante).catch(function () {});
  });
}

// ================================================ trabajar sin cobertura
// En un restaurante la señal falla: la cámara no tiene, el sótano tampoco y la
// wifi va y viene. Trabajar no puede depender de eso.
//
// Lo que se escribe se guarda en el propio teléfono mientras se teclea, y al
// darle a guardar **sin red el trabajo se da por hecho**: la pantalla sigue
// adelante y el envío se queda en una cola aquí dentro. Se manda solo, en
// orden y de uno en uno, en cuanto vuelva la señal —al volver, al abrir
// cualquier pantalla y cada poco rato—, porque el aviso del navegador no
// existe en el iPhone y esperarlo sería no mandarlo nunca.
//
// Cada envío lleva su número, puesto aquí antes del primer intento y el mismo
// en todos los reintentos. Si un intento entró y se perdió la respuesta, el
// servidor reconoce el número y contesta «ya está hecho» sin tocar los kilos.
(function () {
  if (!window.localStorage) return;
  var COLA = "grill_cola";
  var textos = {
    guardado: window.GRILL.textos.saved,
    pendiente: window.GRILL.textos.pending,
    enviando: window.GRILL.textos.sending,
    enviado: window.GRILL.textos.sent,
    fallo: window.GRILL.textos.failed,
    rechazado: window.GRILL.textos.rejected,
    encola: window.GRILL.textos.queued,
    pendiente_titulo: window.GRILL.textos.pending_nav,
    sinsenal: window.GRILL.textos.gone,
    vuelve: window.GRILL.textos.back,
    apuntado: window.GRILL.textos.noted,
    espera: window.GRILL.textos.waiting,
    reintento: window.GRILL.textos.retrying,
    sinfoto: window.GRILL.textos.sinfoto,
    sinsitio: window.GRILL.textos.nospace,
    entrar: window.GRILL.textos.login_again,
    reintentar: window.GRILL.textos.resend,
    tirar: window.GRILL.textos.discard
  };

  function cola() {
    try { return JSON.parse(localStorage.getItem(COLA) || "[]"); } catch (e) { return []; }
  }
  // [01713] Devuelve si de verdad quedó guardado. Antes esto era un `catch` vacío y
  // la línea siguiente pintaba el panel con la lista que estaba en memoria: con
  // el teléfono sin sitio, el apunte se perdía y la pantalla decía «Apuntado. Se
  // manda solo». Un inventario de cuarenta piezas contado en la cámara se podía
  // ir entero sin que nadie se enterara nunca. Y un móvil de cocina con dos mil
  // fotos es exactamente el móvil al que le pasa.
  //
  // Si no cabe, se hace sitio con lo único que se puede tirar sin perder nada
  // que no esté ya visto: los rechazados viejos, que una persona ya miró. Y si
  // aun así no cabe, se dice que no cabe. Mentir aquí es lo caro.
  function guardarCola(lista) {
    if (!escribirCola(COLA, lista)) {
      try { localStorage.removeItem("grill_apartados"); } catch (e) {}
      if (!escribirCola(COLA, lista)) { pintarContador(cola()); return false; }
    }
    pintarContador(lista);
    return true;
  }

  function escribirCola(clave, lista) {
    try { localStorage.setItem(clave, JSON.stringify(lista)); return true; }
    catch (e) { return false; }
  }
  function numero() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return String(Date.now()) + "-" + Math.random().toString(16).slice(2);
  }

  // Lo que falta por mandar se ve en todas las pantallas, con su hora y su
  // estado. Si no se ve, nadie se entera de que su recuento sigue en el
  // teléfono —y eso es peor que no tener cola—.
  function pintarContador(lista) {
    lista = lista || cola();
    document.querySelectorAll("[data-cola-cuenta]").forEach(function (marca) {
      marca.textContent = lista.length;
      marca.hidden = lista.length === 0;
    });
    var panel = document.getElementById("colapanel");
    var main = document.querySelector("main");
    // También cuenta lo apartado: si el panel desapareciera al vaciarse la
    // cola, lo que el servidor rechazó no se volvería a ver nunca y no habría
    // manera de reintentarlo ni de tirarlo.
    if (!lista.length && !apartados().length) { if (panel) panel.remove(); return; }
    if (!panel && main) {
      panel = document.createElement("div");
      panel.id = "colapanel";
      panel.className = "card";
      main.insertBefore(panel, main.firstChild);
    }
    if (!panel) return;
    var fuera = apartados();
    function fila(x, esperando) {
      var hora = new Date(x.cuando).toTimeString().slice(0, 5);
      var que = (x.accion || "").replace("/", "").replace("/", " · ");
      // Qué es, no solo de qué pantalla viene: con veinte apuntes iguales en
      // la lista hay que poder saber cuál es cuál sin abrir nada.
      var suyo = x.datos || {};
      var pistas = [];
      ["serial", "serial:0", "kg", "kg:0", "lot", "pieces"].forEach(function (k) {
        if (suyo[k]) pistas.push(suyo[k]);
      });
      var estado = esperando ? (x.intentos ? textos.reintento : textos.espera)
                             : textos.rechazado;
      var botones = esperando ? "" :
        '<button type="button" class="btn ghost" data-vuelve="' + x.id + '">' +
        textos.reintentar + '</button>' +
        '<button type="button" class="btn ghost" data-tira="' + x.id + '">' +
        textos.tirar + '</button>';
      return '<div class="rowflex" style="gap:10px;padding:8px 0;flex-wrap:wrap;' +
             'align-items:center">' +
             '<span class="tag ' + (esperando ? "warn" : "bad") + '">' + hora + "</span>" +
             "<b>" + que + "</b>" +
             '<span class="muted">' + pistas.slice(0, 3).join(" · ") + "</span>" +
             '<span class="muted" style="flex:1">' + estado + "</span>" + botones + "</div>";
    }
    var filas = lista.map(function (x) { return fila(x, true); }).join("") +
                fuera.map(function (x) { return fila(x, false); }).join("");
    panel.innerHTML = '<b>' + textos.pendiente_titulo + " · " +
                      (lista.length + fuera.length) + "</b>" + filas;
    panel.querySelectorAll("[data-vuelve]").forEach(function (b) {
      b.addEventListener("click", function () {
        window.cola.reintentar(b.getAttribute("data-vuelve"));
      });
    });
    panel.querySelectorAll("[data-tira]").forEach(function (b) {
      b.addEventListener("click", function () {
        window.cola.tirar(b.getAttribute("data-tira"));
      });
    });
  }

  // El aviso flotante de la señal. Se pone arriba del todo, en todas las
  // pantallas, y no hay que ir a buscarlo: si se ha ido la cobertura, se ve; y
  // cuando vuelve, lo dice y se quita solo. Se crea vacío ya, antes de que
  // haga falta, para que quede el primero de la columna: la señal manda sobre
  // cualquier otra cosa que se apile debajo.
  var globo = window.avisos ? window.avisos.nuevo("") : null, relojGlobo = null;
  if (globo) globo.id = "avisored";
  function decir(texto, malo, segundos) {
    if (!globo) return;
    if (!texto) {
      window.avisos.ver(globo, false);
      return;
    }
    globo.textContent = texto;
    globo.classList.remove("malo", "bien");
    globo.classList.add(malo ? "malo" : "bien");
    clearTimeout(relojGlobo);
    // Lo malo se queda puesto y aparta el contenido: hay que saber que no hay
    // señal y el cartel no puede taparle el título a la pantalla. Lo bueno se
    // va solo, flotando por encima: un cartel fijo diciendo que todo va bien
    // estorba, y mover la página para meterlo y sacarlo, más.
    if (segundos) { globo.removeAttribute("data-fijo"); }
    else { globo.setAttribute("data-fijo", "1"); }
    window.avisos.ver(globo, true);
    if (segundos) {
      relojGlobo = setTimeout(function () {
        window.avisos.ver(globo, false);
      }, segundos * 1000);
    }
    var viejo = document.getElementById("sinred");   // el de dentro, si lo hay
    if (viejo) {
      viejo.textContent = texto;
      viejo.hidden = false;
      viejo.className = "banner " + (malo ? "warn" : "ok");
    }
  }

  // ------------------------------------------------ lo escrito, en el aparato
  //
  // [01730] En plural, y no en singular como estaba. `/traslados` tiene dos fichas
  // —«mandar pieza» y «mandar corte»— y esto solo enganchaba la primera: lo
  // escrito en la segunda se perdía al recargar, sin decir nada. La marca
  // `data-keep` la llevan las dos desde el principio; lo que faltaba era
  // mirarlas todas.
  var form = null;                        // la primera, para el `localStorage.removeItem`
  document.querySelectorAll("form[data-keep]").forEach(function (ficha) {
    if (form === null) form = ficha;
    var clave = "cuenta:" + ficha.dataset.keep;
    function leer() { try { return JSON.parse(localStorage.getItem(clave) || "{}"); }
                      catch (e) { return {}; } }
    var guardado = leer(), había = false;
    for (var nombre in guardado) {
      var campo = ficha.elements[nombre];
      if (campo && !campo.value) { campo.value = guardado[nombre]; había = true; }
    }
    if (había) decir(textos.guardado, false);
    ficha.addEventListener("input", function (ev) {
      var campo = ev.target;
      if (!campo.name || campo.type === "hidden") return;
      var datos = leer();
      if (campo.value) { datos[campo.name] = campo.value; } else { delete datos[campo.name]; }
      try { localStorage.setItem(clave, JSON.stringify(datos)); } catch (e) {}
    });
  });

  // --------------------------------------- guardar sin red: se apunta y sigue
  //
  // Lo que aguanta una persona con una pieza en la mano mirando la pantalla.
  // Pasado esto, el envío se corta y se apunta: más vale «apuntado, sin
  // señal» en siete segundos que una pantalla quieta que no dice nada.
  var PLAZO_SIN_RESPUESTA = 7000;

  document.querySelectorAll("form[data-cola]").forEach(function (formulario) {
    // La llave contra duplicados va SIEMPRE, con red y sin ella. El servidor
    // sabe reconocer un envío repetido —`_ya_estaba`— pero solo lo hacía con
    // los que venían de la cola, o sea justo en el caso raro: con cobertura,
    // dos toques con guante apuntaban dos mermas. Se pone al pintar y se
    // renueva después de cada envío, que si no el segundo alta del mismo
    // formulario llegaría con la llave del primero y se tomaría por repetida.
    function marcarEnvio() {
      var campo = formulario.querySelector("input[name=envio]");
      if (!campo) {
        campo = document.createElement("input");
        campo.type = "hidden";
        campo.name = "envio";
        formulario.appendChild(campo);
      }
      campo.value = numero();
      return campo;
    }
    var llave = marcarEnvio();

    // `colgado` es que el envío salió y nadie contestó. No es lo mismo que no
    // tener red: aquí el navegador dice que sí la hay, y por eso el aviso que
    // se enseña y lo que se hace después son distintos.
    function alaCola(colgado) {
      var datos = {};
      var conFichero = false;
      new FormData(formulario).forEach(function (v, k) {
        // Un fichero no cabe aquí: la cola vive en el almacén del navegador,
        // que guarda texto. Meter una foto de tres megas por pieza lo llena y
        // no se manda nunca. Se aparta y se avisa: lo escrito sí se guarda.
        if (typeof File !== "undefined" && v instanceof File) {
          if (v.name) conFichero = true;
          return;
        }
        datos[k] = v;
      });
      datos.envio = llave.value || numero();
      marcarEnvio();                  // el siguiente apunte lleva la suya
      // La hora a la que se escribió, no la de cuando salga. Un recuento
      // apuntado a las 23:50 en la cámara y mandado a las 00:10 cuando el
      // teléfono vuelve a tener señal quedaba fechado al día siguiente: el
      // turno de noche entero cambiaba de día y ni el consumo ni el food cost
      // de ninguno de los dos volvían a cuadrar.
      datos.cuando = new Date().toISOString();
      var lista = cola();
      lista.push({id: datos.envio, accion: formulario.getAttribute("action"),
                  datos: datos, cuando: Date.now(), intentos: 0});
      if (!guardarCola(lista)) {
        // [01714] No cabe en el teléfono. Lo que NO se hace aquí es vaciar el
        // formulario: lo escrito es lo único que queda de ese pesaje, y se deja
        // en la pantalla para que se pueda mandar en cuanto haya señal o
        // apuntar en un papel. El aviso se queda puesto, sin contador.
        decir(textos.sinsitio, true);
        return;
      }
      if (form && formulario === form) localStorage.removeItem("cuenta:" + form.dataset.keep);
      formulario.reset();
      // Un «apuntado» corto y se quita; si sigue sin haber señal, vuelve el
      // aviso de que no la hay, que es lo que hace falta tener delante.
      decir(conFichero ? textos.sinfoto : textos.apuntado, false, conFichero ? 6 : 2);
      setTimeout(function () {
        // Si el envío se quedó colgado, la señal no está aunque el navegador
        // diga que sí: se dice lo mismo que cuando no la hay, porque para
        // quien está delante es exactamente lo mismo.
        if (colgado || !navigator.onLine) decir(textos.sinsenal, true);
      }, conFichero ? 6100 : 2100);
      // Y no se reintenta de inmediato lo que acaba de no llegar: se deja al
      // reintento de cada poco, que es el que espera a que la red vuelva.
      if (!colgado) mandar();
    }

    formulario.addEventListener("submit", function (ev) {
      // Con algo esperando delante, a la cola siempre: colarse rompería el
      // orden —una pieza no se puede despiezar antes de recibirla—.
      if (!navigator.onLine || cola().length) {
        ev.preventDefault();
        alaCola();
        return;
      }
      // Y aquí el navegador dice que hay red. En una cámara eso no quiere
      // decir nada: el punto de acceso se ve desde dentro y no se llega a
      // ninguna parte, así que `navigator.onLine` da que sí, el envío sale y
      // se queda colgado. La persona se queda mirando una pantalla que no
      // hace nada, sin saber si lo suyo se guardó, que es justo lo que este
      // programa no se puede permitir.
      //
      // Así que el envío sale y se le pone un reloj. Si en unos segundos no
      // ha contestado, se corta y se apunta en la cola, y quien está delante
      // sigue trabajando. Si además el envío llegaba tarde y entra, no pasa
      // nada: los dos llevan la misma llave y el servidor reconoce el
      // repetido. Es para lo que está la llave.
      if (form && formulario === form) {
        localStorage.removeItem("cuenta:" + form.dataset.keep);
      }
      var reloj = setTimeout(function () {
        window.stop();                // se corta el que no llega
        alaCola(true);
      }, PLAZO_SIN_RESPUESTA);
      window.addEventListener("pagehide", function () { clearTimeout(reloj); },
                              {once: true});
    });
  });

  // ------------------------------------------- la cola, de uno en uno y en orden
  var mandando = false;

  function mandar() {
    var lista = cola();
    if (!lista.length || mandando || !navigator.onLine) return;
    var primero = lista[0];
    mandando = true;
    decir(textos.enviando, true);
    // El token se coge AHORA, no cuando se apuntó. Un móvil compartido entre
    // el turno de mañana y el de tarde guardaba el token de quien lo tenía
    // antes, y al mandarlo el servidor contestaba 403: el apunte se quedaba
    // atascado por algo que no tenía nada que ver con lo apuntado.
    var ahoraToken = window.GRILL.csrf;
    if (ahoraToken && primero.datos && "csrf" in primero.datos) {
      primero.datos.csrf = ahoraToken;
    }
    var cuerpo = new URLSearchParams(primero.datos);
    var corte = ("AbortController" in window) ? new AbortController() : null;
    var reloj = setTimeout(function () { if (corte) corte.abort(); }, 15000);
    fetch(primero.accion, {
      method: "POST", body: cuerpo, credentials: "same-origin",
      // Sin esto, el navegador sigue solo la redirección a la pantalla de
      // entrar, devuelve su 200 y la cola daba por guardado lo que no se
      // guardó: el recuento desaparecía y la pantalla cantaba «enviado».
      redirect: "manual",
      headers: {"Content-Type": "application/x-www-form-urlencoded",
                "X-Cola": "1"},
      signal: corte ? corte.signal : undefined
    }).then(function (r) {
      clearTimeout(reloj);
      mandando = false;
      var ahora = cola();

      // Hay que volver a entrar. No se toca nada: lo apuntado espera a que
      // alguien entre otra vez, que es exactamente lo que tiene que pasar.
      if (r.status === 401 || r.status === 402 || r.type === "opaqueredirect" ||
          (r.status >= 300 && r.status < 400)) {
        decir(textos.entrar, true);
        return;
      }

      if (r.ok) {
        ahora = ahora.filter(function (x) { return x.id !== primero.id; });
        guardarCola(ahora);
        decir(ahora.length ? textos.enviando : textos.enviado, ahora.length > 0,
              ahora.length ? 0 : 4);
        if (ahora.length) { mandar(); }
        else { setTimeout(function () { location.reload(); }, 600); }
        return;
      }

      // El servidor lo ha rechazado de verdad: la hoja se cerró, la pieza ya
      // no está. Eso no se reintenta solo, pero **no puede tapar a los de
      // detrás**: se aparta a su propia lista y la cola sigue. Antes se
      // quedaba el primero y congelaba el turno entero.
      if (r.status >= 400 && r.status < 500) {
        ahora = ahora.filter(function (x) { return x.id !== primero.id; });
        guardarCola(ahora);
        primero.rechazado = r.status;
        apartar(primero);
        decir(textos.rechazado, true);
        mandar();
        return;
      }
      throw new Error(r.status);      // 5xx: culpa del servidor, se reintenta
    }).catch(function () {
      clearTimeout(reloj);
      mandando = false;
      var ahora = cola();
      ahora.forEach(function (x) { if (x.id === primero.id) x.intentos = (x.intentos || 0) + 1; });
      guardarCola(ahora);
      decir(textos.fallo, true);
    });
  }

  // Lo apartado: lo que el servidor rechazó. Vive fuera de la cola para que no
  // tape a nadie, y se queda hasta que una persona lo mire.
  function apartados() {
    try { return JSON.parse(localStorage.getItem("grill_apartados") || "[]"); }
    catch (e) { return []; }
  }

  function guardarApartados(lista) {
    // [01715] Aquí el `catch` vacío duele menos —lo apartado ya lo vio una persona—
    // pero tampoco se da por guardado lo que no cupo: el contador se pinta con
    // lo que hay de verdad en el disco.
    escribirCola("grill_apartados", lista);
    pintarContador();
  }

  function apartar(envio) {
    var lista = apartados();
    lista.push(envio);
    guardarApartados(lista.slice(-40));      // cuarenta ya son demasiados
  }

  // Se reintenta al volver la señal, al abrir cualquier pantalla y cada poco:
  // el aviso del navegador no llega en el iPhone, así que no se espera solo a él.
  // Se avisa de la señal aunque no haya nada en la cola: quien está contando
  // tiene que saber que se ha quedado sin red antes de escribir veinte pesos.
  window.addEventListener("online", function () {
    decir(textos.vuelve, false, 4);
    mandar();
  });
  window.addEventListener("offline", function () { decir(textos.sinsenal, true); });
  if (!navigator.onLine) decir(textos.sinsenal, true);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) mandar();
  });
  // Al entrar, se le pide al ayudante que vuelva a guardar las pantallas de
  // trabajo: al cerrar sesión se borraron, y su `activate` no se dispara otra
  // vez mientras el ayudante no cambie. Una vez por sesión del navegador, que
  // son quince peticiones y no hacen falta en cada pantalla.
  // [01731] El candado va con el nombre de quien ha entrado, no a secas.
  //
  // Antes era una marca sin más, `grill_lleno`, que se ponía la primera vez y
  // no se quitaba al salir. Como el ayudante tampoco cambia, su arranque no se
  // vuelve a disparar solo: en un móvil que usan cuatro personas al día, el
  // tercer turno ya no llenaba nada y esa persona se encontraba «Sin conexión»
  // al abrir el despiece dentro de la cámara. Y no había nada en pantalla que
  // explicara por qué a uno sí y a otro no.
  try {
    var candado = "grill_lleno:" + window.GRILL.usuario;
    if (!sessionStorage.getItem(candado) && navigator.serviceWorker &&
        navigator.serviceWorker.controller) {
      navigator.serviceWorker.controller.postMessage({tipo: "llena"});
      sessionStorage.setItem(candado, "1");
    }
  } catch (e) {}

  setInterval(mandar, 8000);
  pintarContador();
  if (cola().length && navigator.onLine) mandar();

  // La cola, para quien no es un formulario. El tutorial apunta «ya lo he
  // visto» desde un guion, no desde un `submit`, y dentro de la cámara eso
  // también tiene que esperar a que vuelva la señal en vez de perderse.
  window.cola = {
    apuntar: function (accion, datos) {
      if (datos && !datos.cuando) datos.cuando = new Date().toISOString();
      var carga = {};
      Object.keys(datos || {}).forEach(function (k) { carga[k] = datos[k]; });
      carga.envio = numero();
      var lista = cola();
      lista.push({id: carga.envio, accion: accion, datos: carga,
                  cuando: Date.now(), intentos: 0});
      guardarCola(lista);
      if (navigator.onLine) mandar();
    },
    pendientes: function () { return cola().length; },
    // Lo que el servidor rechazó, para que una persona lo mire y decida.
    apartados: apartados,
    reintentar: function (id) {
      var fuera = apartados(), vuelve = null;
      fuera = fuera.filter(function (x) {
        if (x.id !== id) return true;
        vuelve = x; return false;
      });
      guardarApartados(fuera);
      if (!vuelve) return;
      delete vuelve.rechazado;
      var lista = cola();
      lista.push(vuelve);
      guardarCola(lista);
      mandar();
    },
    tirar: function (id) {
      guardarApartados(apartados().filter(function (x) { return x.id !== id; }));
    }
  };
})();

// Cada uno deja el menú como le gusta y se lo encuentra igual en la pantalla
// siguiente. Sin esto, plegar un grupo duraba hasta el próximo clic, que es
// peor que no poder plegarlo.
(function () {
  var clave = "grill_menu";
  function leido() {
    try { return JSON.parse(localStorage.getItem(clave) || "{}"); } catch (e) { return {}; }
  }
  var guardado = leido();
  document.querySelectorAll("details.grp").forEach(function (grupo) {
    var nombre = grupo.getAttribute("data-grp");
    // El grupo de la pantalla en la que estás se abre siempre: si no, se
    // plegaría encima de lo que estás mirando.
    var aqui = grupo.querySelector("a.item.on");
    if (!aqui && nombre in guardado) grupo.open = guardado[nombre];
    grupo.addEventListener("toggle", function () {
      var ahora = leido();
      ahora[nombre] = grupo.open;
      try { localStorage.setItem(clave, JSON.stringify(ahora)); } catch (e) {}
    });
  });
})();

// Refresca el contador de avisos sin recargar la página. Si la persona ha
// autorizado los avisos del navegador, una alerta crítica salta a su pantalla.
(function () {
  // [01732] Los tres contadores, no solo el de la barra del móvil.
  //
  // Hay uno en la barra de abajo (`navbadge`), otro en la columna lateral
  // (`navbadgeside`) y otro en el cajón. El refresco tocaba solo el primero, y
  // ese está escondido justo en el ordenador y en la tablet en horizontal: el
  // que lleva la casa miraba la pantalla del despacho todo el día con un número
  // que no cambiaba hasta recargar.
  var badges = [].slice.call(document.querySelectorAll("[data-aviso-contador]"));
  var badge = document.getElementById("navbadge");
  if (badge && badges.indexOf(badge) < 0) badges.push(badge);
  if (!badges.length) return;
  badge = {
    set textContent(v) { badges.forEach(function (b) { b.textContent = v; }); },
    set hidden(v) { badges.forEach(function (b) { b.hidden = v; }); }
  };
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
      var comoEs = { body: it.body, tag: "grill-" + it.id, icon: "/static/icono-180.png" };
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

// ======================================= lo que acaba de pasar en la casa
// Dos personas trabajan la misma carne desde pantallas distintas: el del muelle
// da de alta seis lomos mientras el de la mesa despieza, y el que está contando
// no se entera de ninguna de las dos cosas hasta que va a la cámara. En FEFO
// eso se paga: se saca la pieza vieja porque nadie sabía que había entrado una
// nueva, o se cierra un inventario sin lo que entró hace diez minutos.
//
// Así que lo que pasa sale arriba, en la misma columna que el aviso de la
// señal, y con su X: se lee y se quita. Y lo quitado no vuelve —ni al recargar,
// ni al cambiar de pantalla—, porque un aviso que resucita se deja de mirar.
(function () {
  // Solo dentro, y solo si la columna está montada: en la portada y en la
  // pantalla de entrar no hay a quién avisar.
  if (!window.avisos || !document.getElementById("navbadge")) return;
  var SUELO = "grill_novedad_suelo";   // hasta dónde ha leído este aparato
  var QUITADAS = "grill_novedad_x";    // las que se han cerrado por encima del suelo
  // Cuántas caben a la vez. Con más de tres, la columna se come la pantalla; y
  // si han pasado cinco cosas, el sitio para verlas no es un cartel: son la
  // cámara y la pantalla de cada cosa.
  var MAXIMO = 3;
  var CERRAR = window.GRILL.textos.cerrar;
  var puestas = {};                    // id -> el cartel que está puesto

  function leer(clave, porDefecto) {
    try {
      var crudo = localStorage.getItem(clave);
      return crudo === null ? porDefecto : JSON.parse(crudo);
    } catch (e) { return porDefecto; }
  }
  function escribir(clave, valor) {
    try { localStorage.setItem(clave, JSON.stringify(valor)); } catch (e) {}
  }
  function suelo() { return Number(leer(SUELO, 0)) || 0; }
  function quitadas() { var x = leer(QUITADAS, []); return x instanceof Array ? x : []; }

  // Al cerrar una, el suelo sube todo lo que pueda seguido: si se han cerrado
  // la 11 y la 12 y el suelo estaba en la 10, el suelo pasa a 12 y la lista se
  // vacía. Lo que queda son huecos —la 15 cerrada con la 13 sin leer—, y esos
  // sí hay que recordarlos uno a uno.
  function cerrar(id) {
    var el = puestas[id];
    delete puestas[id];
    if (el) {
      window.avisos.ver(el, false);
      setTimeout(function () { el.remove(); window.avisos.medir(); }, 250);
    }
    var fuera = quitadas();
    if (fuera.indexOf(id) < 0) fuera.push(id);
    fuera.sort(function (a, b) { return a - b; });
    var piso = suelo();
    while (fuera.length && fuera[0] <= piso + 1) { piso = Math.max(piso, fuera.shift()); }
    escribir(SUELO, piso);
    escribir(QUITADAS, fuera.slice(-50));
  }

  function pintar(item) {
    if (puestas[item.id]) return;
    var el = window.avisos.nuevo("nuevo");
    el.setAttribute("data-fijo", "1");
    el.setAttribute("data-novedad", item.id);
    var dice = document.createElement("span");
    dice.className = "dice";
    dice.textContent = item.titulo || item.texto;
    if (item.detalle) {
      var abajo = document.createElement("small");
      abajo.textContent = item.detalle;
      dice.appendChild(abajo);
    }
    var x = document.createElement("button");
    x.type = "button";
    x.className = "x";
    x.setAttribute("aria-label", CERRAR);
    x.textContent = "\u00d7";
    x.addEventListener("click", function () { cerrar(item.id); });
    el.appendChild(dice);
    el.appendChild(x);
    puestas[item.id] = el;
    window.avisos.ver(el, true);
    // Si se amontonan, la más vieja se da por leída. Perderla del cartel no es
    // perderla: está en la cámara y en la pantalla de recepción o de despiece.
    var ids = Object.keys(puestas).map(Number).sort(function (a, b) { return a - b; });
    while (ids.length > MAXIMO) { cerrar(ids.shift()); }
  }

  var primera = leer(SUELO, null) === null;

  function mirar() {
    if (!navigator.onLine) return;    // sin señal no hay novedades que traer
    fetch("/api/novedades?desde=" + suelo(), {
      headers: { Accept: "application/json" }, credentials: "same-origin"
    }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        // Un aparato que entra por primera vez no se merece la pared de lo que
        // pasó esta mañana: empieza a contar desde aquí.
        if (primera) { primera = false; escribir(SUELO, d.ultimo || 0); escribir(QUITADAS, []); return; }
        var fuera = quitadas();
        d.items.forEach(function (it) {
          if (fuera.indexOf(it.id) < 0) pintar(it);
        });
      }).catch(function () {});
  }

  mirar();
  setInterval(mirar, 30000);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) mirar(); });
  window.addEventListener("online", mirar);
})();
