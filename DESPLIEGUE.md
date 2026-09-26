# Poner esto en internet

Dos preguntas, contestadas por orden: **cómo montar un entorno de prueba de
verdad** y **qué hosting hace falta para servir esto en todo el mundo**.

## Entorno de prueba en veinte minutos

Hace falta una máquina con Docker y un dominio apuntando a ella. Nada más.

```bash
git clone <este repositorio> carnes && cd carnes
cp .env.example .env
$EDITOR .env                       # dominio, contraseña de la base y clave de cifrado
docker compose up -d --build

# el dueño de la plataforma, solo la primera vez
# Comillas simples alrededor de todo: `$GRILL_DB` tiene que expandirla el
# contenedor, que es quien la conoce. Con comillas dobles la expande el shell
# del host, donde esa variable no existe, y el comando recibe `--db ""`.
docker compose exec app sh -c 'python -m thegrill.cli \
    --db "$GRILL_DB" crear-dueno \
    --email tu@correo.com --nombre "Tu nombre" --password "una-clave-larga"'
```

Y ya está: `https://tu-dominio` sirve la web de venta con su certificado, y
`/admin` es tu consola. Caddy saca y renueva el certificado solo; la aplicación
detecta que va detrás de HTTPS por la cabecera que le pasa el proxy, y solo
entonces marca la cookie como segura y manda HSTS.

Para probarlo sin dominio, en tu portátil:

```bash
pip install -r requirements.lock      # las versiones exactas, las probadas
python -m thegrill.cli --db sqlite:///carnes.db crear-dueno \
    --email tu@correo.com --nombre "Tú" --password "una-clave-larga"
GRILL_INSECURE_COOKIE=1 python -m thegrill.cli --db sqlite:///carnes.db \
    serve-carne --port 8001
```

`GRILL_INSECURE_COOKIE=1` **es solo para desarrollo**: sin él la cookie de
sesión exige HTTPS y en `http://localhost` no habría manera de entrar.

## Qué hosting hace falta

La respuesta corta: **una máquina pequeña en Europa y una CDN delante**. Esto no
es una red social; es una herramienta de trabajo que usan unas decenas de
personas por restaurante, en horas de cocina.

### Lo que consume de verdad

Cada apunte es una pantalla y unas pocas escrituras. Un turno completo de un
restaurante —recepción, despiece, veinte recuentos, el parte de ventas y el
cierre— son unos cientos de peticiones. Cien restaurantes trabajando a la vez
siguen siendo un tráfico que **un servidor de 2 vCPU y 4 GB mueve sin
despeinarse**. Lo que crece no es la CPU: es la base de datos y las copias.

### Lo que yo pondría, por orden

| | Qué | Por qué |
|---|---|---|
| Servidor | Un VPS de 2 vCPU y 4 GB en **Hetzner** (Falkenstein o Helsinki) o **OVH** | Cuesta entre 5 y 15 € al mes y sobra para empezar. En Europa, que es lo que importa. |
| Base de datos | **PostgreSQL gestionado** —Neon, Supabase o el de tu proveedor— en región europea | Que las copias y el punto de restauración no dependan de que te acuerdes tú. |
| Delante | **Cloudflare** en plan gratuito | TLS, caché de lo estático, protección contra saturación y un cortafuegos de aplicación sin tocar el servidor. |

**Lo estático ya viene preparado para esa caché.** El estilo y los guiones se
sirven en `/estatico/<nombre>.<huella>.css`, con la huella de su propio
contenido en el nombre y `Cache-Control: public, max-age=31536000, immutable`.
No hay que configurar nada delante: Cloudflare —o cualquier proxy— los guarda
y deja de preguntar. Y como la huella cambia con el contenido, un despliegue
nuevo estrena nombre y llega a todo el mundo sin purgar caché a mano.

Lo que **no** hay que hacer es poner una regla que cachee el HTML: ahí va lo de
cada casa, y con sesión dentro.
| Correo | **Postmark**, **Resend** o **Amazon SES** | Un servidor propio de correo acaba en la carpeta de spam. Estos entregan. |
| Cobros | **Stripe** | Guarda las tarjetas él, que para eso está certificado, y manda el aviso de recibo devuelto. |
| Fotos y ficheros | El disco del servidor, o **S3/R2** cuando crezca | De momento no hace falta. |

