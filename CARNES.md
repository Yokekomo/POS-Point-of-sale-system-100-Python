# Control de carnes

Edición solo carne, para restaurantes y hoteles que lo que necesitan controlar
es la carne. Misma casa que la plataforma de cocina y **el mismo motor
probado** —despiece, reparto de coste, FEFO, descongelado, inventario,
trazabilidad y merma—, pero con una sola puerta.

No hay recetas de cocina, ni plantillas HACCP generales, ni escandallos de
platos que no llevan carne. Lo que hay es el recorrido entero de una pieza:

```
recepción → despiece → cámara → descongelado → venta → inventario → historia
```

Corre por su cuenta, con su propia base de datos y su propio acceso:

```bash
python -m thegrill.cli --db sqlite:///carnes.db serve-carne --port 8001
```

## Cómo se entra: la cuenta

Nadie se registra solo. La cadena es corta y a propósito:

1. Una casa entra en la web pública, lee **cómo funciona** y **los precios**, y
   deja una **solicitud** con quién es: nombre del restaurante, nombre y número
   fiscal, dirección, país, persona a cargo y su cargo, correo, teléfono,
   cuántos cocineros y cuántos locales. La solicitud **se guarda siempre** y,
   si el correo está configurado, sale un aviso a la plataforma. El registro
   manda; el correo solo avisa.
2. La plataforma la ve en su consola y **da de alta la casa** creando de paso
   la cuenta de su manager.
3. Se pone el **método de pago** en la pasarela y arrancan los **quince días de
   prueba**, con su reloj. Sin método de pago no empieza la prueba.
4. El **manager crea las cuentas de su gente**: carniceros y ayudantes, cada uno
   con su nivel y su contraseña. Managers, no: eso es de la plataforma.
5. Si se **cancela antes de que termine la prueba, no se paga nada**, y así lo
   dice la pantalla y lo deja escrito el registro de auditoría.
6. Si el recibo del mes falla, la plataforma lo marca. Primero avisa y se sigue
   trabajando; si se bloquea, **se para la casa entera**.

**El reloj de la prueba lo ven el manager y la plataforma, y nadie más.** La
cocina no tiene por qué enterarse de cómo va el recibo.

**Cuando la cuenta está parada**, cada quien ve lo suyo: el manager, que el pago
del mes no ha entrado y la nota de por qué; la cocina, que hable con su manager.
El motivo del bloqueo no es cosa del carnicero.

## Los pagos, fuera de aquí

Esta plataforma **no pide, no guarda y no ve números de tarjeta**. El método de
pago se da de alta en una pasarela certificada —Stripe, Adyen o la que se
elija—, y aquí solo se apunta lo que esa pasarela devuelve: su referencia, la
marca, los cuatro últimos dígitos y la caducidad.

No es una promesa: el formulario público y la consola **rechazan** cualquier
campo que contenga algo con forma de número de tarjeta, comprobado con Luhn,
antes de escribir nada. Lo que no entra no se puede filtrar.

Queda pendiente, y hay que decirlo: **falta conectar la pasarela de verdad**.
Hoy la referencia se apunta a mano después de darla de alta en el panel del
proveedor. El cobro recurrente automático y el aviso de recibo devuelto los da
la pasarela por webhook, y ese enganche está por hacer.

## Datos personales y reglamento europeo

Una solicitud de acceso trae nombre, correo, teléfono, dirección y número fiscal
de una persona identificable. En la Unión Europea eso no es un formulario
cualquiera, así que el programa trae las reglas puestas:

- **Se guarda lo justo**: lo que hace falta para dar de alta y facturar. Ni
  tarjetas, ni nada que no se use.
- **No se guarda para siempre**: una solicitud que no llegó a cuenta se borra a
  los 180 días, con su botón en la consola y su comando para dejarlo en un cron
  (`purgar-solicitudes`). Las aceptadas no se tocan ahí: esas ya son una casa.
- **Se puede entregar y se puede borrar**: cada solicitud tiene su descarga de
  datos —derecho de portabilidad— y su botón de borrar —derecho de supresión—, y
  el borrado deja constancia de que se borró.
- **Queda escrito quién mira**: abrir la bandeja de solicitudes deja huella en el
  registro de auditoría, igual que exportarlas o borrarlas.
