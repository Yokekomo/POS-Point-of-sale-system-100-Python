# Plataforma de gestión de cocina

Aplicación web para cualquier restaurante. El equipo registra desde el móvil
(temperaturas, mermas, recepciones, producción, limpieza, conteos, con fotos) y
la dirección lo ve todo en un panel con estadísticas, alertas y export para
auditoría.

El programa recoge datos de los trabajadores y los presenta. No envía mensajes
a nadie por fuera: los avisos viven dentro de la propia plataforma.

Está en siete idiomas: español, inglés, francés, alemán, neerlandés, árabe y
húngaro. Y
trae las mismas hojas en Excel para imprimir y rellenar a mano cuando hace
falta.

Incluye además los motores especializados de control de carne desarrollados
para el proyecto The Grill (FEFO, stock por serial de primal, despieces y
coste) como módulos opcionales sobre la misma base de datos.

## Por qué web y no un programa de escritorio ni una app de tienda

| Necesidad | Web | Programa Windows | App nativa |
|---|---|---|---|
| Cualquiera entra y mete datos, sin instalar nada | Sí | No | Requiere descarga |
| Registro a pie de cámara desde el móvil | Sí | No | Sí |
| Varios usuarios a la vez | Sí | Un puesto | Sí |
| Hora de servidor no manipulable, registros no editables | Sí | Archivo local editable | Sí |
| La dirección lo consulta desde fuera | Sí | No | Sí |
| Cadena de tareas automática nocturna | Sí, en servidor | El PC ha de estar encendido | No |
| Coste de mantenimiento | Un solo código | Un solo código | Dos plataformas y tiendas |

Se añade a la pantalla de inicio del móvil como una aplicación más. Para
imprimir etiquetas en térmica se usa un pequeño servicio local, apoyado en el
código de impresión del punto de venta que ya existe en este repositorio.

## Los dos roles

**Manager.** Ve el panel de estadísticas, el historial completo de todos,
las fotos de cualquiera, y gestiona alertas, plantillas y equipo. Descarga el
CSV de auditoría. Reparte el código de acceso del restaurante.

**Empleado.** Ve la pantalla de captura y rellena registros con fotos. Consulta
solo lo que él mismo ha enviado. No entra en ninguna pantalla de gestión: cada
ruta del área de manager le responde 403.

## Recetas, escandallos y stock en tiempo real

Cuatro niveles, de abajo arriba:

1. **Artículos**: lo que compras de verdad, con su marca y su proveedor. Dos
   tipos de chunk beef son dos artículos.
2. **Ingrediente madre**: «Beef for burger». Los artículos cuelgan de él y sus
   lotes se gastan en una sola cola. Cambiar de proveedor es dar de alta otro
   artículo; la receta no se toca.
3. **Elaboraciones**: Burger patty, Salsa burger, Burger. Una elaboración dice
   cuánto produce, y por tanto cuánto cuesta su unidad.
4. **Platos**: Cheese burger. Se componen de elaboraciones e ingredientes.

**Rotación.** Cada madre elige FEFO, que saca antes lo que antes caduca, o FIFO
estricto para seco y no perecedero. Compiten los lotes de todas sus marcas.

**Precio real.** El precio de una madre es la media ponderada de lo que queda
en sus lotes. Sin stock, el último precio conocido. Sin ninguno de los dos, el
precio es desconocido y se dice: nunca se cuenta como cero.

**Peso neto y merma.** Cada línea lleva lo que acaba en el plato y el porcentaje
que se pierde al limpiar. Del almacén sale el bruto, `neto / (1 - merma)`. Así
se ve lo que cuesta lo que se tira.

**Food cost.** Se mide contra el precio sin impuestos. Sobre el PVP saldría un
número más bonito y falso. Se muestra en entero y hacia arriba —un 32,4 % se
lee 33 %—, igual en el escandallo, en la carta y en la trazabilidad de una
pieza: un food cost no se redondea a la baja, y el color de aviso va sobre el
número que se lee, para que nunca se contradigan.

