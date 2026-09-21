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
la pasarela por webhook, y ese enganche ya está: la pasarela llama a
`/pasarela/stripe` con el evento firmado, se comprueba la firma —sin firma
buena no se toca nada, que esa dirección es pública—, se mira que no sea un
evento repetido y se aplica: un recibo cobrado deja la cuenta al día hasta el
final del periodo, uno fallado la pone en aviso, una suscripción cancelada la
cierra, y la tarjeta puesta arranca la prueba. El manager se entera por un
aviso, porque le cambia el día.

El secreto va en `STRIPE_WEBHOOK_SECRET`; sin él, esa dirección contesta que no
está puesta en marcha en vez de fingir que sí. Y el número de la tarjeta sigue
sin pasar por aquí: de la pasarela solo se guarda su referencia y los cuatro
últimos dígitos.

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

## Verificación en dos pasos

Quien puede bloquear una casa entera o ver el dinero de todas no debería entrar
solo con una contraseña: una contraseña se apunta en un papel, se repite en
otra web y se la lleva quien mire por encima del hombro. Por eso cualquiera
puede activar en su configuración los seis dígitos que cambian cada medio
minuto —el TOTP de siempre, el que leen Google Authenticator, Aegis o
1Password—, y desde ese momento la contraseña solo abre la puerta de los
dígitos: la sesión existe pero no sirve para nada más hasta que se teclean.

Al activarla salen seis **códigos de repuesto**, de un solo uso, que se enseñan
una vez. Son los que dejan entrar el día que el teléfono se pierde. Y si
también se pierden, el manager puede quitarle los dos pasos a su gente y la
plataforma a un manager: nadie se queda fuera para siempre.

Los seis dígitos se prueban muy deprisa, así que tienen el mismo freno que las
contraseñas: cinco intentos y a esperar.

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

**Recepción, una pieza cada vez.** Así se descarga de verdad: se coge una bolsa,
se le hace la foto a su etiqueta, se apuntan su número y sus kilos, se guarda, y
se coge la siguiente. La pantalla va en ese orden —**1. la etiqueta, 2. la
pieza**— y no al revés, porque la foto se hace cuando la etiqueta está delante,
no cuando ya has dejado la bolsa en la cámara.

La foto se ve en la propia pantalla antes de guardar, para saber que ha salido
legible, y **viaja con la pieza en el mismo envío**: no hay un segundo paso que
se olvide.

**Lo del camión se escribe una vez.** El lote, el corte, la cámara, la calidad,
la procedencia, el consumo preferente y la etiqueta entera del proveedor se
quedan puestos para las siguientes piezas de la misma descarga: veinte piezas de
la misma caja no son veinte veces el matadero. Va plegado, debajo del botón,
porque se toca al empezar y ya no se vuelve a mirar.

**Y el número y el peso van escritos en la carne.** Con rotulador permanente o
en una etiqueta pegada, antes de meter la pieza en la cámara. La pantalla lo dice al
lado del número y lo repite al guardar, con el número delante, que es el momento
de coger el rotulador —la pieza sigue en la mano—. Sin eso la trazabilidad vive
solo en el ordenador: en la cámara nadie abre el móvil para saber qué bolsa
tiene cogida, y se acaba buscando a ojo.

**Cómo bajó del camión, que no es dónde está ahora.** Refrigerada o congelada,
a cuántos grados marcaba el termómetro al abrir la caja, y —si llega fresca— si
va derecha al arcón sin pasar por la cámara. Las tres cosas quedan escritas en
la pieza y salen en su ficha.

No es lo mismo llegar congelada que llegar fresca y acabar en el arcón el mismo
día: las dos están en el congelador, pero la primera nunca estuvo fresca en esta
casa. Y una pieza que llega congelada, o que se congela al entrar, está en el
arcón **desde el primer día**; contarla como fresca le pondría el reloj que no
es. La temperatura no la juzga nadie: se apunta, y es lo primero que se pregunta
el día que una pieza sale mal.

Dos piezas no pueden compartir número: o entra la pieza entera, o no entra nada.

**Y con la etiqueta del proveedor delante.** El recorrido de la carne no empieza
en el muelle: empieza en el matadero. Así que se copia lo que viene escrito en
la etiqueta —matadero o productor, número de registro sanitario, raza, país,
calidad, fecha de sacrificio, fecha de envasado, el nombre que le da el
proveedor y si es halal— y **de cada pieza el suyo**, porque dos bolsas de la
misma caja no son la misma carne: pueden traer distinto número de canal,
distinta fecha de sacrificio y distinta calificación. Lo que es igual para todo
el camión se escribe una vez arriba y se copia a cada ficha; lo que cambia, en
la ficha que cambie.