- **El formulario dice lo que hay que decir**: quién es el responsable, para qué
  se usan los datos, cuánto se guardan y cómo pedir verlos, corregirlos o
  borrarlos.
- **Cifrado de los datos de contacto**: si se define `GRILL_DATA_KEY` y está
  instalada la biblioteca `cryptography`, nombre, correo, teléfono, dirección y
  número fiscal se guardan cifrados. Si no, se guardan en claro **y la consola
  lo dice en rojo**: es mejor saberlo que creerse protegido.

Lo que sigue siendo tuyo y no lo arregla el código: el registro de actividades
de tratamiento, el contrato con cada encargado (el proveedor de hosting, el de
correo, la pasarela de pago), el procedimiento de brecha —72 horas—, y cifrar el
disco o el motor de base de datos, que es donde el cifrado en reposo tiene su
sitio de verdad. Esto no es asesoramiento legal.

## Cookies

Dos, y ninguna sirve para seguir a nadie:

| Cookie | Para qué | Cuánto |
|---|---|---|
| `grill_session` | mantener la sesión abierta | catorce días o hasta salir |
| `grill_lang` | recordar el idioma, y solo cuando lo eliges tú | un año |

Ni analítica, ni publicidad, ni nada de terceros: **la web no carga ni una
tipografía de fuera**, que es lo que permite que la política de contenido sea
estricta de verdad.

El reglamento **no pide consentimiento para las cookies estrictamente
necesarias**, y estas dos lo son, así que no hay ventana de consentimiento: hay
un aviso que informa y una página `/cookies` que las lista con su plazo. El
aviso sale en la web de venta y en la pantalla de acceso —donde llega quien
todavía no nos conoce— y no dentro del programa.

**Ojo con esto**: el día que se añada analítica, un píxel o cualquier cosa de un
tercero a la web de venta, ese aviso deja de valer y hace falta un
consentimiento de verdad —rechazar por defecto y no escribir nada antes de que
lo acepten—. Hoy no hace falta porque no hay nada que consentir.

## Repaso contra los ataques conocidos

Lo de abajo es una pasada contra el **OWASP Top 10 de 2025** —que subió la mala
configuración al número dos, metió la cadena de suministro y añadió el mal
manejo de errores— y contra lo que traen los informes de 2026: bots que imitan
a personas, relleno de credenciales y abuso de API. Lo que estaba mal, se
arregló; lo que falta, está dicho.

| Ataque | Qué hay puesto |
|---|---|
| **Control de acceso roto** (A01) | Cuatro niveles comprobados en la ruta, no en la plantilla, y un barrido que recorre todas las rutas y exige sesión. Cada objeto se busca filtrando por el restaurante de quien pregunta: cambiar un número en la dirección no abre lo de otro. |
| **Mala configuración** (A02) | `/docs` y `/openapi.json` cerrados; cabeceras en todas las respuestas; cookie `httponly`, `secure` y `samesite=strict`; sin documentación de API publicada. |
| **Cadena de suministro** (A03) | Seis dependencias, todas conocidas, y la web no carga **nada** de fuera: ni una tipografía. Falta fijar versiones exactas con un fichero de bloqueo. |
| **Inyección** (SQL, comandos) | Todo va por el ORM con parámetros; no hay SQL construido con texto del usuario ni se ejecutan comandos. |
| **XSS** | Las plantillas escapan por defecto y no hay un solo `\|safe`. La política de contenido marca nuestros scripts con un número distinto en cada respuesta, así que un script colado en la página no se ejecuta. |
| **CSRF** | Un token por sesión en cada formulario, y la cookie con `samesite=strict` no viaja en peticiones que vengan de fuera. |
| **Clickjacking** | `X-Frame-Options: DENY` y `frame-ancestors 'none'`. |
| **Relleno de credenciales y fuerza bruta** | Ocho fallos por correo y dirección, y a esperar cinco minutos. Los fallos quedan en el registro. |
| **Enumeración de usuarios** | El error de acceso es el mismo exista el correo o no, **y tarda lo mismo**: si no existe se gasta igualmente una comprobación de contraseña, porque si no el reloj delata quién tiene cuenta. |
| **Redirección abierta** | El cambio de idioma solo acepta destinos que empiecen por una barra y no por dos. |
| **Datos de tarjeta** | No entran: el formulario y la consola rechazan cualquier cosa con forma de número de tarjeta, comprobada con Luhn. |
| **Fallos criptográficos** | Contraseñas con PBKDF2-SHA256 y 240.000 vueltas; datos de contacto cifrados si hay clave; HSTS cuando se sirve por HTTPS. |
| **Registro y alerta** (A08) | Auditoría de todo lo que toca una cuenta o los datos personales, y aviso de cada acceso fallido. |
| **Mal manejo de errores** (A10) | Toda operación va en una transacción que se deshace entera si algo falla, y las pantallas de error no enseñan trazas. |