**Identificación del POS.** Cada emplatado se ata a su producto del punto de
venta. Según el POS, el artículo llega por número o por nombre; se guardan los
dos y el restaurante elige en su configuración por cuál emparejar. Con «ambos»,
el código manda, porque un nombre se reescribe y un código no.

**Descuento de stock.** Lo vendido se explota hasta ingredientes madre y se
descuenta de los lotes por rotación, dejando un movimiento por cada salida con
su coste. Si falta stock se descuenta lo que hay, se registra el faltante y
salta una alerta: alguien no registró una entrada. Nunca queda stock negativo.

**Tres vistas del dinero** en cada escandallo:

- **Dónde se va el dinero**: total por ingrediente madre sumando todos los
  caminos por los que entra al plato, de mayor a menor.
- **Composición**: el árbol entero con el coste y el porcentaje de cada nivel,
  del plato hasta el ingrediente.
- **La carta por food cost**: todos los platos ordenados por el peor margen.

## La carne va por su cuenta

> **Edición solo carne.** Para una cocina que lo que necesita controlar es la
> carne, todo este capítulo existe como programa aparte, con su propio acceso y
> su propia base de datos: **[Control de carnes](CARNES.md)**. Mismo motor,
> sin recetas de cocina ni plantillas generales, y con las pantallas que aquí
> no hay: recepción de primales, despiece y descongelado.


Un primal no es un ingrediente cualquiera: es una pieza física que se convierte
en varias cosas a la vez.

**Llegada.** Los primales vienen en grupo bajo un lote de recepción. Cada pieza
lleva su propio número y su coste puesto en almacén, que se congela al
despiezarla para que no se pierda ni cambie después.

**Despiece.** Del primal salen hasta una decena de cortes distintos, partes
para reusar y merma. El recorte es un corte más, marcado como tal. La masa
tiene que cuadrar: entrada igual a cortes más recorte más merma, con su
tolerancia; si no cuadra, se dice.

**Reparto del coste.** El coste de la pieza se reparte entre lo aprovechable en
proporción a kilos por índice de valor. La merma no recibe nada: su coste lo
absorben los cortes. Por eso un striploin comprado a veinte sale a veinticinco
el kilo cuando el despiece rinde al ochenta por ciento, y a treinta y tres si
rinde al sesenta. Eso es lo que hay que ver.

**La etiqueta del corte.** Cada lote guarda cuántas piezas salieron, el peso de
carta, la calidad y la procedencia, y se lee «330 g (~352 g · 40,1 % FC) ·
MB9+ · AUS» sin ir a buscar el despiece.

Delante va el peso de carta, el que se vende y con el que se hace el
escandallo. Entre paréntesis, la realidad: el promedio que salió y el food cost
al que está saliendo ese corte.

El promedio son los kilos pesados entre las piezas que salieron. Cortando a
mano no hay precisión de gramo, así que lo único fiable es el total y el
recuento, y el peso por pieza se deduce de ambos. Si el corte sale clavado, el
peso no se repite. El food cost solo aparece cuando ya se ha vendido algo:
antes de eso no hay número que dar.

Ese promedio contra el objetivo dice si se está cortando de más: un despiece
que se pasa del diez por ciento lo avisa al volcarlo. Y con él, los kilos que
quedan se estiman en piezas.

Si dos calidades distintas entran en el mismo despiece no se afirma ninguna; el
origen sí, si coincide.

**Trazabilidad.** Cada corte y cada recorte recibe un serial nuevo al salir del
despiece, atado a la pieza de la que salió y al envío en que llegó. Desde una
venta se puede volver hacia atrás hasta el primal. Cuando un despiece consume
varias piezas a la vez, el padre es el batch y no se finge una trazabilidad por
pieza que no existe.

**Y de ahí a la cocina.** Cada corte entra en el almacén como un artículo, el
artículo cuelga de su ingrediente madre, y la madre se usa en subrecetas,
recetas y emplatados igual que cualquier otro ingrediente. El recorte de
striploin y el de cube roll caen los dos en «Beef for burger» y se gastan en
una sola cola.

## Descongelado y consumo real de la carne

Un entrecot no pesa siempre lo mismo, así que la carne al corte no se puede
descontar por escandallo. Se mide físicamente y por serial:

    consumido = lo que había + lo que se sacó a descongelar − lo que queda