Las fechas se comprueban: una carne sacrificada después de llegar, o envasada
antes de sacrificarla, es un dedo en el teclado. Y un dato de etiqueta mal
tecleado es peor que no tenerlo, porque se guarda, no lo vuelve a mirar nadie, y
el día que hay que contestar de dónde salió la pieza se contesta mal.

**La foto de la etiqueta es el respaldo de lo tecleado**: el día que un número no
cuadre, la etiqueta está. No se sirve desde `/static` —cada una pasa por una
ruta que primero mira de qué casa es quien la pide— y repetirla borra la
anterior, que si no el disco se llena de fotos que no mira nadie.

**Sin cobertura, la foto no viaja.** En la cola del teléfono caben textos, no
ficheros de tres megas: meter ahí una foto por pieza llena el móvil y no se
manda nunca. Así que sin señal se guarda lo escrito —que es lo que no se puede
perder— y la pantalla lo dice con esas palabras; la foto se hace luego, desde
«últimas recepciones», donde cada pieza sin foto lleva su botón.

**El precio no lo pone el muelle.** Quien descarga apunta lo que llega; el
dinero es de dirección y además llega después, en la factura. Así que al
carnicero no se le enseña la casilla del precio, y si la escribe a mano en el
formulario no entra. La pieza queda **esperando precio**: está en la cámara, se
ve en la cámara, y **no se puede despiezar**, porque el despiece reparte el
coste del primal entre los cortes y repartir cero es perder el rastro del dinero
sin que salte nada. Se para en el motor y no solo en la lista de la pantalla,
que un formulario escrito a mano se salta la lista.

A dirección le salta el aviso en el contador de la cabecera —«3 piezas esperando
precio», con el lote y quién las recibió— y en **Precios pendientes** las activa:
el mismo precio para todas, que es lo que trae un albarán, y el suyo a la que
sea distinta. Cada pieza se enseña ahí con lo que hace falta para ponerle
precio: qué es, cuánto pesa, de qué calidad, de dónde viene y su etiqueta
entera. Si quien recibe es el manager, lo pone de una vez y se ahorra el
segundo paso.

**Despiece.** Se marcan las piezas que entran, se escriben los cortes que salen
—hasta diez— y se vuelca a cámara.

**Un bloque por corte, y solo los huecos que valen.** La hoja abría con diez
filas por ocho columnas, y de los cuatro huecos de cada corte la mitad sobraban
según cómo saliera ese corte: «a peso» era una casilla en medio de la fila con
dos columnas a cada lado y nadie sabía cuáles tocaban. Ahora se elige **cómo
sale** —en raciones, o a peso— y se enseña solo la pareja que corresponde:
piezas y gramos, o kilos. Lo que casi nunca se toca —el índice de valor y si es
para reusar— se abre debajo. Tres bloques a la vista y los demás se piden. Cada corte sale con **serial
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

**Y se pesa todos los días.** Una pieza madurando no está congelada: está en
una cámara a dos grados, fresca y abierta, perdiendo agua cada día. Así que
entra en el conteo diario igual que lo descongelado, con su casilla en la
pantalla de maduración: se pesan todas de una vez y sale lo que se ha ido hoy,
en kilos y en dinero. Lo que no se pese hoy aparece mañana en los pendientes
de la pantalla de hoy, porque un conteo a medias no cuadra nada.

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
queda al cerrar el turno. Son **dos pantallas**, porque son dos momentos: sacar
del arcón es de media mañana y el recuento es de madrugada, y los dos
formularios piden lo mismo —número, piezas, kilos— a un palmo el uno del otro.
Juntos, la salida acababa en la casilla del recuento y el turno salía
descuadrado sin que nadie lo viera. Las tres cosas juntas son el cuadre del día:

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

**Lo congelado está en espera.** En esa cola no entra lo que está en el arcón.
Un número congelado es carne que existe y que hoy no se puede servir, así que el
POS no descuenta de él: descontarle una venta sería apuntar que se ha servido un
entrecot que sigue duro, y el día que alguien vaya a buscarlo no está. Si lo
único que hay de un corte está congelado, la venta se queda corta y el aviso lo
dice con esas palabras —hay tantos kilos, pero congelados—, en vez de decir que
falta carne.

