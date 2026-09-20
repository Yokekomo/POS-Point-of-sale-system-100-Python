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
docker compose exec app python -m thegrill.cli \
    --db "$GRILL_DB" crear-dueno \
    --email tu@correo.com --nombre "Tu nombre" --password "una-clave-larga"
```

Y ya está: `https://tu-dominio` sirve la web de venta con su certificado, y
`/admin` es tu consola. Caddy saca y renueva el certificado solo; la aplicación
detecta que va detrás de HTTPS por la cabecera que le pasa el proxy, y solo
entonces marca la cookie como segura y manda HSTS.

Para probarlo sin dominio, en tu portátil:

```bash
pip install -r requirements.txt
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
| Correo | **Postmark**, **Resend** o **Amazon SES** | Un servidor propio de correo acaba en la carpeta de spam. Estos entregan. |
| Cobros | **Stripe** | Guarda las tarjetas él, que para eso está certificado, y manda el aviso de recibo devuelto. |
| Fotos y ficheros | El disco del servidor, o **S3/R2** cuando crezca | De momento no hace falta. |

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

- **Copias de seguridad diarias de la base de datos, y una restauración
  probada.** Una copia que nunca se ha restaurado no es una copia.
- **La clave `GRILL_DATA_KEY` guardada aparte.** Si se pierde, los datos de
  contacto cifrados no se recuperan.
- **Purga de solicitudes en un cron**, para no guardar datos personales de más:
  ```
  0 4 * * *  docker compose exec -T app python -m thegrill.cli --db "$GRILL_DB" purgar-solicitudes
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