Cada ingrediente madre dice cómo se descuenta: al vender según la receta, o al
cerrar turno por conteo. La carne marcada por conteo no se descuenta al vender,
para no contarla dos veces; su cifra teórica se guarda y se compara.

El cierre de turno descuenta de esa pieza concreta y dice el **peso real por
pieza vendida**. Comparado con el teórico, ese número es el que revela si se
está cortando de más. Pasado el desvío configurado, avisa.

Sin recuento no hay consumo: no se inventa nada, el stock se queda quieto y se
nombran los seriales que faltan por contar. Contar más de lo que había es
imposible y se dice: falta apuntar una salida a descongelar.

## Stock de carne al cerrar el día

Una pantalla dice cuántos kilos de cada corte quedan en cámara, con cuántos
seriales abiertos y cuánto hay descongelado ahora mismo, y cuántos primales sin
despiezar quedan por SKU.

Cada corte puede llevar un mínimo, y cada SKU de primal un mínimo de piezas. Al
cerrar el día se avisa de lo que baja del mínimo, de los primales que hay que
pedir y de los cortes que caducan pronto. Un SKU a cero con mínimo definido sale
igualmente, porque cero es justo el caso que hay que gritar.

## Inventario de carne para cuadrar números

Obligatorio uno al mes, el día que convenga. Contar más a menudo es opcional, y
un inventario empezado se puede cancelar: uno cancelado no ajusta nada y no
cuenta para el mes. Uno parcial tampoco cuenta, por lo mismo que no cuadra.

Al abrirlo, el sistema saca la lista de lo que cree
tener: cada corte con su serial y sus kilos, y cada primal sin despiezar. Se
cuenta a mano pieza a pieza y al cerrarlo se cuadra.

De cada pieza sale uno de cinco resultados: cuadra, falta, sobra, no aparece, o
estaba en cámara sin que el sistema la tuviera. Cinco gramos de diferencia son
la báscula, no una diferencia.

Al cerrar:

- lo contado **re-ancla el stock**, con su movimiento de ajuste valorado al
  precio de ese lote;
- un primal que no aparece queda **sospechoso**, nunca cortado: solo el
  despiece que lo confirma lo pasa a cortado;
- lo que apareció sin estar en el sistema se nombra, no se inventa un lote;
- lo que quedó **sin contar no se toca** y se dice cuál es. Un parcial no
  cuadra.

Si al mes le quedan cinco días o menos sin inventario hecho, el cierre diario
de carne lo recuerda.

El cierre dice cuántos kilos faltan y cuánto dinero son, qué parte de las
piezas cuadraba, y avisa a dirección de la merma, de los fantasmas y de lo que
quedó a medias. Lo esperado se relee al cerrar, no al abrir, para que el ajuste
cuadre contra el estado de ese momento aunque se haya vendido mientras se
contaba.

## La historia de una pieza

Se mete el número de serie de un primal y sale todo lo que pasó con él: el
despiece del que salió y con qué rendimiento, cada corte con su propio serial,
lo que se vendió de cada uno y en qué plato, lo que se tiró, lo que queda y los
kilos que no cuadran.

Arriba, el resumen de la pieza: lo que costó, lo que ingresó, lo ganado, su
food cost real y qué parte del coste ya se ha recuperado.

**Cómo se reparte el ingreso.** Cada venta reparte el precio del plato sin
impuestos entre sus ingredientes, en proporción a lo que cuesta cada uno dentro
del plato. Un coste C dentro de un plato que convierte Cd de coste en Pd de
ingreso aporta C × Pd / Cd. Lo que cae sobre un corte de esta pieza es lo que
se le atribuye, y por eso cada salida de almacén deja escrito a qué plato fue.

Para la carne que se descuenta por conteo de descongelado, que no nombra el
plato, se reparte con la media de los platos que usan ese ingrediente.

## La merma de cámara y quién paga lo tirado

La merma del despiece ya la absorben los cortes al repartir el coste del
primal. La otra merma es la que pasa después: la pieza que ya estaba cortada y
se echa a perder. Esa tiene su propio apartado, y lo puede registrar cualquiera
del equipo, porque quien tira la pieza es quien está en la cámara.