Lo que despierta a un número es sacarlo a descongelar, que es lo que pasa de
verdad: alguien abre el arcón. Si sale entero, ese número deja de estar
congelado. Si salen unas piezas, el número se parte y lo que sale nace con su
propio número (`TG-0001·01·D1`), ya descongelado, mientras el resto se queda
dentro esperando su turno. El kilo vale lo mismo dentro y fuera: descongelar no
cuesta dinero. En la cámara los kilos congelados se ven aparte, para que nadie
cuente como disponible lo que está en espera. Y una pieza entera del congelador
tampoco se vende al corte: primero sale del arcón.

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

**La ficha de una pieza, entera.** Al abrir un primal sale, en este orden: **la
foto de su etiqueta** —la que se hizo en el muelle, a tamaño de leerla— con los
datos que se copiaron de ella, lo que dejó la pieza en total, y debajo **el
desglose de todos los cortes que salieron de ella**. El despiece dice el
rendimiento, los kilos que fueron a cortes, **cuántas raciones salieron y a
cuántos gramos de media**, la merma y lo aprovechado. De cada corte: cuántas
piezas salieron y a qué peso medio —con el desvío contra el peso de carta—, lo
vendido, **lo que queda**, lo tirado y lo que se fue a otra sede.

**Y dónde ha ido cada corte**, junto y por plato: «HAMBURGUESA · 0,540 kg ·
49,50 · TARTAR · 0,240 kg · 38,00». Con lo aprovechado es la pregunta de verdad
—el recorte de un lomo caro acaba en la hamburguesa o en el tartar, y saber en
cuál de los dos es lo que dice si ese recorte se está pagando—. Las ventas una
a una, con su día, siguen estando, plegadas debajo: es el detalle, y casi nunca
es lo que se viene a ver.

**Y el dinero solo para dirección**: lo que ingresó cada corte, lo que costó lo
vendido de él, **lo que se ha ganado con ese corte** y su food cost. Por corte y
no solo por pieza, que es donde se ve la verdad: del mismo primal, el filete
deja dinero y el recorte se lo come, y comparándolos solo por food cost no se
sabe cuál de los dos paga la pieza. El carnicero ve los kilos y las piezas; los
euros, no.

**Trazabilidad: la ficha abre por de dónde viene.** Antes de los cortes, las
ventas y el margen, la historia de una pieza enseña su etiqueta: lote del
proveedor, matadero, registro sanitario, país, raza, calidad, sacrificio,
envasado y la foto. Y el buscador encuentra por todo eso —no solo por nuestro
número—, porque quien llama para retirar un lote no sabe cómo lo numeramos
nosotros: sabe su lote, o el matadero, o el número de registro.

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

## Obrador y locales

Un grupo no son tres restaurantes iguales: lo normal es **un obrador** —donde
llegan las piezas, se maduran y se despiezan— y **unos locales** que consumen
de él. El obrador corta; el local sirve.

Por eso la carne no está «en la casa»: está en una sede. Ocho piezas en el
obrador y ninguna en la playa no es lo mismo que cuatro en cada sitio, y hasta
ahora el programa no sabía distinguirlo.

**Sedes** (`/sedes`, del manager) da de alta el obrador y los locales, y dice
quién trabaja en cada uno. Lo que esa persona da de alta entra en su sede: la
carne que recibe el del local entra en el local. Sin sede asignada, trabaja con
la casa entera, que es como funcionaba antes y sigue funcionando: una casa que
nunca ha oído hablar de sedes tiene una sola, la principal, y no hay nada que
configurar.

**Traslados** (`/traslados`) enseña dónde está la carne —piezas, kilos y, si lo
puede ver, el dinero de cada sede— y manda carne de una a otra:

- **Una pieza entera** se va al local con su número. Allí se despieza o se
  sirve, y sigue siendo la misma pieza.
- **Cortes**, el lote entero o unos kilos. Si van unos kilos, el lote se parte:
  lo que sale nace con su propio número colgando del de origen
  (`TG-0001·01·T1`), así que se sigue pudiendo seguir hasta el plato.

Lo que viaja se lleva su coste: una pieza que sale del obrador vale en el local
exactamente lo mismo. Ni se abarata por el camino ni se encarece, porque si el
traslado tocara el kilo el food cost del local sería un cuento. Y queda escrito
qué salió, de dónde, adónde, cuánto pesaba y quién lo mandó, que es lo que se
pregunta cuando falta algo.

