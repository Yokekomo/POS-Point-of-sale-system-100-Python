# Probar el control de carnes

Una demo con **un mes de trabajo ya dentro**: dos casas —un grupo con obrador y
dos locales, y un asador de un solo local—, cinco personas en cada una, piezas
recibidas, despieces, carne madurando, traslados, ventas, merma e inventario
cerrado. No hay que escribir nada para empezar a mirar.

## Antes de nada: bajar el programa

El programa no está en tu ordenador hasta que lo bajas, y hay que **estar
dentro de su carpeta** para arrancarlo. Desde `C:\Users\TuNombre` no funciona:
Python contesta `No module named 'thegrill'`, que quiere decir «aquí no hay
ningún programa que se llame así».

**Sin saber de git**, lo más corto: entra en
<https://github.com/Yokekomo/POS-Point-of-sale-system-100-Python/tree/claude/cool-bohr-jct64l>,
botón verde **Code → Download ZIP**, y descomprímelo (por ejemplo en
`C:\carnes`). Queda una carpeta con el nombre largo dentro.

**Con git**, en la consola:

```
git clone https://github.com/Yokekomo/POS-Point-of-sale-system-100-Python.git
cd POS-Point-of-sale-system-100-Python
git checkout claude/cool-bohr-jct64l
```

## En Windows, sin pelearse

Dentro de la carpeta del programa hay un **`probar.bat`**. Doble clic y ya:
prepara lo que haga falta y arranca la demo. La primera vez tarda un par de
minutos; las siguientes, segundos.

Si prefieres la consola, en esa misma carpeta:

```
python -m pip install -r requirements.txt
python -m thegrill.cli --db sqlite:///demo.db demo
```

Con Python 3.13, instala con **`requirements.txt`** y no con `requirements.lock`:
el `lock` fija versiones exactas y alguna es anterior a tu Python.

## En el ordenador, con Python (Mac o Linux)

Hace falta Python 3.11 o más nuevo, y estar dentro de la carpeta del programa.

```bash
pip install -r requirements.txt
python -m thegrill.cli --db sqlite:///demo.db demo
```

Al arrancar escribe las claves y las direcciones. Se abre en
**http://127.0.0.1:8001** y se para con Ctrl+C.

Para empezar de cero en cualquier momento:

```bash
python -m thegrill.cli --db sqlite:///demo.db demo --reiniciar
```

## En el ordenador, con Docker

Si tienes Docker, no hace falta ni Python:

```bash
docker compose -f docker-compose.demo.yml up --build
```

Igual: **http://localhost:8001**, y las claves salen en el terminal.

## Desde el móvil o la tablet

La demo escucha en toda la red de casa, así que desde el móvil **conectado a la
misma wifi** se abre con la dirección de ese ordenador y `:8001` detrás —
`http://192.168.1.40:8001`, por ejemplo—. El propio arranque la escribe.

Es la forma de verlo como lo va a ver un carnicero: con el teléfono en la mano.

## Con quién entrar

Todas las cuentas llevan la misma contraseña: **`demo-2026`**. Lo que cambia es
lo que ve cada una, que es la mitad de la gracia:

| Quién | Correo | Qué verás |
|---|---|---|
| Manager del grupo | `ana0@demo.com` | Todo: el dinero, la carta, el equipo, las sedes |
| Segundo manager | `marta0@demo.com` | Lo mismo: una casa no la lleva una sola persona |
| Carnicero del obrador | `paco0@demo.com` | La carne entera, sin el dinero |
| Carnicera de la Playa | `eva0@demo.com` | Solo lo de su local |
| Ayudante de la Sierra | `leo0@demo.com` | Mete datos del día y ve el stock |
| Asador de un solo local | `ana1@demo.com` | Cómo se ve sin sedes, que es lo normal |
| Dueño de la plataforma | `dueno@plataforma.com` | Las casas, el recibo y los fallos contados |

## Por dónde empezar a mirar

1. **Hoy** — lo que está pendiente. Empieza por aquí.
2. **Cámara** — lo que queda, y aparte los kilos congelados, que no se venden.
3. **Maduración** — el conteo de la noche: cada pieza pierde agua y el kilo que
   queda vale más. Pesa una y mira cómo sube su precio por kilo.
4. **Traslados** (entra como Ana del grupo) — manda una pieza a la Playa y
   luego entra como Eva: la tiene ella, y el obrador ya no.
5. **Ventas** — mete unas ventas a mano y mira cómo bajan los kilos por su
   número, nunca de lo congelado ni de la carne de otra sede.
6. **Parte del día** — la hoja para el pase, lista para imprimir o guardar en
   PDF desde el navegador.
7. **Contar un fallo** — está en todas las pantallas. Cuenta uno y míralo
   después entrando como dueño de la plataforma.

## Lo que esto no es

Los datos son de mentira, las contraseñas están escritas aquí y va por `http`
sin cifrar. Para trabajar de verdad está `docker-compose.yml`, con su base de
datos, su dominio y su certificado: el detalle está en
**[DESPLIEGUE.md](DESPLIEGUE.md)**.
