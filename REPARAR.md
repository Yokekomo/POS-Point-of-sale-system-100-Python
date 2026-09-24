# Lo que hay que reparar

De la auditoría de septiembre de 2026: 312 agentes revisando código y diseño, 324
hallazgos en bruto, **116 confirmados** después de que dos escépticos
independientes intentaran tumbar cada uno. Los 14 que no sobrevivieron no están
aquí. Los informes completos, en `.auditoria/informe-codigo.md` y
`informe-diseño.md`.

Ordenado por lo que más daño hace, no por lo que más fácil es.

---

## 1. Nada de esto puede llegar a un cliente

- [x] **Un encargado de local se hace jefe del grupo en tres peticiones.**
      `meat/app.py:1340` (`POST /manager/equipo/{id}/sede`) no llama a
      `perms.can_manage`; sus cuatro rutas hermanas sí. Y «manager general» se
      define como «manager sin sede», así que quitarse la sede es ascender.
      Después degrada a la dueña metiéndola en un local.
- [x] **Un encargado de local cancela la cuenta del grupo entero.**
      `meat/app.py:543` pide solo `perms.TEAM`, que tienen todos los managers.
      Sin contraseña y sin confirmación.
- [x] **Uno se cambia su propia contraseña sin saber la anterior.**
      `meat/app.py:2322` y `2344`: el `target.id != user.id` deja pasar el caso
      «yo sobre mí mismo», que es el peligroso. Una tablet olvidada es la cuenta.
- [x] **Una casa abre inventario en la cámara de otra.** `inventory.py:85`
      acepta el `site_id` del formulario sin comprobar de quién es. Y luego no
      ajusta nada: se cuenta la cámara entera y no se mueve un kilo.
- [x] **Los códigos de recuperación viajan en la barra de direcciones.**
      `meat/app.py:2294`. Acaban en el historial de la tablet y en los registros
      del proxy, en claro y para siempre. Valen como segundo factor completo.
- [x] **La edición de cocina no tiene ni un middleware de seguridad.**
      `web/app.py:36`: sin CSP, sin nosniff, sin X-Frame-Options. Y escribe
      `<script nonce="">`, que parece que hay política y no la hay.
- [x] **La foto se lee entera en memoria antes de mirar si cabe.**
      `meat/app.py:664`. Un POST de 300 MB son 300 MB en el proceso.

## 2. La cámara sin cobertura, que es para lo que se vende esto

- [x] **La cola da por enviado lo que no se guardó.** `base.html:963`: el
      `fetch` sigue el 303 a `/login` y devuelve 200, así que se borra el
      apunte y se canta «enviado». Igual con la casa bloqueada por impago.
- [x] **Un solo rechazo congela la cola para siempre.** `base.html:949`. Un 403
      de CSRF —móvil compartido entre turnos— y todo lo que se apunte detrás se
      queda dentro del teléfono. La única salida es borrarlo todo.
- [ ] **La cola solo se enciende si `navigator.onLine` es falso**, que es justo
      lo que no pasa en una cámara: el punto de acceso se ve y no se llega.
- [x] **Despiece y traslados no tienen cola.** Se guardan para abrirlos sin
      señal pero no se pueden mandar: la hoja más cara de rellenar se pierde.
- [x] **Salir de la sesión borra las copias y no vuelven.** Desde el primer
      cierre de sesión, cada mañana sale «Sin conexión» en la cámara.
- [x] **El ayudante espera a la red sin plazo.** Con una raya, pantalla en
      blanco indefinida teniendo la copia guardada al lado.
- [ ] **La hora del apunte se guarda y no se manda.** Lo apuntado a las 23:50 y
      mandado a las 00:10 queda fechado al día siguiente.

## 3. Lo que hace perder dinero

- [x] **La maduración no llega al precio.** `service.py:376`: al poner el precio
      se escribe el del albarán, no el del kilo que queda. Una pieza de 10 kg
      que madura hasta 8,5 sale a 30 €/kg en vez de 35,29. **45 € por pieza.**
- [x] **Limpiar una pieza sin precio mete recortes a cero euros**
      (`aging.py:609`) y envenena el escandallo de todos los platos que la
      lleven. El despiece sí tiene esa puerta; la limpieza no.
- [x] **La pérdida del mes sale al doble.** `defrost.py:493`: el desvío del
      turno y el agua del descongelado son el mismo dinero y se suman dos veces.
      Y hay una prueba que garantiza el error en vez de cazarlo.