**Cuánto ocupa una foto de etiqueta.** Al subirla se endereza, se le quita el
EXIF —con el GPS de quien la hizo— y se deja en 2400 px de lado largo y
calidad 80: de los dos megas que manda un móvil quedan unos 250 kB. No es por
ahorrar disco: es que lo que hay que leer de una etiqueta térmica cabe ahí, y
lo que se tira es grano de sensor. Está medido leyendo las etiquetas después
con OCR y con dos lectores de código de barras; por debajo de 1600 px se
empieza a perder el lote, y a 1200 la foto ya no sirve de prueba. Por eso esos
números no se tocan sin volver a medir.

**Y HEIC no se acepta.** Una foto de iPhone subida desde la galería no la abre
ni el servidor, ni la descarga de datos del cliente, ni el ordenador de un
inspector. Se rechaza al subirla y se pide repetirla, que con la pieza delante
son diez segundos; descubrirlo dos años después, en una inspección, no tiene
arreglo. Los iPhone de ahora, al hacer la foto desde la propia pantalla, ya
mandan JPEG.

Con eso: **entre 20 y 40 € al mes** para empezar, subiendo con los clientes y no
antes.

### «A nivel mundial»

Conviene separar dos cosas que suelen confundirse:

**Que se vea rápido en todo el mundo.** Eso lo resuelve la CDN: Cloudflare tiene
presencia en cientos de ciudades y cachea la web de venta —que es HTML estático,
sin nada de fuera— a milisegundos de cualquiera. Esa parte ya es mundial el día
uno, y gratis.

**Que la aplicación responda rápido en todo el mundo.** Eso es otra cosa, porque
cada pantalla lee y escribe en la base de datos, y **la base de datos vive en un
sitio**. Un cocinero en Dubái contra una base en Frankfurt tiene unos 120 ms de
ida y vuelta: se nota un poco, pero se trabaja perfectamente. Contra Sídney ya
son 280 ms y empieza a molestar.

Mi consejo, y va en serio: **no montes multirregión hasta que un cliente te lo
pida**. Repartir la base de datos por el mundo multiplica el coste y las formas
de romperse —replicación, conflictos, copias que no cuadran— a cambio de unos
milisegundos que a una cocina no le cambian el día. Cuando llegue ese cliente,
el camino es:

1. **Una región por zona**: Europa en Frankfurt, América en Virginia, Asia en
   Singapur. Cada restaurante vive entero en la suya. Es lo más simple y lo que
   mejor se lleva con la protección de datos.
2. **Réplicas de lectura** con las escrituras en una sola región, si prefieres
   una instalación única. Funciona, pero hay que tener cuidado con leer justo
   después de escribir.

La opción 1 es la buena para este producto. Un restaurante no consulta los datos
de otro continente: consulta los suyos.

### Protección de datos, que aquí manda

Si vas a tener clientes europeos, **la base de datos va en la Unión Europea**.
No es una preferencia técnica: los datos de las solicitudes —nombre, correo,
teléfono, dirección, número fiscal— son datos personales, y sacarlos de la UE
obliga a papeleo que no quieres. Hetzner, OVH, Scaleway y las regiones europeas
de los grandes valen; una región de Estados Unidos te complica la vida.

Y con el proveedor de hosting, el de correo y la pasarela hay que firmar su
contrato de encargado del tratamiento. Lo tienen hecho los tres; es descargarlo
y guardarlo.

### Lo que hay que dejar montado antes de abrir