**El consumo sale de la sede.** Lo que se vende en un local se descuenta de los
números que están en ese local: descontar en la playa un lote que está en el
obrador cuadra el papel y descuadra las dos cámaras —la de allí, con carne que
el programa ya ha dado por servida, y la de aquí, que no tiene la que dice
tener—. Lo mismo con la merma: el que tira carne en el local no está tirando la
del obrador.

Quien tiene sede puesta trabaja con la suya en todas las pantallas: la cámara,
el descongelado y las ventas enseñan lo de su sede y nada más. El manager, que
no tiene sede, sigue viendo la casa entera y elige de qué local es cada parte de
ventas cuando lo sube.

Y cuando falta carne se dice por qué falta, que no es lo mismo en los tres
casos: **está en otra sede** —hace falta un traslado—, **está congelada** —hace
falta sacarla— o **no está** —alguien no registró una entrada—.

**Se madura donde se sirve.** En el obrador hay de todo: piezas frescas
esperando la mesa, piezas congeladas esperando turno y piezas en curación. Y al
local le puede ir de todo también: un primal fresco para cortarlo allí, uno
curado para seguir madurando o venderlo al peso delante del cliente, uno
congelado que espera, y cortes ya hechos. Viaja como está.

Una pieza en curación es carne fresca abierta —no está congelada, está a dos
grados perdiendo agua todos los días—, así que **se cuenta todas las noches**,
igual que lo descongelado, y la cuenta quien la tiene delante: si madura en la
playa, la pesa la playa. El cierre de turno lo dice cuando queda alguna sin
pesar, porque esa agua es la merma de hoy y mañana ya no se sabe de qué día era.

El local también corta. Lo que despieza se queda en su cámara, y una mesa no
mezcla dos cámaras: un despiece con piezas de dos sedes no ha pasado por ninguna
mesa.

**El inventario es de cada cámara.** Cada sede abre el suyo y cuenta lo suyo, y
dos pueden estar contando a la vez. Contar el obrador y el local en la misma
hoja no cuadra nada: nadie pesa dos cámaras que están a veinte kilómetros, y lo
que no se mira sale como faltante. La obligación del mes también es de cada
sede: la playa puede estar al día y el obrador no.

**Los mínimos, también.** La playa en agosto y la sierra en enero no quieren el
mismo mínimo del mismo corte. En **Sedes → Mínimos** cada una pone los suyos, en
kilos para los cortes y en piezas para los primales; lo que se deje en blanco se
rige por el mínimo de la casa, como siempre.

**Y las hojas de papel llevan su nombre.** Las rejillas para imprimir salen con
la sede en la cabecera y en el pie, para que el papel del obrador y el del local
no se confundan al lado de la balanza.

En el recibo el grupo ya existía desde antes: las casas de la misma empresa
comparten grupo, se ven juntas en la pantalla de la plataforma con sus locales
y su cuota total, y se cobran de una vez —una empresa, un recibo, no cinco—.

## El parte del día, el mes y las cámaras

**Parte de carne del día** (`/parte`). Lo que hay, lo que se ha ido hoy y lo que
queda por hacer, en una hoja pensada para el pase: se imprime tal cual —o se
guarda en PDF desde el navegador, que es lo que hay en una cocina— y lleva sus
dos líneas de firma. Se puede pedir el de cualquier día.

**El mes se suma solo.** Cada cierre de turno deja escrito su cuadre —lo
gastado, el desvío contra la carta, el agua del descongelado, los números que
se quedaron sin contar—, así que ya no hay que ir aviso por aviso: la pantalla
de descongelado enseña lo que llevamos del mes, en kilos y en dinero. Un turno
tiene un cuadre: si se vuelve a cerrar, se pisa.

**Varias cámaras en la misma sede.** Una sede grande no tiene una cámara: tiene
la de maduración, la de cortes y el arcón del pasillo. Cada pieza y cada lote
pueden decir en cuál están, con el nombre que se use en la casa; se pone al
recibir y se cambia desde la cámara, los cortes heredan la de su pieza y lo que
sale del arcón se queda donde estaba. En blanco es «sin decir», que es como
estaba antes de esto.

## En el móvil, en la tablet y en el ordenador

El carnicero no lleva un ordenador a la cámara: lleva el móvil en el bolsillo
del delantal y lo toca con el guante puesto. El manager cierra el mes en la
tablet o en el ordenador. Las tres cosas tienen que ir bien, y eso no se
consigue mirando la pantalla grande:

- **Nada se sale de la pantalla.** Ninguna de las pantallas obliga a arrastrar
  la página de lado para leer un número; las tablas se deslizan dentro de su
  ficha y la página se queda quieta.