**Lo que falta, y conviene saberlo:** no hay segundo factor; no hay recuperación
de contraseña; los frenos viven en memoria del proceso, así que detrás de un
balanceador hacen falta en un sitio compartido —o un WAF delante—; no hay
protección de bots más allá del freno, que contra un bot que imita a una persona
se queda corto; y las dependencias no están fijadas a una versión exacta.

## Lo que protege la puerta

- **Cabeceras** en todas las respuestas: la web no se deja embeber, el navegador
  no adivina tipos de contenido, la dirección no se filtra a terceros y no se
  carga nada de fuera. La política de contenido puede ser estricta de verdad
  porque esta web no carga ni tipografías externas.
- **Freno al probar contraseñas**: ocho fallos seguidos por correo y dirección, y
  a esperar. Sin freno, una contraseña corta se adivina en una tarde.
- **Freno al formulario público**: es la única puerta abierta a internet.
- **Contraseñas** con PBKDF2-SHA256 y 240.000 vueltas, con su sal por usuario.
- **Sesiones** en cookie httponly y `secure` salvo en desarrollo, con su token
  CSRF en cada formulario.
- **Aislamiento entre casas**, probado: ninguna ve los datos de otra.
- **Registro de auditoría** de todo lo que toca una cuenta: quién, qué y por qué.
- **Sin documentación de API publicada**: `/docs` y `/openapi.json` están
  cerrados, que enseñaban el mapa entero de la aplicación a cualquiera.
- **Nada se abre sin haber entrado**: hay un barrido que recorre todas las rutas
  y comprueba que la que no es pública manda a la pantalla de acceso.

Los frenos viven en memoria del proceso: con varios procesos detrás de un
balanceador, el freno es por proceso. Conviene saberlo antes que creerse
protegido.

## Tres niveles de acceso

| | Plataforma | Manager | Carnicero | Ayudante |
|---|---|---|---|---|
| Dar de alta casas, cobrar y bloquear cuentas | ✓ | — | — | — |
| Recibir primales · despiezar | ✓ | ✓ | ✓ | — |
| Descongelar · contar · apuntar merma | ✓ | ✓ | ✓ | ✓ |
| Abrir y cerrar inventarios · cerrar turno | ✓ | ✓ | ✓ | — |
| Ver cámara, cortes y la historia de una pieza | ✓ | ✓ | ✓ | ✓ |
| Ver el dinero: costes, food cost, valor de la cámara | ✓ | ✓ | — | — |
| Carta, ingredientes y ventas | ✓ | ✓ | — | — |
| Catálogo de cortes, recuperar piezas, equipo | ✓ | ✓ | — | — |

El carnicero ve **lo que queda de primales y los cortes de cada pieza**, en
kilos y en piezas. El dinero no: ni el coste del primal, ni el precio por kilo,
ni el valor de la cámara, ni el food cost de la trazabilidad.

Lo que no se puede tocar tampoco se enseña —quien no puede abrir una pantalla no
la ve en la barra—, y **la puerta se cierra en la ruta, no solo en la
plantilla**: una barra sin enlace no es una puerta cerrada, basta escribir la
dirección a mano. Los tres niveles son una escalera: todo lo que puede el
ayudante lo puede el carnicero, y el manager lo puede todo.

Una excepción por comodidad, dicha para que se sepa: el carnicero **sí escribe
el precio del albarán** al recibir primales, porque lo tiene delante y sin coste
no se puede despiezar. Lo que no hace es verlo después en ninguna pantalla.

