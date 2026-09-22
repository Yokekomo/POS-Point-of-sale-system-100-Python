# Control de carnes — dossier para diseñar lanzamiento y estrategia

> **Para el chat que lea esto:** todo lo de abajo es verificable en el producto,
> que está construido y funcionando. Lo que **no** está decidido aparece
> marcado como **[PENDIENTE DE DECIDIR]** — no te lo inventes: pregúntamelo.
> Fecha de este dossier: septiembre de 2026.

---

## 1. Qué es, en una frase

Una aplicación web para **restaurantes y hoteles que trabajan carne de calidad**
(asadores, steakhouses, hoteles con parrilla). Controla el recorrido entero de
cada pieza —de la etiqueta del proveedor al plato vendido— y dice en euros qué
ha dejado cada una.

```
recepción → despiece → cámara → maduración/congelador → descongelado →
venta → inventario → trazabilidad
```

**No es** un software de HACCP, ni un ERP de cocina, ni un TPV. Es el control de
la carne y su trazabilidad. El HACCP lo lleva cada chef como quiere; ahí no nos
metemos, y es una decisión de producto tomada a propósito.

## 2. El problema que resuelve

Un asador compra piezas enteras a 25–45 €/kg. Entre que entra el primal y sale
el filete al pase hay pérdidas que nadie mide:

- **Maduración**: una pieza pierde 10–15 % de su peso en agua. Ese coste no
  desaparece: se queda en los kilos que quedan, y el food cost real sube sin que
  nadie lo recalcule.
- **Despiece**: de un primal salen cortes de distinto valor más recortes y
  merma. Repartir el coste de la pieza entre ellos a mano no lo hace nadie.
- **Descongelado**: lo que se saca y no se vende.
- **Merma en cámara**: lo que se echa a perder ya cortado.
- **Trazabilidad**: cuando llega la llamada del proveedor —«ese lote está
  retirado»— hay que poder decir dónde ha ido cada pieza.

Hoy eso se lleva en una libreta y en un Excel que nadie actualiza, y el resultado
es que **el food cost que cree el restaurante y el que tiene no son el mismo**.

## 3. Qué hace exactamente (lo que está construido)

### El recorrido de una pieza

| Pantalla | Qué se hace |
|---|---|
| **Recepción** | Se abre el lote (proveedor, matadero, calificación, procedencia, fecha de sacrificio, consumo preferente, halal) y se suben las piezas una a una. Cada pieza lleva **foto de su etiqueta**, su número de serie propio, kg, y cómo llegó (refrigerada/congelada, temperatura al recibir, si va directa al arcón). |
| **Precios pendientes** | El precio por kilo **lo pone dirección, no el muelle**. Hasta que no lo tiene, la pieza no se puede despiezar. El manager recibe aviso. |
| **Despiece** | El primal se convierte en cortes, recortes reutilizables y merma. El coste de la pieza se reparte entre los cortes por índice de valor (un filete vale más por kilo que un recorte; la merma no paga nada). Cada corte sale con su serial y entra en cámara con su coste. |
| **Piezas enteras** | Maduración y congelador: mover, pesar (conteo diario del agua perdida), limpiar (costra y grasa, con lo que se aprovecha y lo que se tira), vender al peso. |
| **Cámara** | Lo que hay ahora: cortes y primales sin despiezar, mínimos y caducidades. |
| **Descongelado** | Lo que sale a descongelar y el recuento de cierre del turno. La diferencia es lo vendido de verdad. |
| **Merma** | Lo que se echa a perder ya cortado. Su coste lo absorbe lo que queda del lote. |
| **Traslados** | Del obrador a los locales, con el número y el coste de la pieza. |
| **Inventario** | Conteo pieza a pieza por número de serie; re-ancla el stock. Dos personas pueden contar a la vez sobre la misma hoja. |
| **Trazabilidad** | Metes un número de serie y sale todo: su etiqueta, de dónde viene, cada corte que salió, su rendimiento, su merma, dónde ha ido cada uno (incluso reusado en hamburguesa o tartar) y —solo para dirección— lo que dejó. |
| **Parte del día** | La hoja para colgar en el pase, lista para imprimir. |
| **Carta** | Cada plato atado a su artículo del TPV, con sus gramos; de ahí salen el food cost y el descuento de cámara. |

### Cómo está pensado para la cocina real

- **Funciona sin cobertura.** Dentro de una cámara no hay señal. Las pantallas de
  trabajo se guardan en el móvil y lo que se escribe se manda solo cuando vuelve
  la señal. Nada se pierde.
- **Móvil primero.** Una pieza por pantalla, campos grandes, botones que caben en
  un pulgar con guante. Nada de tablas de ocho columnas.