De cada merma queda escrito el ingrediente, el número de despiece, el serial de
la pieza, los kilos, las piezas, el motivo y quién la tiró. Se indica el serial
si la pieza lo lleva; si no, basta el ingrediente y sale del lote que toque por
rotación.

**Lo tirado no desaparece del coste.** Si de diecisiete filetes se tiran dos,
los quince que se vendan tienen que pagar los diecisiete: al registrar la merma
sube el precio por kilo de lo que queda de ese lote, de modo que el valor total
del lote se mantiene, y con él sube el food cost de los platos que lo lleven.
Diecisiete filetes de 5,1 kg a 30 €/kg valen 153 €; tirados 0,6 kg, los 4,5 kg
que quedan pasan a 34 €/kg y siguen valiendo 153 €.

Si se va el lote entero no queda nadie a quien cargárselo: ese coste se pierde,
y así se dice. Una merma nunca puede dejar el stock en negativo. Dirección
recibe el aviso de cada merma, crítica si pasa de 50 en dinero.

**Y todo lo que se tira se ve junto.** La pantalla de merma suma dos orígenes:
lo que se echa a perder en cámara y lo que se quita limpiando una pieza entera
—costra, telilla, grasa sucia—. Cada línea dice de dónde viene, de qué pieza,
cuántos kilos, lo que valían y quién lo tiró; arriba, el total del periodo y el
reparto entre los dos orígenes. Lo tirado limpiando se valora a lo que costaba
el kilo de esa pieza en ese momento, que es lo que se perdió de verdad. El
dinero es cosa del manager: quien no ve costes ve los kilos.

## Maduración, congelador y venta a peso

Una pieza entera está en uno de tres sitios, y no es lo mismo. En cámara es la
que llegó. Congelada, el reloj se para: manda la fecha de consumo del
congelador y lo que se despiece de ella nace congelado, hasta que alguien lo
saque a descongelar. Madurando, pierde agua todos los días.

Y ahí está la cuenta que casi nadie hace: **los kilos se van, el dinero no**.
Una pieza de 9 kg a 30 € el kilo son 270 €. A los cuarenta y cinco días pesa
7,6 y esos 270 € siguen enteros, así que el kilo vale 35,53 €. El programa
vuelve a calcular el precio del kilo en cada pesada y deja escrito el peso de
antes, el de ahora, lo perdido y a cómo queda: eso es lo que hay que mirar
antes de poner el precio en la carta.

El inventario del mes pesa esas piezas como cualquier otra, así que también
cuenta como pesada. Lo que se ha dejado madurando es agua, no carne que falte,
y se apunta como tal: el aviso de carne que falta se queda solo con lo que de
verdad falta. Si pierde más de lo razonable —más de un 10 % de una pesada a la
siguiente o más de un 20 % desde que entró— salta el aviso; del 30 % para
arriba, crítico.

Se limpia dos veces y no son lo mismo: antes de madurar, para quitar lo que
sobra, y al cabo de las semanas, para quitar la costra seca, que crece con los
días. Las dos se apuntan aparte del agua —si no, la merma de maduración sale
inflada— y las dos suben el precio del kilo que queda. De una limpieza salen siempre las dos cosas, y se dicen las dos: los
kilos que vuelven a cámara —hasta tres destinos, cada uno con su artículo y su
índice de valor— y los que se tiran. Lo guardado más lo tirado tiene que ser lo
quitado, o no se apunta. Lo que vuelve entra con su propio lote colgando de la
pieza y se lleva solo lo que vale; lo tirado no se lleva nada y lo pagan los
kilos que quedan. De
ahí sale el **rendimiento**: lo que queda de lo que entró, que es el número que
dice si compensan cuarenta y cinco días o sesenta.

La madurada pasa por despiece igual que las demás: la limpieza grande puede
hacerse ahí, y la pagan los kilos que quedan. Al limpiarla se
decide qué sale, y pueden salir las dos cosas a la vez: un corte **a peso**, que
entra en cámara en kilos y sin piezas porque se corta delante del cliente, y
raciones ya cortadas con su peso.