## Las pantallas

**Hoy.** Lo que está pendiente y lo que queda: piezas descongelando sin
recuento, el inventario del mes sin hacer, cortes bajo mínimo, lotes que
caducan y despieces empezados sin volcar. Debajo, primales, kilos en cámara y
el valor de lo que hay.

**Recepción.** Los primales llegan en grupo, bajo un lote de recepción común.
Cada pieza recibe su número y su coste, y el precio puede ser distinto por
pieza. Dos piezas no pueden compartir número: o entra el lote entero, o no
entra nada.

**Despiece.** Se marcan las piezas que entran, se escriben hasta diez cortes
con sus piezas y sus gramos, y se vuelca a cámara. Cada corte sale con **serial
propio** —`8017-01`, `8017-02`— y se lleva su parte del coste del primal
repartida por el índice de valor: un filete vale más por kilo que un recorte.
La merma no se lleva nada, así que la pagan los cortes. Si no cuadra, no se
vuelca y no queda un despiece a medias.

**Cámara.** Cortes y primales que quedan, con su mínimo, sus seriales abiertos,
lo que hay descongelando y los días que faltan para caducar. El cierre del día
deja los avisos de lo que baja del mínimo.

**Maduración y congelador.** Una pieza entera puede estar en tres sitios y en
cada uno le pasa algo distinto. En cámara es la que llegó. Congelada, el reloj
se para y manda la fecha de consumo del congelador, no la de la etiqueta. Y
madurando pierde agua todos los días.

Ahí está lo que casi nadie apunta: **los kilos se van y el dinero no**. Una
pieza de 9 kg comprada a 30 € el kilo son 270 €; si a los cuarenta y cinco días
pesa 7,6, esos 270 € siguen enteros y el kilo ha pasado a valer 35,53 €. Quien
siga cobrando como si costara 30 está regalando la maduración. Por eso cada
pesada deja escrito el peso de antes, el de ahora, lo perdido y a cómo sale el
kilo, y el inventario del mes cuenta también como pesada: la merma de
maduración se apunta como agua evaporada, no como carne que falte, y el aviso
de carne que falta se queda solo con lo que de verdad falta.

Si la pieza pierde más de lo que una maduración explica —más de un 10 % de una
pesada a la siguiente, o más de un 20 % desde que entró— salta el aviso; a
partir del 30 % es crítico, porque eso ya no es maduración.

**Se limpia dos veces, y no son lo mismo.** Antes de madurar se le quita lo
que sobra —grasa suelta, telillas— para que entre limpia. Y cuando lleva
semanas hay que quitarle la costra seca, que es mucha más cuanto más tiempo
lleva. Las dos se apuntan en la pantalla de maduración y las dos suben el
precio del kilo que queda, porque el dinero de la pieza no se va con el
recorte.

**De una limpieza salen siempre las dos cosas**: lo que se tira —costra,
telilla, grasa sucia— y lo que se aprovecha —recortes para picada, grasa para
fondo—. Por eso se dicen los kilos que se han quitado y, de esos, cuáles
entran en cámara y a qué artículo, hasta tres destinos. Lo que no se reparte
es lo que se tira, y si los números no cuadran no se apunta nada: lo quitado
es lo guardado más lo tirado.

Lo aprovechado sale con su propio lote colgando de la pieza —`8017-90`— y se
lleva solo lo que vale, con su índice: 0,25 para un recorte, 0,10 para la
grasa. Lo tirado no se lleva nada, así que su coste se queda en los kilos que
quedan, igual que la merma de cámara de toda la vida.

Lo importante es que la limpieza **no se cuenta como agua**. Una pieza que
pierde 1,4 kg evaporando y 1,2 de costra tiene dos columnas distintas, porque
juntas cuentan la misma historia mal: el agua es inevitable y la costra
depende de cuántos días la tengas.

Y al lado, el **rendimiento**: lo que queda de lo que entró, contando lo
vendido. De 9,4 kg que entraron, 6,3 vendibles es un 67 %. Ese es el número
que dice si compensa madurar cuarenta y cinco días o sesenta.