- **Tres niveles de acceso**: manager (todo, incluido el dinero), carnicero (la
  carne entera, **sin ver el dinero**) y ayudante (mete datos del día y ve el
  stock). El dinero no se filtra nunca a quien no debe verlo, y hay pruebas que
  lo verifican.
- **Obrador y locales.** Un grupo puede tener un obrador que despieza y locales
  que consumen. Cada local pesa lo suyo y cuenta lo suyo.
- **Siete idiomas**: español, inglés, francés, alemán, neerlandés, árabe (de
  derecha a izquierda) y húngaro.
- **Moneda configurable** (19), sin conversión entre ellas a propósito.
- **Hojas de Excel imprimibles** para rellenar a mano cuando se cae todo.

## 4. Estado real del producto

**Está construido y funcionando**, no es una maqueta.

- ~25.000 líneas de Python y ~6.400 de plantillas.
- **866 pruebas automáticas** pasando, en 41 ficheros: dominio, permisos,
  concurrencia (dos personas sobre lo mismo), velocidad con tres años de datos
  dentro, y navegador real (móvil, tablet, sin cobertura).
- **Banco de pruebas sintético** que genera 50 casas × 30 días, audita
  invariantes contables y prueba operaciones ilegales.
- **Velocidad medida con medio año de trabajo dentro**: portada 31 ms,
  maduración 29 ms, parte del día 35 ms, cámara 21 ms. Puesto como prueba para
  que no se degrade.
- **Repaso de seguridad contra OWASP Top 10 (2025)**: control de acceso
  verificado en la ruta, CSP con número por respuesta, CSRF, freno a fuerza
  bruta, sin enumeración de usuarios (mismo error y mismo tiempo), verificación
  en dos pasos, datos de contacto cifrados en reposo.
- **Cumplimiento europeo**: datos de contacto cifrados, purga automática de
  solicitudes, página de cookies (solo técnicas), la web pública **no carga nada
  de fuera** (ni una tipografía).

### Lo que NO está hecho (importante para no prometerlo)

1. **Integración en vivo con TPV.** Hoy las ventas se meten a mano o por
   importación; el emparejamiento plato ↔ artículo del TPV existe, pero **no hay
   conector automático a ningún TPV concreto**. Esto es probablemente el mayor
   trabajo pendiente y el que más pesa en la venta.
2. **Etiqueta impresa del corte** (serial, lote de origen, fecha de despiece, QR
   a su trazabilidad) para pegar en la bolsa. Sin ella el FEFO en cámara se hace
   de memoria.
3. **Retirada de lote hacia delante**: hoy se pregunta de dónde viene una pieza;
   la llamada real es la contraria, «este lote está retirado, ¿dónde ha ido?».
4. **Traslados en camino** (saber qué va en la furgoneta y qué llegó de verdad).
5. **Cobros**: la pasarela (Stripe) está prevista en el modelo de datos pero
   **no conectada**.
6. **Multiproceso**: arranca un solo proceso a propósito (los frenos de
   seguridad viven en memoria). Hay que moverlos a Redis antes de escalar
   horizontalmente. Es un cambio pequeño, pero va antes de crecer.

## 5. Modelo de negocio (lo que hay montado en el producto)

- **Suscripción mensual por local.** Dos planes en el código: `SINGLE` (un
  local) y `MULTI` (varios locales bajo la misma cuenta).
- **15 días de prueba**, que arrancan **al poner el método de pago**. Si se
  cancela antes de terminar la prueba, no se paga nada.
- **Nadie se registra solo.** El alta es con venta de por medio: la casa deja
  una solicitud en la web (restaurante, número fiscal, dirección, persona a
  cargo, cuántos cocineros, cuántos locales) y la plataforma da de alta la
  cuenta. Está construido así a propósito: es un producto de venta consultiva.
- **Si el recibo falla**: primero avisa y se sigue trabajando; si se bloquea, se
  para la casa entera. El reloj de la prueba y el estado del recibo **solo los
  ven el manager y la plataforma**, nunca la cocina.
- **El precio no está puesto.** La web pública dice «consultar».
  **[PENDIENTE DE DECIDIR: precio por local y mes, y si el plan MULTI es por
  local o tarifa de grupo.]**

## 6. Qué hace falta en hosting e infraestructura

Esto no es una red social: es una herramienta que usan unas decenas de personas
por restaurante, en horas de cocina. Un turno completo (recepción, despiece,
veinte recuentos, ventas y cierre) son unos cientos de peticiones.

### Para empezar — entre 20 y 40 € al mes

