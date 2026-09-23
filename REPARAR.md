# Lo que hay que reparar

De la auditoría de septiembre de 2026: 312 agentes revisando código y diseño, 324
hallazgos en bruto, **116 confirmados** después de que dos escépticos
independientes intentaran tumbar cada uno. Los 14 que no sobrevivieron no están
aquí. Los informes completos, en `.auditoria/informe-codigo.md` y
`informe-diseño.md`.

Ordenado por lo que más daño hace, no por lo que más fácil es.

---

## 1. Nada de esto puede llegar a un cliente

- [ ] **Un encargado de local se hace jefe del grupo en tres peticiones.**
      `meat/app.py:1340` (`POST /manager/equipo/{id}/sede`) no llama a
      `perms.can_manage`; sus cuatro rutas hermanas sí. Y «manager general» se
      define como «manager sin sede», así que quitarse la sede es ascender.
      Después degrada a la dueña metiéndola en un local.
- [ ] **Un encargado de local cancela la cuenta del grupo entero.**
      `meat/app.py:543` pide solo `perms.TEAM`, que tienen todos los managers.
      Sin contraseña y sin confirmación.
- [ ] **Uno se cambia su propia contraseña sin saber la anterior.**
      `meat/app.py:2322` y `2344`: el `target.id != user.id` deja pasar el caso
      «yo sobre mí mismo», que es el peligroso. Una tablet olvidada es la cuenta.
- [ ] **Una casa abre inventario en la cámara de otra.** `inventory.py:85`
      acepta el `site_id` del formulario sin comprobar de quién es. Y luego no
      ajusta nada: se cuenta la cámara entera y no se mueve un kilo.
- [ ] **Los códigos de recuperación viajan en la barra de direcciones.**
      `meat/app.py:2294`. Acaban en el historial de la tablet y en los registros
      del proxy, en claro y para siempre. Valen como segundo factor completo.
- [ ] **La edición de cocina no tiene ni un middleware de seguridad.**
      `web/app.py:36`: sin CSP, sin nosniff, sin X-Frame-Options. Y escribe
      `<script nonce="">`, que parece que hay política y no la hay.
- [ ] **La foto se lee entera en memoria antes de mirar si cabe.**
      `meat/app.py:664`. Un POST de 300 MB son 300 MB en el proceso.

## 2. La cámara sin cobertura, que es para lo que se vende esto

- [ ] **La cola da por enviado lo que no se guardó.** `base.html:963`: el
      `fetch` sigue el 303 a `/login` y devuelve 200, así que se borra el
      apunte y se canta «enviado». Igual con la casa bloqueada por impago.
- [ ] **Un solo rechazo congela la cola para siempre.** `base.html:949`. Un 403
      de CSRF —móvil compartido entre turnos— y todo lo que se apunte detrás se
      queda dentro del teléfono. La única salida es borrarlo todo.
- [ ] **La cola solo se enciende si `navigator.onLine` es falso**, que es justo
      lo que no pasa en una cámara: el punto de acceso se ve y no se llega.
- [ ] **Despiece y traslados no tienen cola.** Se guardan para abrirlos sin
      señal pero no se pueden mandar: la hoja más cara de rellenar se pierde.
- [ ] **Salir de la sesión borra las copias y no vuelven.** Desde el primer
      cierre de sesión, cada mañana sale «Sin conexión» en la cámara.
- [ ] **El ayudante espera a la red sin plazo.** Con una raya, pantalla en
      blanco indefinida teniendo la copia guardada al lado.
- [ ] **La hora del apunte se guarda y no se manda.** Lo apuntado a las 23:50 y
      mandado a las 00:10 queda fechado al día siguiente.

## 3. Lo que hace perder dinero

- [ ] **La maduración no llega al precio.** `service.py:376`: al poner el precio
      se escribe el del albarán, no el del kilo que queda. Una pieza de 10 kg
      que madura hasta 8,5 sale a 30 €/kg en vez de 35,29. **45 € por pieza.**
- [ ] **Limpiar una pieza sin precio mete recortes a cero euros**
      (`aging.py:609`) y envenena el escandallo de todos los platos que la
      lleven. El despiece sí tiene esa puerta; la limpieza no.