- [x] **El Excel del POS multiplica por diez o por cien.** `pos_import.py:138`:
      convertía a texto celdas que ya venían como número y luego las pasaba por
      el adivinador de separador decimal. Ahora un número que Excel ya leyó
      viaja como número hasta el final y no vota sobre cómo se escriben los
      decimales, porque no tiene separador que interpretar.
- [x] **Un parte con miles en punto se lee dividido por mil, en silencio.**
      `pos_import.py:273`. Se sigue tomando el punto —no hay manera de saberlo
      con ese fichero delante— pero ya no se da por seguro: sale el aviso con
      el número de ejemplo y cómo se ha leído.
- [x] **«1,5» en los kilos del inventario se guarda 15.** Los dieciséis
      campos decimales de las dos ediciones pasan de `type="number"` a
      `type="text" inputmode="decimal"`: el teclado del móvil sigue saliendo
      numérico y la coma llega entera al servidor, que sí sabe leerla.
- [x] **«1.250» se guarda 1,25.** `exacto.leer` sustituye al `replace(",", ".")`
      en los veinticinco sitios que leían un número de un formulario. Con los
      dos separadores manda el último; repetido, son los miles; y cuando hay
      uno solo con tres cifras detrás lo decide el campo, que es lo único que
      lo sabe: una báscula da tres decimales y el dinero da dos.
- [x] **Entran 1370 kg de solomillo y 240 °C de temperatura de llegada.**
      `rangos.py`, y con dos niveles que no se confunden: lo **imposible** no se
      guarda y la pantalla dice el rango que esperaba; lo que es **verdad y está
      mal** —refrigerado a 12 °C— se guarda tal cual, porque es la prueba, y
      sale hoy en los avisos del manager. Los límites de norma son los del
      Reglamento (CE) 853/2004.
- [x] **Los kilos salen con punto decimal en los siete idiomas.** Una casa
      española leía «9.400 kg» y entendía nueve mil cuatrocientos. Arreglado en
      dos sitios y no en doscientas plantillas: el filtro `format` de Jinja
      —que no es más que el `%` de Python— pasa a poner el separador del
      idioma, y `t()` hace lo mismo con los números que van metidos dentro de
      una frase, que eran los que se escapaban. Se toca solo lo que es un
      número y nada más que un número: un serial, una fecha o un nombre salen
      intactos. `cifras.py`.

## 4. Fechas y trazabilidad

- [x] **Lo que sale del arcón se queda con la fecha del congelador**
      (`defrost.py:124`), así que FEFO lo mandaba al final de la cola y nunca
      salía en «caduca pronto»: la bandeja que había que gastar esta semana
      esperando detrás de la que aguanta hasta el año que viene. Ahora lo
      descongelado caduca como lo descongelado —tres días de serie, los que
      diga la casa en su configuración— y **nunca más tarde de lo que ya
      decía su etiqueta**, porque descongelar no alarga nada. La fecha del
      arcón no se borra: se guarda aparte, que sigue siendo verdad y hay que
      poder enseñarla. `caducidad.py`.
- [x] **Descongelar una pieza entera borra su única fecha** (`aging.py:388`).
      Una pieza que llegó congelada no trae más fecha que la del arcón, y al
      sacarla se quedaba sin caducidad, sin aviso y sin sitio en la cola —y
      el despiece se plantaba con «una fecha no se inventa». Ahora al salir
      del congelador se le pone la de después de descongelar, que es la que
      manda de verdad.
- [x] **El día de trabajo sale del reloj del servidor.** `Restaurant.timezone`
      se pedía, se guardaba y no lo leía nadie. Ahora hay un `jornada.py` que
      responde a una sola pregunta —cuándo es hoy en esta casa— y lo hace con
      dos datos: dónde está el local y **a qué hora cierra el día**, que el
      manager elige en su configuración y de serie son las tres de la mañana.
      Una cocina no cierra a medianoche: lo que se apunta a las dos y media es
      del servicio de anoche, y meterlo en el día siguiente deja dos días mal,
      el de ayer corto y el de hoy largo. Los cuarenta y tantos `date.today()`
      del programa pasan por ahí.