| Pieza | Qué poner | Por qué |
|---|---|---|
| **Servidor** | VPS de 2 vCPU y 4 GB en **Hetzner** (Falkenstein/Helsinki) u **OVH** | 5–15 €/mes y sobra. Cien restaurantes trabajando a la vez lo mueve sin despeinarse. **En Europa**, que es lo que importa. |
| **Base de datos** | **PostgreSQL gestionado** (Neon, Supabase o el del proveedor), región europea | Para que las copias y el punto de restauración no dependan de que te acuerdes tú. |
| **Delante** | **Cloudflare** plan gratuito | TLS, caché de lo estático, protección contra saturación y cortafuegos de aplicación sin tocar el servidor. Hace que la web de venta sea rápida en todo el mundo desde el día uno. |
| **Correo** | **Postmark**, **Resend** o **Amazon SES** | Un servidor de correo propio acaba en spam. |
| **Cobros** | **Stripe** | Guarda las tarjetas él (está certificado para eso) y avisa de recibos devueltos. |
| **Fotos** | El disco del servidor; **S3/R2** cuando crezca | De momento no hace falta. |

El despliegue ya está resuelto: `docker compose up -d --build` con Caddy
delante, que saca y renueva el certificado solo. Hay un `docker-compose.yml`,
un `Dockerfile` y un `Caddyfile` en el repositorio, y un documento de despliegue
con el procedimiento completo.

### Qué hay que dejar montado **antes** de abrir

- **Copias diarias de la base de datos y una restauración probada.** Una copia
  que nunca se ha restaurado no es una copia.
- **La clave de cifrado (`GRILL_DATA_KEY`) guardada aparte.** Si se pierde, los
  datos de contacto cifrados no se recuperan.
- **Purga de solicitudes en un cron** (datos personales).
- **Vigilancia** que avise si la web deja de responder (Uptime Kuma o similar).
- **Contratos de encargado del tratamiento** firmados con hosting, correo y
  pasarela. Los tres los tienen hechos: es descargar y guardar.

### Sobre «a nivel mundial»

Dos cosas que se confunden:

- **Que la web de venta se vea rápida en todo el mundo**: lo resuelve la CDN.
  Gratis y desde el día uno.
- **Que la aplicación responda rápido en todo el mundo**: la base de datos vive
  en un sitio. Dubái contra Frankfurt son ~120 ms (se trabaja bien); Sídney,
  ~280 ms (empieza a molestar).

**Recomendación: no montar multirregión hasta que un cliente lo pida.** Cuando
llegue, el camino es una región por zona (Europa/América/Asia), con cada
restaurante entero en la suya — que además es lo que mejor se lleva con
protección de datos.

### Protección de datos

Si hay clientes europeos, **la base de datos va en la Unión Europea**. No es
preferencia técnica: nombre, correo, teléfono, dirección y número fiscal son
datos personales, y sacarlos de la UE obliga a papeleo que no quieres.

## 7. Quién lo hace

**[PENDIENTE DE DECIDIR / RELLENAR POR MÍ]**

- Soy director culinario (control de HACCP, FEFO, FIFO y números). El producto
  sale de saber cómo se trabaja la carne en una cocina de verdad, no de mirar el
  mercado desde fuera.
- **Pendiente de decir**: si estoy solo o con socios; si hay capital; si hay
  algún restaurante dispuesto a ser el primer cliente; cuánto tiempo puedo
  dedicarle a la semana; si busco inversión o quiero que se pague solo.

## 8. Lo que necesito que me diseñes

1. **Posicionamiento y mensaje.** Cómo contar esto a un dueño de asador en una
   frase. Qué número le hace levantar la ceja.
2. **Segmento inicial.** Por dónde empezar: asadores independientes, pequeños
   grupos, hoteles. Y por qué ese y no otro.
3. **Precio.** Qué cobrar por local y mes, cómo tratar al grupo con varios
   locales, y cómo justificarlo contra lo que se ahorra.
4. **Plan de lanzamiento.** Primeros clientes, cómo conseguirlos, qué darles a
   cambio de ser los primeros, y qué hito marca que funciona.
5. **Qué construir antes de vender y qué puede esperar.** Mi intuición es que la
   integración con TPV y la etiqueta impresa son las dos que más pesan, pero
   dímelo tú.
6. **Riesgos.** Qué puede matar esto y qué haría falta para verlo venir.

### Lo que NO quiero que hagas

- **No te inventes cifras de mercado, competidores ni precios de nadie.** Si te
  hacen falta, dime qué hay que averiguar y cómo.
- **No des por hechas funciones que están en la lista de "lo que NO está
  hecho".** Si una estrategia depende de ellas, dilo explícitamente.
- No me des un plan genérico de SaaS. Este producto tiene una forma concreta —se
  vende con venta consultiva, se usa con guantes y sin cobertura, y el que paga
  no es el que lo usa— y el plan tiene que tenerlo en cuenta.