- **La primera columna se queda fija** al deslizar una tabla: los kilos de la
  derecha no dicen nada si no se ve de qué pieza son.
- **Lo que se toca es grande**: nada de lo que se pulsa baja de 44 píxeles, que
  es lo que pide la norma de accesibilidad para un dedo con prisa.
- **El teclado sale con números** donde se escriben kilos, piezas o precios, y
  los campos van a 16 píxeles para que el iPhone no haga zoom al escribir.
- **El zoom no se prohíbe nunca**: quien no ve de cerca tiene que poder acercar.
- **La muesca y la barra del iPhone** no tapan nada: la pantalla se aparta de
  las zonas seguras.
- **La barra de menú se desliza en una línea** en vez de amontonar quince
  enlaces y comerse media pantalla.
- **El menú del ordenador se pliega por grupos.** Veinte enlaces no caben en la
  pantalla de un portátil, y lo que sobra sale como barra de desplazamiento.
  Se abre el grupo de la pantalla en la que estás y los demás se recogen; el
  que cada uno abre o cierra se queda así en las pantallas siguientes.
- **Sin el retardo del doble toque**, que hace que el programa parezca lento
  cuando no lo es.
- **El tema del aparato llega hasta lo que pinta el navegador.** La barra de
  desplazamiento, los desplegables y el calendario no los dibujamos nosotros:
  los dibuja el navegador, y si no se le dice en qué tema va, los saca en
  blanco. Una raya de tiza al lado de la carne. Y la barra del menú lateral,
  cuando la ventana es baja y el menú no cabe, solo aparece al pasar por
  encima: dos barras juntas no las quiere ver nadie.

Se prueba con un navegador de verdad en tres tamaños —teléfono de pie,
teléfono de lado y tablet—, dando de alta una pieza con el pulgar para ver que
el camino entero funciona, no solo que la pantalla se dibuja. La plataforma de
cocina tiene su propia plantilla y lleva exactamente los mismos arreglos, con
sus propias pruebas: dos ediciones con dos comportamientos sería una trampa
para quien use las dos.

## Probarla

Una demo con un mes de trabajo ya dentro —dos casas, once cuentas, piezas
madurando, traslados, ventas e inventario cerrado— y sin nada que configurar:

    python -m thegrill.cli --db sqlite:///demo.db demo

Al arrancar escribe las claves y las direcciones, y escucha en toda la red de
casa: desde el móvil de la misma wifi se abre con la dirección de ese ordenador
y `:8001` detrás, que es como hay que verlo. Con Docker es
`docker compose -f docker-compose.demo.yml up --build`. El paseo, con qué mirar
primero y con quién entrar, está en **[PRUEBALA.md](PRUEBALA.md)**.

## Cuando no hay cobertura

Dentro de una cámara frigorífica no hay señal. Tampoco en el sótano del
almacén, ni cuando la wifi de la casa va y viene, ni con una barra en el patio.
En un restaurante la señal falla, y **el trabajo no puede depender de eso**.

La regla es que **sin red se trabaja igual**: se escribe, se guarda y se sigue.
Lo que se manda espera en una cola dentro del propio teléfono y sale solo
cuando hay señal, en orden y de uno en uno. Así:

- **Lo que se teclea se guarda en el propio teléfono** según se escribe. Si la
  pantalla se recarga, si el móvil se bloquea o si se va la batería, lo
  escrito sigue ahí al volver.
- **Al darle a guardar sin señal, el trabajo se da por hecho.** La pantalla
  sigue adelante y se puede hacer lo siguiente: contar otra pieza, apuntar otra
  merma, dar de alta otro lote. No se espera a nadie.
- **La cola sale sola y en orden**, de uno en uno. Se intenta al volver la
  señal, al abrir cualquier pantalla y cada poco rato: el aviso de «ya hay red»
  del navegador **no existe en el iPhone**, y esperarlo sería no mandarlo nunca.
- **Se avisa de la señal, arriba y en todas las pantallas.** Cuando se va, el
  aviso se queda puesto y el contenido se aparta para no taparlo: quien está
  contando tiene que enterarse antes de escribir veinte pesos, no al darle a
  guardar. Cuando vuelve, lo dice y se quita solo.
- **Se ve lo que falta por mandar**, con su hora y su estado, en todas las
  pantallas. Un recuento esperando en un teléfono sin que nadie lo sepa es
  peor que no tener cola.