- [x] **La trazabilidad no sirve para lo único que tiene que servir.** Eran
      cuatro agujeros y los cuatro del mismo tamaño, porque lo que la
      trazabilidad no enseña no existe:
      **lo trasladado** —la historia se acababa en el muelle del obrador; ahora
      se sigue el número hijo hasta donde llegue, con su sede delante, y lo
      que se venda allí cuenta para la pieza;
      **lo que se limpia** —una pieza que se limpia y se vende entera no tiene
      despiece, y su historia era una línea: «llegó»—;
      **la venta al corte**, que no deja lote ni pasa por el escandallo, así
      que una pieza madurada vendida entera salía como carne que no se vendió
      nunca;
      y **el despiece de varias piezas**, donde cada una se apuntaba el
      despiece entero: tres piezas de nueve kilos vendiendo veintisiete cada
      una, con el food cost a un tercio de la verdad. Ahora se reparte por
      peso, se dice en pantalla y las partes suman uno.

## 5. Formularios: lo tecleado se pierde

- [x] **Cualquier error borra lo que acabas de escribir** en recepción,
      despiece, precios y merma. En las cuatro vuelve puesto todo lo que
      había: el número de la pieza, los kilos tal y como se escribieron, el
      artículo, el lote del proveedor, las diez líneas del despiece, los
      precios uno a uno y la casilla que hay que corregir, que vuelve con lo
      que se escribió y no vacía. Cuando **sale bien** es al revés y también
      importa: se queda lo del camión y solo lo del camión, porque la
      siguiente bolsa es de la misma caja pero no es la misma pieza. La foto
      no vuelve —un fichero no se puede devolver escrito en una casilla— y
      quien la hizo la tiene todavía en el teléfono.
- [x] **Dos toques con guante crean dos apuntes.** La llave del envío la
      lleva el formulario desde que se pinta, con red y sin ella. Y como es la
      misma mientras la pantalla no se vuelva a pintar, recargar tampoco
      repite el apunte.
- [ ] **Recargar una pantalla de merma vuelve a apuntar los mismos kilos.** Doce
      rutas contestan al POST con HTML en vez de redirigir.
- [x] **Un error deja la pantalla en blanco** con el título «Error 400». Ahora
      cuando el error no trae texto se dice qué ha pasado —si te has
      equivocado tú, si no te toca, o si eso ya no está—, en el idioma de
      **quien está delante** y no en el del navegador, y el botón vuelve a la
      pantalla en la que estabas y no a la portada. Un «volver» que apunte
      fuera de la casa no se obedece.
- [x] **Un «4 C» en la temperatura tumba la recepción con un 500 en inglés.**
      La temperatura se leía una línea por encima del `try`. Ahora no se lee
      nada del formulario fuera de él, y un número mal escrito se contesta
      enseñando lo que se escribió —«4 C» se arregla mirando la C— en el
      idioma de la casa y con la hoja entera todavía puesta.
- [x] **Recepción se abre 1050 px por debajo del formulario** en el móvil. Era
      el `autofocus` de los kilos: con el bloque del lote abierto —la primera
      bolsa de la descarga— esa casilla está mil píxeles más abajo, y el móvil
      abría ahí, enseñando media hoja de nada. Ahora el cursor va a los kilos
      solo de la segunda bolsa en adelante, que es cuando el lote ya está
      puesto, el bloque viene plegado y ese toque se agradece.

## 6. Lo que se romperá con cien casas dentro

- [x] **A una casa en marcha no le llega ninguna columna NOT NULL nueva**, ni
      las reglas UNIQUE, ni los valores nuevos de listas cerradas en PostgreSQL.
      Y la migración no decía ni una palabra. Ahora: una columna obligatoria
      entra con el valor por defecto del modelo puesto en las filas que ya
      existen —que es lo que hace una migración de verdad— y, cuando ese valor
      es una función y no hay uno que valga para todas, se queda fuera **pero
      se dice**. Las reglas de «no puede haber dos iguales» se ponen como
      índice único, comparando por columnas y no por nombre para no repetirlas
      en cada arranque; si los datos de la casa ya traen un repetido, la orden
      falla y eso también se dice, porque significa que algo se duplicó de
      verdad. Y en PostgreSQL se añaden los valores nuevos de las listas
      cerradas, que si no revientan el día que alguien usa ese estado.
      El parte sale en `/admin`, en rojo y lo primero, con la orden que lo
      arregla al lado.