Todo se vende por el POS. Un plato a peso se cobra por kilo y el parte de ventas
trae los gramos de cada venta: son esos gramos los que descuentan de cámara y
los que dan el coste de ese corte al precio del kilo de hoy. Sin ese peso se
descuenta la ración de referencia y se avisa, porque la referencia no es lo que
se cortó.

## Cuando la pieza aparece

Dar una pieza por perdida no es definitivo. Si el primal o el corte aparecen
después, hay una corrección que los devuelve al stock:

- un **primal sospechoso** deja de serlo, y se puede corregir su peso de paso;
- un **corte que el inventario dejó a cero** vuelve con los kilos que hayan
  aparecido, dejando su movimiento de ajuste valorado;
- una **pieza que el sistema nunca tuvo** se da de alta diciendo de qué
  artículo es y a qué precio: un lote no se inventa con el coste en blanco.

La corrección solo sube, nunca baja: para bajar stock se cuenta en un
inventario. Un primal que consta cortado no se corrige aquí, porque lo que está
mal entonces es el despiece. Y nada de esto se hace en silencio: queda en el
registro de auditoría con quién, cuándo y por qué, y dirección recibe el aviso.

## Hojas para imprimir

Hay un apartado de descargas, abierto a todo el equipo, con una hoja de Excel
por cada registro activo, más un libro con todas juntas, una pestaña por
registro.

Cada hoja sale de la misma plantilla que el formulario de la pantalla, así que
las columnas del papel y los campos de la aplicación coinciden siempre. Si el
manager añade un campo, la hoja lo trae en la siguiente descarga.

Lo que lleva cada hoja:

- Cabecera con el registro, el restaurante, y huecos a mano para turno y quién
  la rellena.
- Una columna de número de fila, otra de fecha y otra de hora, porque un parte
  de papel cubre varios días y sin hora no vale para HACCP.
- Una columna por campo, con su unidad, sus límites y un asterisco si es
  obligatorio. Así el cocinero ve en el papel que la cámara no puede pasar de
  +5 °C.
- Una fila de ejemplo en gris con valores realistas, y una leyenda que avisa de
  que no se use.
- Veinte filas en blanco con cuadrícula y una línea de firma del responsable.
- Ajustes de impresión ya puestos: A4, todo a lo ancho de una página, el
  encabezado repetido en cada página, y vertical mientras quepa para sacar más
  filas por hoja. En árabe la hoja se lee de derecha a izquierda.

El aviso de la pantalla lo deja claro: lo escrito en papel hay que pasarlo
luego a la plataforma, porque el papel no genera alertas ni entra en las
estadísticas.

## Idiomas

Siete idiomas, con las tres lenguas oficiales de Bélgica cubiertas. A quien
llega sin decir nada se le habla en inglés; si su navegador pide un idioma que
sabemos, ese. El español sigue siendo el catálogo de referencia por dentro.

| Código | Idioma | Escritura |
|---|---|---|
| es | Español | izquierda a derecha |
| en | English | izquierda a derecha |
| fr | Français | izquierda a derecha |
| de | Deutsch | izquierda a derecha |
| nl | Nederlands | izquierda a derecha |
| ar | العربية | derecha a izquierda |
| hu | Magyar | izquierda a derecha |

**Dónde se elige.** En la pantalla de acceso, antes de entrar, con un selector
que recuerda la elección en este navegador. Y dentro, en Configuración, donde
cada persona fija su propio idioma. Un manager fija además el idioma del
restaurante.

**Qué idioma se usa**, por orden: el que la persona eligió, el elegido en el
acceso, el que pide el navegador, el del restaurante y, si no, español. Un
idioma desconocido se ignora en cada paso, nunca rompe la pantalla.

**Qué se traduce y cuándo.** La interfaz, en el idioma de quien mira. Los
errores de un formulario, en el idioma de quien lo rellena, porque los lee él
en ese momento. Las alertas y los avisos se guardan en el idioma del
restaurante, porque quedan almacenados y los lee todo el equipo. Las plantillas
iniciales se crean en el idioma del restaurante al darlo de alta, y a partir de
ahí son datos suyos que puede reescribir.