- **Mandar dos veces no apunta dos veces.** Cada envío lleva su número, puesto
  por el teléfono antes del primer intento y el mismo en todos los reintentos.
  Si uno entró y se perdió la respuesta —que es lo que pasa con media raya—,
  el servidor reconoce el número y contesta «ya está hecho» sin tocar los
  kilos. La regla la sujeta la base de datos, así que dos reintentos a la vez
  tampoco pasan los dos.
- **Lo que el programa rechaza no se reintenta a ciegas.** Si la hoja se cerró
  o la pieza ya no está, ese envío se marca y se enseña para que lo mire una
  persona; lo demás sigue saliendo.
- **Todas las pantallas de trabajo se abren sin red**: el programa deja una
  copia de cada una en el aparato al entrar. Si se entra en una que no estaba
  guardada, sale una página que lo explica en vez de la del dinosaurio.
- Lo que se manda **no se guarda nunca**: un envío sin red falla, y lo recoge
  el guardado del formulario. Nada se manda dos veces.
- Y un envío que se queda colgado —media raya de cobertura, que es lo normal en
  una cámara— **no se queda colgado para siempre**: a los quince segundos se
  corta, el recuento vuelve a la cola y se reintenta solo cada poco. Lo que no
  puede pasar es que el aviso diga «mandando» toda la tarde con el recuento
  todavía en el teléfono.
- Al salir —o cuando caduca la sesión— **las copias se borran del aparato**: un
  móvil de cocina lo usan cuatro personas.

El ayudante que guarda las copias solo lo permite el navegador con certificado
o en el propio ordenador. En la demo por wifi, sin certificado, no se instala:
lo que se escribe se sigue guardando igual, pero una pantalla nueva no se abre
sin señal. Con el despliegue de verdad —dominio y certificado— funciona entero.

## Lo que acaba de pasar, para el de al lado

Dos personas trabajan la misma carne desde pantallas distintas. El del muelle
da de alta seis lomos mientras el de la mesa despieza, y el que está contando
no se entera de ninguna de las dos cosas hasta que va a la cámara y se lo
encuentra. En FEFO eso se paga: se saca la pieza vieja porque nadie sabía que
había entrado una nueva, o se cierra un inventario sin lo que entró hace diez
minutos y las diferencias salen del sitio equivocado.

Así que **cada entrada de carne y cada despiece salen arriba**, en la pantalla
de quien esté trabajando, sea la que sea:

> ● **Han entrado 6 × Ribeye AUS MB7**
> 56,1 kg · lote L-260921-1 · Paco

Dos renglones: arriba lo que hay que entender de un vistazo con las manos
ocupadas, y debajo, en pequeño, lo que hace falta para ir a buscarlo —los
kilos, el lote y quién lo metió—.

Cuatro decisiones, y ninguna es de adorno:

- **Con su X.** Se lee, se quita, y no vuelve: ni al recargar, ni al cambiar de
  pantalla, ni al día siguiente. Un aviso que resucita se deja de mirar a los
  diez minutos, y entonces ya no avisa de nada. Lo cerrado se recuerda en el
  propio aparato, así que cerrar funciona también sin cobertura.
- **Lo tuyo no se te avisa.** Quien acaba de recibir ya sabe que ha recibido.
  El aviso es para los demás.
- **Solo lo de tu sede y solo lo de hoy.** Lo que entra en el obrador no le hace
  falta al local de la playa, y una novedad de anoche no es una novedad: es
  historia, y la historia está en su pantalla. Doce horas y se cae.
- **Se guarda el hecho, no la frase.** Cuántas piezas, de qué, cuántos kilos, con
  qué lote y quién. La frase se arma al leerla, en el idioma de quien lee: el
  del muelle escribe en español y el jefe de cocina lo lee en francés.

Comparten columna con el aviso de la señal, uno debajo de otro y la señal
arriba: dos carteles fijos en el mismo sitio se tapan y no se lee ninguno. Y la
columna va **debajo** de la barra del teléfono, nunca encima —tapar el contador
de alertas para avisar de otra cosa es cambiar un aviso por otro—.

Como mucho tres a la vez. Si han pasado cinco cosas, el sitio para verlas no es
un cartel: son la cámara y la pantalla de cada cosa.

## Que no se ponga lenta con los años

Una pantalla que tarda medio segundo no se abre: el cocinero mira el papel de
la cámara y sigue a lo suyo. Y hay una manera muy fácil de llegar a ese medio
segundo sin enterarse —leer toda la historia de la casa para enseñar lo de
hoy—, porque el primer mes va rápido y el problema sale al año, ya trabajando.