- [x] **El lote hijo se escribe antes de comprobar que quedan kilos:** aparecen
      kilos de la nada. Pasaba al sacar del arcón y al mandar a otra sede: si
      la resta fallaba —otra persona se había llevado esos kilos un segundo
      antes— se levantaba el error y la pantalla lo decía, pero **el hijo
      quedaba escrito igual**, porque la petición terminaba bien y la sesión
      se guardaba. En la cámara quedaba un número con kilos que no habían
      salido de ninguna parte, y el padre seguía teniéndolos: la misma carne
      dos veces. Ahora primero se sacan los kilos y solo después nace el
      número que los lleva. Un error que se ve se arregla; uno que deja carne
      inventada en el inventario no lo ve nadie hasta el recuento.
- [ ] **Un despiece de varias piezas que choca a mitad deja piezas marcadas como
      cortadas** sin cortes detrás.

---

## Ya reparado

- [x] **Que la cola no mienta**, y el token se coja al mandar y no al apuntar:
      con eso el rechazo por token viejo —el más frecuente— deja de existir.
      Y la llave contra duplicados va ya en todos los formularios, con red y
      sin ella. `tests/test_cola.py`.

- [x] **Las siete del bloque 1**, y con ellas `tests/test_puertas.py`: nueve
      pruebas que no comprueban que el manager pueda, sino **que el de al lado
      no pueda**, que es lo que nadie escribe hasta que pasa.

- [x] **El reparto del coste perdía millonésimas.** Ahora en céntimos enteros
      por restos mayores (patrón Money de Fowler). `exacto.py`.
- [x] **El libro se inventaba dinero al redondear cada apunte.** Hasta medio
      euro por lote. Lo encontró esta misma auditoría contra código escrito dos
      commits antes: `cost` es un derivado y estaba donde van los importes.
- [x] **Importes con milésimas y pesos con miligramos llegaban al disco.**
      Doscientos treinta y siete en una casa de veinticinco días; ahora cero.

- [x] **Los cinco del dinero escrito a mano**: el Excel que multiplicaba por
      diez, el parte que se dividía entre mil sin avisar, el «1,5» que se
      guardaba 15, el «1.250» que se guardaba 1,25 y los rangos de proceso.
      `exacto.leer`, `rangos.py`, `tests/test_numeros.py` y
      `tests/test_rangos.py`.

- [x] **La actualización de una casa en marcha, y su parte.** Columnas
      obligatorias, reglas de unicidad, listas cerradas de PostgreSQL, y lo
      que no se pudo poner dicho en `/admin`. `tests/test_migration.py` (12).

- [x] **El número que nacía sin kilos que llevar.** Al descongelar y al
      trasladar, el lote hijo se escribía antes de comprobar que quedaban
      kilos en el padre. Dos pruebas que reproducen la carrera de verdad, y
      que fallan contra el código de antes.

- [x] **Los seis de los formularios**: el «4 C» de la temperatura y el
      «32 eur» de los precios, que tumbaban la pantalla; lo tecleado que se
      borraba en las cuatro hojas; el doble toque con guante; la pantalla de
      error en blanco; y el móvil que abría mil píxeles por debajo del
      formulario. `tests/test_formularios.py` (19).

- [x] **Los cuatro de los formularios**: el «4 C» que tumbaba la recepción, lo
      tecleado que se borraba, el doble toque con guante y la pantalla de
      error en blanco. `tests/test_formularios.py`.

- [x] **La historia de una pieza llega hasta el final.** Traslados, limpiezas,
      venta al corte y el reparto del despiece compartido. `tracing.py`.

- [x] **Lo descongelado sale primero.** Caduca como lo descongelado y no como
      lo congelado, que es lo que decide su sitio en la cola de rotación. Los
      días los pone la casa. `caducidad.py`, `tests/test_caducidad.py`.

- [x] **Cuándo es hoy.** La zona horaria de la casa y la hora a la que cierra
      el día, elegible por el manager. `jornada.py`, `tests/test_jornada.py`.

- [x] **Los números se escriben y se leen como en el país de la casa.**
      `exacto.leer` para lo que se escribe y `cifras` para lo que se lee.

- [x] **La pantalla de alertas del manager salía vacía siempre.** Encontrado
      escribiendo las pruebas de los rangos: la ruta de carne mandaba la lista
      como `alerts` y la plantilla la leía como `rows`. Sin error y sin aviso:
      simplemente no aparecía ni una línea. Todo lo que el programa levanta
      —carne caliente, pieza pasada de fecha, merma de maduración, corte bajo
      mínimo— se guardaba bien y no lo veía nadie, que en un registro sanitario
      es lo mismo que no guardarlo.
