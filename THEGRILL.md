# Plataforma de gestión de cocina

Aplicación web para cualquier restaurante. El equipo registra desde el móvil
(temperaturas, mermas, recepciones, producción, limpieza, conteos, con fotos) y
la dirección lo ve todo en un panel con estadísticas, alertas y export para
auditoría.

El programa recoge datos de los trabajadores y los presenta. No envía mensajes
a nadie por fuera: los avisos viven dentro de la propia plataforma.

Está en seis idiomas: español, inglés, francés, alemán, neerlandés y árabe. Y
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
número más bonito y falso.

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

Seis idiomas, con las tres lenguas oficiales de Bélgica cubiertas:

| Código | Idioma | Escritura |
|---|---|---|
| es | Español | izquierda a derecha |
| en | English | izquierda a derecha |
| fr | Français | izquierda a derecha |
| de | Deutsch | izquierda a derecha |
| nl | Nederlands | izquierda a derecha |
| ar | العربية | derecha a izquierda |

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
  models.py              34 tablas: plataforma, módulos de carne, auditoría
  rules.py               Reglas del negocio de carne como funciones puras
  web/
    i18n.py              Seis idiomas, resolución y escritura de derecha a izquierda
    sheets.py            Hojas de registro en Excel, listas para imprimir
    costing.py           Precios reales, rotación de lotes y descuento por venta
    butchery.py          Del primal a los cortes, con reparto de coste, serial propio y stock diario
    defrost.py           Descongelado, recuento de cierre y peso real por pieza
    auth.py              Contraseñas, sesiones, alta de restaurante, códigos de acceso, roles
    seed.py              Las siete plantillas por defecto
    service.py           Validación, alertas, avisos, fotos, estadísticas, export CSV
    app.py               Rutas web de empleado y de manager
    templates/           Veinte pantallas, móvil primero, claro y oscuro
  engine/
    fefo.py              Consumo por caducidad y valoración de merma
    stock.py             Motor v4: ledger, re-anclaje por conteo, genealogía por serial
    cost.py              Landed por kg, coste por corte, food cost
    recipes.py           Escandallo: explosión, árbol de costes y food cost
    defrost.py           Cuadre del descongelado y desvío contra la receta
  orchestrator/chain.py  Cadena diaria idempotente con checkpoints y reintentos
  importers/             Pendiente: POS PDF, facturas, hojas manuscritas
  reports/               Pendiente: parte de carne, informe diario PDF
  cli.py                 init-db, run-chain, serve
tests/                   242 tests
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