Lo que se pide a la base de datos es lo de hoy:

- **La cámara de ahora, no la de siempre.** La pizarra de maduración y el
  conteo del día hablan de las piezas que están colgadas hoy —cincuenta, no
  cinco mil pesadas—, y la última fecha de cada una la saca la propia base de
  datos en vez de recorrerlas en memoria.
- **Una lectura por pantalla, no cuatro.** La pizarra, el conteo, el resumen y
  la portada preguntaban lo mismo cada uno por su cuenta. Ahora se lee una vez
  y se pasa.
- **Las medias las hace la base de datos.** Los tramos de rendimiento —si los
  quince días de más salen a cuenta— se sacaban recorriendo cinco mil pesadas
  para acabar con diez medias; ahora las suma la base y llega una fila por
  pieza. Los números son exactamente los mismos.
- **Y solo se trae lo que se enseña.** El parte del día cargaba la cámara
  entera para ponerle nombre a las cuatro mermas del día.

Con medio año de trabajo dentro, en el ordenador donde se desarrolla:

| Pantalla       | Antes  | Ahora |
|----------------|--------|-------|
| Hoy            | 199 ms | 31 ms |
| Maduración     | 376 ms | 29 ms |
| Parte del día  | 364 ms | 35 ms |
| Cámara         |  39 ms | 21 ms |

Y queda puesto como prueba, que es lo que impide que vuelva: `tests/test_speed.py`
abre cada pantalla, mira lo que tarda y cuántas consultas hace, le mete a la
casa **veinte mil pesadas** de piezas que ya pasaron por ella —tres años de
trabajo— y vuelve a medir. Exige las mismas consultas y nada de tiempos que se
dupliquen. Una pantalla que se lea la historia entera para enseñar lo de hoy
falla ahí, no en la cocina del cliente.

## Dos personas a la vez sobre lo mismo

En una casa nadie trabaja solo. Mientras uno cuenta la cámara del local, otro
despieza en el obrador y un tercero da de alta el camión. Casi siempre tocan
cosas distintas y no pasa nada; lo que hay que resolver es el rato en que tocan
**la misma**, porque ahí es donde los números se rompen sin que nadie se entere.

La regla, la misma en todas las pantallas: **el que escribe comprueba en la
misma orden**. No se pregunta «¿está entera la pieza?» y luego se escribe
«cortada»; se escribe «ponla cortada *si sigue entera*», y de dos que lo
intenten a la vez se lo lleva uno. Al otro se le dice, con nombre y apellidos,
que ha llegado segundo.

Qué pasa en cada caso:

- **Abriendo el inventario entre dos.** Una cámara, una hoja abierta, y lo
  sujeta la base de datos: si dos encargados le dan a la vez, se abre una y el
  otro cuenta en esa. Dos hojas de la misma cámara no cuadran nunca.
- **Contando la misma cámara.** Cada sede cuenta en su hoja y las dos pueden
  contar a la vez. Dentro de una hoja pueden escribir cuatro personas desde
  cuatro móviles, cada una con sus estantes: guardar manda toda la pantalla,
  pero lo que ya estaba escrito **no se pisa con lo mismo** ni cambia de dueño.
  Cada línea guarda **quién la contó y a qué hora**, y se ve en la pantalla.
- **Contando la misma pieza dos personas.** Vale el número del que está delante
  de la pieza ahora —el último—, pero la línea queda marcada **en discusión**
  con el otro número y el nombre de quien lo puso, y al cerrar salta un aviso
  con la lista. Una pieza en discusión no es una pieza contada.
- **Cerrando el inventario entre dos.** Lo cierra uno. El otro recibe «otra
  persona acaba de cerrar este inventario» en vez de escribir un segundo
  ajuste: los kilos quedarían igual, pero el libro se llevaría el dinero de la
  misma merma dos veces y el mes saldría el doble de malo de lo que fue.
- **Despiezando la misma pieza.** De un lomo de nueve kilos no pueden entrar
  dieciocho en cámara. Lo despieza uno; al otro se le dice que mire el despiece
  que ya está hecho. Y si los dos abrieron la hoja a la vez y traen **el mismo
  número de despiece** —el que propone la pantalla—, el segundo se corre solo
  al siguiente libre: nadie pierde una hoja escrita a mano por un número que
  puso la máquina.
- **Recibiendo en dos muelles.** Igual con la numeración de las piezas: si dos
  recepciones piden el mismo número, la segunda se renumera al guardar y el del
  camión ni se entera.