- [ ] **La pérdida del mes sale al doble.** `defrost.py:493`: el desvío del
      turno y el agua del descongelado son el mismo dinero y se suman dos veces.
      Y hay una prueba que garantiza el error en vez de cazarlo.
- [ ] **El Excel del POS multiplica por diez o por cien.** `pos_import.py:138`:
      convierte a texto celdas que ya venían como número y luego las pasa por el
      adivinador de separador decimal.
- [ ] **Un parte con miles en punto se lee dividido por mil, en silencio.**
      `pos_import.py:273`: el aviso que promete el comentario no se da nunca.
- [ ] **«1,5» en los kilos del inventario se guarda 15.**
      `web/templates/inventory.html:36` es `type="number"` y el navegador tira
      la coma. El servidor sabe leer comas; nunca ve ninguna.
- [ ] **«1.250» se guarda 1,25.** `_num` solo cambia coma por punto.
- [ ] **Entran 1370 kg de solomillo y 240 °C de temperatura de llegada.** No hay
      rango de proceso en ninguna parte. El registro sanitario queda completo y
      falso, que es peor que no tenerlo.
- [ ] **Los kilos salen con punto decimal en los siete idiomas.**

## 4. Fechas y trazabilidad

- [ ] **Lo que sale del arcón se queda con la fecha del congelador**
      (`defrost.py:124`), así que FEFO lo manda al final de la cola y nunca sale
      en «caduca pronto». Se sirve descongelado de la semana pasada.
- [ ] **Descongelar una pieza entera borra su única fecha** (`aging.py:388`) y
      el despiece se planta con «una fecha no se inventa», sin sitio donde
      meterla.
- [ ] **El día de trabajo sale del reloj del servidor.** `Restaurant.timezone`
      se pide, se guarda y no lo lee nadie. En Dubái, un control a la 01:30 se
      apunta el día anterior.
- [ ] **La trazabilidad no sirve para lo único que tiene que servir:** lo
      trasladado a otra sede, lo que se limpia y la venta al corte no aparecen;
      y en un despiece de varias piezas cada una se apunta el 100 %.

## 5. Formularios: lo tecleado se pierde

- [ ] **Cualquier error borra lo que acabas de escribir** en recepción, despiece,
      precios y merma. En recepción se pierde también la foto de la etiqueta.
- [ ] **Dos toques con guante crean dos apuntes.** La llave anti-duplicados
      existe y solo se genera en el camino de la cola: con cobertura está
      desconectada.
- [ ] **Recargar una pantalla de merma vuelve a apuntar los mismos kilos.** Doce
      rutas contestan al POST con HTML en vez de redirigir.
- [ ] **Un error deja la pantalla en blanco** con el título «Error 400», sin
      menú y en el idioma del navegador. En descongelado devuelve JSON crudo.
- [ ] **Un «4 C» en la temperatura tumba la recepción con un 500 en inglés** y
      se pierde el camión entero. `meat/app.py:632` está una línea por encima
      del `try` que lo capturaría.
- [ ] **Recepción se abre 1050 px por debajo del formulario** en el móvil.

## 6. Lo que se romperá con cien casas dentro

- [ ] **A una casa en marcha no le llega ninguna columna NOT NULL nueva**, ni
      las reglas UNIQUE, ni los valores nuevos de listas cerradas en PostgreSQL.
      Y la migración no dice ni una palabra.
- [ ] **El lote hijo se escribe antes de comprobar que quedan kilos:** aparecen
      kilos de la nada.
- [ ] **Un despiece de varias piezas que choca a mitad deja piezas marcadas como
      cortadas** sin cortes detrás.

---

## Ya reparado

- [x] **El reparto del coste perdía millonésimas.** Ahora en céntimos enteros
      por restos mayores (patrón Money de Fowler). `exacto.py`.
- [x] **El libro se inventaba dinero al redondear cada apunte.** Hasta medio
      euro por lote. Lo encontró esta misma auditoría contra código escrito dos
      commits antes: `cost` es un derivado y estaba donde van los importes.
- [x] **Importes con milésimas y pesos con miligramos llegaban al disco.**
      Doscientos treinta y siete en una casa de veinticinco días; ahora cero.
