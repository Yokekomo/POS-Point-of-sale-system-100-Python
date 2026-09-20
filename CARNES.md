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

**Descongelado.** Lo que sale a descongelar y lo que queda al cerrar el turno.
La diferencia es el consumo real, y de ahí sale el peso de verdad por pieza. Sin
recuento no hay cierre.

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
que no es carne, por kilo, por litro o por unidad. De esto no se lleva stock
—aquí no se cuentan patatas—, así que la venta no intenta descontarlo del
almacén ni salta un aviso falso de falta de stock; lo que sí cuenta es su
precio, porque sin él el food cost del plato se queda corto. Cambiar un coste
mueve todos los platos que lo llevan, de una vez.

**Merma de limpieza.** Cada línea del plato puede decir cuánto se pierde al
limpiar: 200 g de patata en el plato salen de 250 en el saco si se pela un 20 %.
Del almacén sale el bruto, y el plato paga el bruto.

**Ventas.** Lo vendido descuenta de cámara por rotación, plato a plato, dejando
escrito a qué plato fue cada salida: sin eso no hay food cost por pieza.

**Inventario, merma y trazabilidad.** Los mismos de la plataforma de cocina:
inventario mensual que re-ancla el stock, merma que la paga lo que queda del
lote, e historia de una pieza de la recepción al plato.

**Descargas.** Cinco hojas de Excel listas para imprimir —recepción, despiece,
descongelado, inventario y merma—, en A4 y en el idioma de quien las baja, para
colgar al lado de la balanza.

## Lo que comparte y lo que no

| | Cocina | Carne |
|---|---|---|
| Motor de despiece, coste, FEFO, descongelado, inventario, trazabilidad, merma | ✓ | ✓ (el mismo código) |
| Acceso, roles, avisos, seis idiomas | ✓ | ✓ |
| Recetas, subrecetas y escandallos de cocina | ✓ | — |
| Emplatado con guarnición y food cost del plato entero | ✓ | ✓ (con su propia pantalla) |
| Plantillas HACCP y registros generales | ✓ | — |
| Recepción de primales y despiece con pantalla propia | — | ✓ |
| Descongelado con pantalla propia | — | ✓ |
| Carta: un plato es un corte y unos gramos | — | ✓ |

Las dos ediciones no comparten base de datos: son dos programas distintos que
se apoyan en el mismo código. Una cocina que quiera las dos cosas usa la
plataforma completa; una que solo maneje carne, esta.

## Idiomas

Los seis de siempre —español, inglés, francés, alemán, neerlandés y árabe, este
de derecha a izquierda—, elegibles en la pantalla de acceso y en configuración.
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
  i18n_meat.py     126 textos propios × seis idiomas
  templates/       Las pantallas propias; lo demás se hereda de la cocina
```

Las plantillas se buscan primero en `thegrill/meat/templates` y luego en
`thegrill/web/templates`: lo que es igual no se copia, y lo que cambia se
sobrescribe. Por eso inventario, merma y trazabilidad son literalmente la misma
pantalla en las dos ediciones.

## Uso

```bash
pip install -r requirements.txt
python -m pytest -q
python -m thegrill.cli --db sqlite:///carnes.db serve-carne --host 0.0.0.0 --port 8001
```

Abre el navegador, pulsa **Dar de alta mi restaurante** y comparte con tu equipo
el código de ocho caracteres. En producción hay que servir por HTTPS, porque la
cookie de sesión se marca como segura salvo que se defina
`GRILL_INSECURE_COOKIE=1`, que es solo para desarrollo.

## Dos cosas que conviene entender

**De dónde sale el consumo de cada corte.** Cada corte elige si se descuenta al
vender en el POS o al cerrar el turno con el recuento de descongelado. Las dos
cosas a la vez descontarían el doble. Un entrecot que se pesa al descongelar va
por conteo; un corte que sale directo de cámara, por venta.

**Qué se controla y qué solo se cuesta.** De la carne se lleva stock pieza a
pieza, con su serial. De la guarnición solo se lleva el coste. Es a propósito:
un hotel no quiere contar patatas en este programa, pero sí quiere que el food
cost del plato sea el de verdad.

## Por dónde seguir

1. Varias cámaras o varios puntos de venta dentro del mismo hotel, para saber
   qué carne está en cada sitio.
2. Importar el parte de ventas del POS desde un fichero, en vez de a mano.
3. Parte de carne diario en PDF, para el pase.