- **Mandando la misma pieza a dos locales.** Sale un albarán, no dos. La pieza
  está donde dice el papel, y al segundo se le dice que la acaban de mover.
- **Mandando kilos del mismo lote.** Los kilos se restan dentro de la propia
  orden, así que de un lote de diez no salen doce: al segundo se le dice cuánto
  queda de verdad.
- **Cerrando el turno dos veces.** El turno tiene un cuadre, y la carne sale de
  la cámara **una vez**. Volver a cerrarlo —porque faltaba un recuento, o
  porque el móvil se quedó pensando y se pulsó otra vez— rehace los números sin
  volver a descontar lo que ya estaba descontado.

Por debajo, la base de datos está puesta en el modo en el que **el que lee no
molesta al que escribe** y los que escriben hacen cola en vez de rebotar: ocho
guardados en el mismo segundo entran los ocho, sin errores rojos. Todo esto
está probado con hilos de verdad escribiendo a la vez, en `tests/test_concurrency.py`.

## El banco de pruebas

Las pruebas de siempre comprueban lo que alguien pensó comprobar. El banco es
lo otro: monta **cincuenta casas distintas** —la mitad grupos con obrador y dos
locales, la mitad asadores de una sola cámara—, cinco personas en cada una
—dos managers y tres más—, y las hace trabajar un mes con sus recepciones, su
maduración, sus traslados, sus ventas, su merma y su inventario mensual.

    python -m thegrill.cli --db sqlite:///banco.db banco --casas 50 --dias 30 --martillo 200

Después pasa la lista de **lo que nunca puede pasar**: kilos negativos, un lote
que da más de lo que tenía, un coste en blanco, carne vendida estando congelada,
el kilo de una pieza madurada abaratándose, un número repetido, dinero que
aparece al despiezar, carne en la sede de otra casa. Y el martillo hace
operaciones al azar —incluidas las imposibles— para ver que lo que se rechaza
se rechaza bien y deja la casa igual que estaba.

Dos fallos de verdad salieron el primer día:

- **El libro no cuadraba.** Un traslado a otro local o unas piezas que salían
  del arcón se llevaban kilos del lote sin dejar apunte: el lote bajaba de 9 a
  0,5 y nadie podía explicar por dónde. Ahora cada partición se apunta en los
  dos números —sale de uno y entra en el otro—, y la trazabilidad lo enseña.
- **El kilo de lo madurado se abarataba solo.** Si la báscula leía treinta
  gramos de más, el coste de la pieza se repartía entre más kilos y el precio
  del kilo bajaba, pesada a pesada. Ahora, dentro del juego de la báscula,
  manda el peso de antes y queda escrito lo que se leyó; un kilo de más ya no
  es la báscula y se rechaza como siempre.

## Contar un fallo desde el programa

En todas las pantallas hay un **Contar un fallo**. Lo que no funciona, lo que no
cuadra o lo que se podría hacer mejor, en treinta segundos y sin escribir un
correo: el parte sale con la pantalla de la que viene, el papel de quien
escribe, su idioma y su sede, que es lo que hace falta para repetirlo, y con
nada más.

Se guarda siempre y se manda a `GRILL_BUGS_EMAIL` si está puesto —el correo es
el aviso, no el registro—. La plataforma los lee en `/admin/fallos`, los marca
como vistos, arreglados o cerrados y escribe qué se hizo; la casa que lo contó
lo ve en su pantalla. Así quien usa el programa es también el banco de pruebas
de la versión siguiente.

## Por dónde seguir

1. **La etiqueta que sale**, no solo la que entra: imprimir la del corte
   —serial, lote de origen, fecha de despiece, consumo preferente y un QR que
   abra su trazabilidad— para pegarla en la bolsa. Sin ella la cadena vive solo
   en el ordenador y en la cámara se hace FEFO de memoria.
2. **La retirada de un lote, hacia delante**: hoy se pregunta de dónde viene una
   pieza; la llamada que llega un martes es la contraria —«el lote está
   retirado, ¿dónde ha ido?»— y hay que ir pieza a pieza.
3. Que el traslado entre sedes pueda ir en camino: hoy sale de una cámara y
   entra en la otra en el mismo momento, y un grupo con reparto quiere saber
   qué hay en la furgoneta y qué llegó de verdad.
4. Caducidades y etiquetas por sede en el parte del día, para el pase de cada
   local.
5. Las fotos de la portada: dejar `primal`, `cortes` y `plato` en
   `thegrill/meat/static/fotos/`.