**La madurada también pasa por despiece.** La limpieza grande puede hacerse
ahí, que es un despiece como cualquier otro, y la pagan los kilos que quedan
igual que la merma de siempre. Una pieza de 9 kg a 30 € que madura hasta 7,6 y
deja 6,4 limpios sale a 42,19 € el kilo: los 270 € no se han movido, los kilos
sí.

Al limpiarla se elige qué sale, y pueden salir las dos cosas del mismo
despiece:

- **para vender a gramos**: el corte sale entero y se marca «a peso». Entra en
  cámara en kilos, sin piezas ni gramos por pieza, porque la ración la decide
  el cuchillo delante del cliente;
- **en piezas ya cortadas**: raciones con su peso, como el resto de la carta.

**Y todo pasa por el POS**, de una de las dos maneras, la que venga mejor ese
día. Se carga el fichero que exporta la caja —CSV o Excel, como salga—: la
pantalla lo lee, dice qué ha entendido de cada columna, con qué plato ha
emparejado cada artículo y cuáles no conoce, y solo descuenta cuando se
confirma; leer no toca el almacén. O se escriben las unidades a mano, plato a
plato, que es lo que se hace cuando la caja no exporta nada, cuando el fichero
sale mal o cuando solo hay que apuntar dos líneas. Las dos están siempre a la
vista en la misma pantalla y las dos descuentan igual.

Un plato «a peso» se cobra por kilo y el parte de ventas trae además los
gramos de cada venta: eso es lo que descuenta de cámara
y con lo que sale el coste de ese corte concreto, al precio del kilo de hoy. Si
el parte llega sin el peso, se descuenta la ración de referencia y la pantalla
lo dice, porque esa ración no es lo que se cortó. La congelada va por el camino
de siempre: porciones —que nacen congeladas, con la fecha del congelador—
vendidas por pieza con su escandallo.

**Descongelado.** Lo que sale a descongelar, lo que el POS ha vendido y lo que
queda al cerrar el turno. Las tres cosas juntas son el cuadre del día:

- **lo vendido se descuenta de lo descongelado**, así el recuento de cierre se
  hace contra un número —«salieron 8, se vendieron 6, deberían quedar 2»— y no
  contra el aire. Si al contar sale otro número, la diferencia no la explica el
  POS y salta el aviso ese mismo día;
- **el peso real por pieza** son los kilos que faltan entre las piezas vendidas,
  no entre las que faltan de la cámara: lo que interesa es cuánto pesa lo que
  sale a la mesa. Salieron 2,81 kg en 8 piezas, quedan 0,70 en 2 y el POS vendió
  6: cada entrecot pesó 352 g;
- **lo que se pierde en el día**, en dinero. La carta dice 6 × 330 g = 1,98 kg y
  la balanza dice 2,11: 130 g de más a 43 € el kilo son 5,59 € que se han ido
  hoy por cortar ancho. En negativo también avisa: o se corta corto, o falta un
  apunte.

Esa diferencia no se queda en un número de pantalla: al cerrar, lo vendido sale
como venta al peso de la carta y **la diferencia se apunta como merma de
descongelado**, con su coste y su lote, porque la carne pierde agua al
descongelar y el corte nunca sale exacto. Si se pasa de lo razonable —más de un
10 % contra la carta— salta la alerta además de apuntarse. Los dos movimientos
suman exactamente lo que falta de la cámara: ni un gramo se queda sin explicar,
y la merma sale en el libro de mermas con todas las demás.

Sin recuento no hay cierre.

**Cortes.** El catálogo. Un corte es lo que se cuenta, se vende y se descuenta;
debajo cuelgan los artículos de cada procedencia, que se gastan en una sola cola
por FEFO o FIFO. Cambiar de proveedor es añadir un artículo, no tocar la carta.

**Carta de carnes.** Un plato es un corte y unos gramos, atado a su producto del
POS por código o por nombre. Por dentro es una receta, así que el food cost y el
descuento de cámara salen del mismo motor de siempre, sin una segunda manera de
calcular lo mismo.

**Emplatado.** Un entrecot no llega solo a la mesa. Cada plato abre su ficha con
todo lo que lleva —la carne primero, y detrás la guarnición, la salsa, el pan—,
con el coste de cada línea, su peso en el plato y el food cost del conjunto. Un
plato que solo lleva carne lo dice: su food cost está incompleto.