**En árabe** la interfaz entera se voltea a derecha a izquierda, incluidas
tablas, barras y el marcado de los avisos.

**Añadir un idioma** es copiar el diccionario español en `web/i18n.py`,
traducir los valores y registrarlo. Cuatro tests lo vigilan: ninguna clave sin
traducir, ninguna de más, ningún texto vacío y ningún hueco de interpolación
como `{label}` perdido por el camino. Un quinto barre todas las pantallas en
inglés y falla si alguna deja texto en español.

## Avisos: la alerta va a buscar a quien debe actuar

Una alerta crítica no puede quedarse esperando a que alguien entre a mirar el
panel. Cuando un registro se sale de límites, cada manager activo recibe un
aviso dentro de la plataforma:

- **Contador en la cabecera**, en todas las pantallas, que se refresca solo cada
  medio minuto y al volver a la pestaña. No hay que recargar nada.
- **Página de avisos** con el detalle, quién lo registró y el enlace para cerrar
  la alerta. Abrirla los marca como vistos y guarda la hora, así queda constancia
  de cuándo se enteró cada cual.
- **Aviso del navegador**, opcional. Si la persona lo autoriza, una alerta
  crítica le salta en la pantalla aunque tenga la plataforma en segundo plano.
  En iPhone hace falta añadir antes la plataforma a la pantalla de inicio.

Quien registró el problema recibe a su vez un aviso cuando un manager cierra la
alerta, con la acción correctiva escrita. Nadie se avisa a sí mismo, y ningún
aviso cruza de un restaurante a otro.

Un manager puede ascender a un empleado o desactivar una cuenta, pero no puede
cambiar su propio rol ni desactivarse a sí mismo.

## Cómo se adapta a cualquier restaurante

No hay nada codificado sobre un negocio concreto. Cada restaurante define sus
**plantillas de registro**: nombre, categoría, cuántas veces al día se espera,
si pide foto, y sus campos (texto, número, lista, fecha, sí/no). Un campo
numérico con mínimo y máximo genera alerta automática al salirse; un campo de
fecha avisa cuando la caducidad se acerca, que es la base del control FEFO.

Un restaurante nuevo arranca con siete plantillas listas, que el manager edita
o desactiva: temperatura de refrigeración, temperatura de congelación,
recepción de mercancía, merma, producción, limpieza y conteo de inventario.
Refrigeración y congelación van separadas a propósito, porque cada una tiene su
propio límite legal y un solo campo numérico no puede validar los dos.

## Reglas que el programa no deja saltarse

- Un campo obligatorio vacío rechaza el registro entero. No se guarda a medias.
- Un valor fuera de límites **sí** se guarda, marcado con alerta. Una cámara a
  9 °C tiene que quedar registrada, no rechazada.
- La fecha y la hora las pone el servidor.
- Los registros no se editan ni se borran. Una corrección es un registro nuevo
  que apunta al anterior, y el original queda marcado como corregido.
- Cerrar una alerta exige escribir la acción correctiva, y queda firmado con
  quién y cuándo.
- Cada aviso guarda cuándo se vio, así que se puede demostrar que la desviación
  llegó a la persona responsable.
- Ningún restaurante ve datos, fotos ni estadísticas de otro.
- Las contraseñas se guardan con PBKDF2 y sal por usuario. La sesión viaja en
  cookie httponly y en la base solo vive el hash del testigo. Cada formulario
  lleva token CSRF.
- Solo se aceptan imágenes y PDF, hasta 12 MB, y cada archivo guarda su SHA-256.

## Estructura