- **Copias de seguridad diarias.** Ya vienen montadas: el servicio `copias` de
  `docker-compose.yml` hace una al arrancar y otra cada veinticuatro horas, y
  guarda las treinta últimas (`COPIAS_GUARDAR` en el `.env` lo cambia). Cada
  copia se lleva la base **y las fotos de las etiquetas**, que están en otro
  volumen y son la prueba de qué matadero y qué lote traía cada pieza.

  **Cómo queda la carpeta `/copias`, y por qué importa:**
  ```
  /copias/carnes-20260926-030000.tar.gz   ← un paquete por día (la base)
  /copias/carnes-20260925-030000.tar.gz
  /copias/fotos/                          ← el almacén, UNA sola copia de cada foto
  ```
  Las fotos no van dentro de los paquetes. Iban, y era un error caro: cada
  paquete se llevaba la carpeta entera y se guardan treinta, así que la misma
  foto vivía **treinta y una veces** en disco. Y sin ganar nada al comprimir,
  porque un JPEG ya viene comprimido —meterlo en el `.tar.gz` ahorra un 0,03 %,
  medido—. Ahora cada foto se guarda una vez en `/copias/fotos` y cada paquete
  lleva la **lista** de las que había ese día, así que volver a una copia sigue
  poniendo exactamente las de aquel día. Del almacén no se borra nada, ni
  cuando la foto desaparece de `/app/subidas`.

  > **El paquete ya no se basta solo.** Para llevarse una copia fuera hay que
  > llevarse el paquete **y** la carpeta `/copias/fotos`. Si restauras un
  > paquete sin su almacén, el programa se para y lo dice; no vuelve en
  > silencio con las etiquetas en blanco.

  Ver qué copias hay y de cuándo son:
  ```
  docker compose exec app ls -lh /copias
  ```
  A mano, sin esperar a mañana:
  ```
  docker compose exec app sh -c 'python -m thegrill.cli --db "$GRILL_DB" \
      copia --a /copias --fotos /app/subidas'
  ```
  **Y volver de una, que es lo que de verdad hay que haber probado.** Sin `--si`
  no toca nada: enseña de cuándo es la copia y cuántas casas y cuántas piezas
  trae, y se para. Con `--si` machaca la base y las fotos que haya ahora:
  ```
  docker compose exec app sh -c 'python -m thegrill.cli --db "$GRILL_DB" \
      restaurar /copias/carnes-AAAAMMDD-HHMMSS.tar.gz --fotos /app/subidas'
  docker compose restart app     # después de restaurar con --si
  ```
  **Prueba la vuelta antes de abrir al público, y repítela cada pocos meses.**
  Una copia que nunca se ha restaurado no es una copia: es un fichero que ocupa
  sitio y tranquiliza. Lo que sí está probado en cada despliegue es el código
  que las hace y las restaura (`tests/test_copia.py` borra una casa entera,
  vuelve de la copia y comprueba que la carne, los nombres y las fotos están);
  lo que solo puedes probar tú es tu servidor.

  **Y sácalas de esa máquina.** El volumen `copias` vive en el mismo disco que
  la base: sirve para volver de un borrado o de una actualización que salió
  mal, y **no sirve para nada si lo que se pierde es la máquina**. Copia esa
  carpeta a otro sitio —otro proveedor, un disco de casa— con lo que tengas a
  mano (`rsync`, `rclone`, `restic`). **La carpeta entera**, paquetes y
  `fotos/`: es justo la forma que mejor le va a un `rsync`, porque de un día
  para otro lo único nuevo son las fotos de ese día y un paquete pequeño.
- **La clave `GRILL_DATA_KEY` guardada aparte.** Si se pierde, los datos de
  contacto cifrados no se recuperan.
- **La persona que deja la casa.** En `Equipo`, cuando alguien está de baja
  aparece «Borrar sus datos»: se le quitan el correo, la contraseña, el segundo
  factor y los códigos de repuesto, y se queda su nombre y todo su trabajo. Esa
  raya no es un criterio nuestro: el (UE) 931/2011 y el (CE) 852/2004 obligan a
  conservar quién hizo qué con cada pieza, y el RGPD —artículo 17.3.b— excluye
  del derecho de supresión justo lo que hay que conservar por ley. Queda escrito
  quién lo hizo y cuándo. **Díselo a tus clientes**: es lo que tienen que poder
  contestar el día que se lo pida un empleado suyo.
- **Purga de solicitudes en un cron**, para no guardar datos personales de más:
  ```
  0 4 * * *  docker compose exec -T app sh -c 'python -m thegrill.cli --db "$GRILL_DB" purgar-solicitudes'
  ```
- **Vigilancia**: que alguien te avise si la web deja de responder. Uptime
  Kuma en la misma máquina, o el comprobador gratuito de cualquiera.

### Un aviso sobre crecer

La plataforma arranca **un solo proceso a propósito**. Los frenos que paran los
intentos de contraseña y el formulario público viven en memoria: con varios
procesos, cada uno llevaría su propia cuenta y el freno valdría la mitad. Para
crecer, primero más máquinas detrás del balanceador con esos frenos en un sitio
compartido —Redis, o la propia base de datos—, y solo después más procesos por
máquina. Está anotado como pendiente y es un cambio pequeño; pero conviene
hacerlo **antes** de multiplicar procesos, no después.