**Otros ingredientes.** El apartado donde se configura lo que cuesta cada cosa
que no es carne: el precio por kilo, por litro o por unidad, la porción que va
en el plato —en gramos, que es como se habla en cocina— y a cuánto sale esa
ración. Patata a 1,20 € el kilo, 200 g por plato, 0,24 € la ración. De esto no se lleva stock
—aquí no se cuentan patatas—, así que la venta no intenta descontarlo del
almacén ni salta un aviso falso de falta de stock; lo que sí cuenta es su
precio, porque sin él el food cost del plato se queda corto. Cambiar un coste
mueve todos los platos que lo llevan, de una vez.

**Merma de limpieza.** Cada línea del plato puede decir cuánto se pierde al
limpiar: 200 g de patata en el plato salen de 250 en el saco si se pela un 20 %.
Del almacén sale el bruto, y el plato paga el bruto.

En todas estas pantallas se escribe y se lee en **gramos**, mililitros o
unidades; el precio se mira **por kilo**, y al lado sale lo que cuesta esa
porción. Nadie en una cocina dice «0,2 kg de patata».

**Ventas.** Lo vendido descuenta de cámara por rotación, plato a plato, dejando
escrito a qué plato fue cada salida: sin eso no hay food cost por pieza.

**Inventario, merma y trazabilidad.** Los mismos de la plataforma de cocina:
inventario mensual que re-ancla el stock, merma que la paga lo que queda del
lote, e historia de una pieza de la recepción al plato.

La pantalla de merma lo enseña **todo junto**: la pieza ya cortada que se echa
a perder y lo que se tira al limpiar una pieza entera. Son la misma cosa
—carne comprada que no se va a vender— y a fin de mes lo que cuenta es el
total, con sus kilos y su dinero, y el desglose de cuánto vino de cada sitio.
Quién lo tiró también queda escrito. El dinero solo lo ve el manager: el
carnicero y el ayudante ven los kilos.

**Descargas.** Cinco hojas de Excel listas para imprimir —recepción, despiece,
descongelado, inventario y merma—, en A4 y en el idioma de quien las baja, para
colgar al lado de la balanza.

## Lo que comparte y lo que no

| | Cocina | Carne |
|---|---|---|
| Motor de despiece, coste, FEFO, descongelado, inventario, trazabilidad, merma | ✓ | ✓ (el mismo código) |
| Acceso, roles, avisos, siete idiomas | ✓ | ✓ |
| Recetas, subrecetas y escandallos de cocina | ✓ | — |
| Emplatado con guarnición y food cost del plato entero | ✓ | ✓ (con su propia pantalla) |
| Plantillas HACCP y registros generales | ✓ | — |
| Recepción de primales y despiece con pantalla propia | — | ✓ |
| Maduración, congelador y venta a peso | ✓ (el mismo motor) | ✓ (con su propia pantalla) |
| Descongelado con pantalla propia | — | ✓ |
| Carta: un plato es un corte y unos gramos | — | ✓ |

Las dos ediciones no comparten base de datos: son dos programas distintos que
se apoyan en el mismo código. Una cocina que quiera las dos cosas usa la
plataforma completa; una que solo maneje carne, esta.

## Idiomas

Siete: español, inglés, francés, alemán, neerlandés, árabe —este de derecha a
izquierda— y húngaro, elegibles en la pantalla de acceso y en configuración.
Los textos propios de esta edición viven en `thegrill/meat/i18n_meat.py` y un
test comprueba que ningún idioma se deje claves sin traducir.

## Estructura

```
thegrill/meat/
  app.py           Las rutas: acceso, hoy, recepción, despiece, cámara,
                   descongelado, cortes, carta, emplatado, ingredientes,
                   ventas, inventario, merma, trazabilidad, avisos, equipo,
                   configuración y descargas
  service.py       Lo que esta edición hace y la cocina no tenía pantalla:
                   alta de primales, despiece desde formulario, catálogo de
                   cortes, ingredientes con su coste, carta y emplatado, y el
                   resumen de hoy
  sheets_meat.py   Las cinco hojas imprimibles
  perms.py         Quién puede hacer qué: cuatro niveles y el dinero aparte
  billing.py       La cuenta: solicitudes, altas, la prueba y el recibo del mes
  mailer.py        El aviso por correo de cada solicitud
  security.py      Cabeceras, freno a las contraseñas y freno al formulario
  i18n_meat.py     389 textos propios × siete idiomas
  templates/       Las pantallas propias; lo demás se hereda de la cocina
  static/fotos/    Las tres fotos de la portada, si se ponen (LEEME.md dentro)
```