```
thegrill/
  config.py              FX, límites HACCP, umbrales de precio, tolerancias de despiece
  db.py                  SQLAlchemy; SQLite por defecto, PostgreSQL cambiando la URL
  models.py              38 tablas: plataforma, módulos de carne, auditoría
  rules.py               Reglas del negocio de carne como funciones puras
  web/
    i18n.py              Siete idiomas, resolución y escritura de derecha a izquierda
    sheets.py            Hojas de registro en Excel, listas para imprimir
    costing.py           Precios reales, rotación de lotes y descuento por venta
    butchery.py          Del primal a los cortes, con reparto de coste, serial propio y stock diario
    defrost.py           Descongelado, recuento de cierre y peso real por pieza
    inventory.py         Inventario mensual que re-ancla el stock, y recuperación de piezas
    tracing.py           Historia de un primal y reparto del ingreso
    waste.py             Merma de cámara: queda escrita y la paga lo que queda del lote
    aging.py             Maduración, congelador y venta a peso: el agua se va y el dinero no
    auth.py              Contraseñas, sesiones, alta de restaurante, códigos de acceso, roles
    seed.py              Las siete plantillas por defecto
    service.py           Validación, alertas, avisos, fotos, estadísticas, export CSV
    app.py               Rutas web de empleado y de manager
    templates/           Veintiuna pantallas, móvil primero, claro y oscuro
  engine/
    fefo.py              Consumo por caducidad y valoración de merma
    stock.py             Motor v4: ledger, re-anclaje por conteo, genealogía por serial
    cost.py              Landed por kg, coste por corte, food cost
    recipes.py           Escandallo: explosión, árbol de costes y food cost
    defrost.py           Cuadre del descongelado y desvío contra la receta
    inventory.py         Conciliación del inventario físico, pieza a pieza
  meat/                  Edición solo carne: ver CARNES.md
  orchestrator/chain.py  Cadena diaria idempotente con checkpoints y reintentos
  importers/             Pendiente: POS PDF, facturas, hojas manuscritas
  reports/               Pendiente: parte de carne, informe diario PDF
  cli.py                 init-db, run-chain, serve
tests/                   551 tests
```

## Uso

```bash
pip install -r requirements.txt
python -m pytest -q
python -m thegrill.cli init-db
python -m thegrill.cli serve --host 0.0.0.0 --port 8000
```

Abre el navegador, pulsa **Dar de alta mi restaurante**, y comparte con tu
equipo el código de ocho caracteres que aparece en el panel. Ellos entran por
**Unirme con un código**.

En producción hay que servir por HTTPS, porque la cookie de sesión se marca
como segura salvo que se defina `GRILL_INSECURE_COOKIE=1`, que es solo para
desarrollo. Las fotos se guardan en la ruta de `GRILL_UPLOAD_DIR`.

## Reglas del módulo de carne y dónde viven

| Regla | Función | Test |
|---|---|---|
| Etiqueta física manda sobre la hoja | `rules.resolve_from_label` | `test_label_wins_over_sheet_and_flags` |
| Cada despiece guarda seriales, piezas y peso por pieza | `rules.validate_tg`, tabla `despiece_primals` | `test_tg_requires_serials_and_cut_detail` |
| Nunca marcar cortado por inferencia | `rules.can_mark_cut`, `stock.drain_primals` | `test_drain_primals_by_serial_only` |
| Conteo semanal completo | `rules.weekly_count_is_complete` | `test_weekly_count_complete_and_stale` |
| FEFO, coste nunca en blanco | `fefo.consume` | `test_never_blank_cost` |
| Lo tirado lo paga lo que queda del lote | `waste.record` | `test_the_pieces_that_survive_pay_for_the_ones_thrown` |
| Food cost entero y hacia arriba | `butchery.ceil_pct` | `test_the_escandallo_shows_the_food_cost_rounded_up_with_no_decimals` |
| Pescado fuera del registro de carne | `rules.is_meat_entry` | `test_fish_excluded_from_meat_entry` |
| Idempotencia y checkpoints | `orchestrator.chain.Chain` | `test_sequential_idempotent_and_weekday_steps` |
| Tres estados, nunca colapsar en cero | `models.SourceStatus` | `test_three_states_never_collapse` |
| Reintentos solo en errores transitorios | `chain.TransientError` | `test_transient_retry_with_backoff...` |

## Siguientes pasos

1. Funcionamiento sin cobertura: guardar en el móvil y sincronizar al recuperar señal.
2. Importadores: PDFs del punto de venta, facturas y hojas manuscritas.
3. Informe diario en PDF y parte de carne, para consultar y descargar desde la plataforma.
4. Programador nocturno que ejecute la cadena diaria por restaurante.
5. Más idiomas según haga falta en cada cocina.