La portada va de dinero: lo que se escapa hoy, lo que eso hace al año y las
seis cosas que se saben de cada euro. Admite tres fotos —la pieza entera, los
cortes y el plato— que se dejan caer en `thegrill/meat/static/fotos` con esos
nombres; las que no estén, no dejan hueco. La política de contenido solo
permite imágenes del propio sitio, así que tienen que estar ahí.

A quien llega sin decir nada se le habla en inglés, o en el idioma que pida su
navegador si lo sabemos hablar. El español queda de catálogo de referencia por
dentro.

Las plantillas se buscan primero en `thegrill/meat/templates` y luego en
`thegrill/web/templates`: lo que es igual no se copia, y lo que cambia se
sobrescribe. Por eso inventario, merma y trazabilidad son literalmente la misma
pantalla en las dos ediciones.

## Uso

```bash
pip install -r requirements.txt
python -m pytest -q
python -m thegrill.cli --db sqlite:///carnes.db crear-dueno \
    --email tu@correo.com --nombre "Tu nombre" --password "una-clave-larga"
python -m thegrill.cli --db sqlite:///carnes.db serve-carne --host 0.0.0.0 --port 8001
```

El primer comando da de alta al dueño de la plataforma, y solo funciona una vez.
Con esa cuenta se entra en `/admin`, que es donde llegan las solicitudes y desde
donde se dan de alta las casas.

Para que salga el aviso por correo de cada solicitud:

```bash
export GRILL_SMTP_HOST=smtp.tu-proveedor.com
export GRILL_SMTP_USER=avisos@tu-dominio.com
export GRILL_SMTP_PASSWORD=...
export GRILL_MAIL_FROM=avisos@tu-dominio.com
export GRILL_MAIL_TO=tu@correo.com
```

Sin esas variables la plataforma funciona igual: las solicitudes se guardan y se
leen en la consola, solo que no sale el aviso.

En producción hay que servir por HTTPS, porque la cookie de sesión se marca como
segura salvo que se defina `GRILL_INSECURE_COOKIE=1`, que es solo para
desarrollo.

## Dos cosas que conviene entender

**De dónde sale el consumo de cada corte.** Cada corte elige si se descuenta al
vender en el POS o al cerrar el turno con el recuento de descongelado. Las dos
cosas a la vez descontarían el doble, y el programa no lo deja: si un corte que
se descuenta al vender aparece en un recuento, el cierre no lo vuelve a
descontar y lo dice. Un entrecot que se pesa al descongelar va por conteo; un
corte que sale directo de cámara, por venta.

**Qué se controla y qué solo se cuesta.** De la carne se lleva stock pieza a
pieza, con su serial. De la guarnición solo se lleva el coste. Es a propósito:
un hotel no quiere contar patatas en este programa, pero sí quiere que el food
cost del plato sea el de verdad.

## Ponerlo en internet

Hay `Dockerfile`, `docker-compose.yml` y `Caddyfile` listos: con una máquina
pequeña y un dominio, la plataforma queda sirviendo con su certificado en veinte
minutos. El detalle —qué hosting hace falta, qué significa «a nivel mundial» y
qué dejar montado antes de abrir— está en **[DESPLIEGUE.md](DESPLIEGUE.md)**.

## Por dónde seguir

1. Varias cámaras o varios puntos de venta dentro del mismo hotel, para saber
   qué carne está en cada sitio.
2. Cambiar la contraseña: hoy nadie puede, ni uno la suya ni el manager la de
   su gente.
3. Parte de carne diario en PDF, para el pase.
4. Guardar el cuadre de cada turno, para ver la pérdida acumulada del mes sin
   tener que ir aviso por aviso.
