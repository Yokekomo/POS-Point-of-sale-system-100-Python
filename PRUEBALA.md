# Probar el control de carnes

Una demo con **un mes de trabajo ya dentro**: dos casas —un grupo con obrador y
dos locales, y un asador de un solo local—, cinco personas en cada una, piezas
recibidas, despieces, carne madurando, traslados, ventas, merma e inventario
cerrado. No hay que escribir nada para empezar a mirar.

## Lo más corto: un icono en el Escritorio

Si no quieres saber nada de carpetas ni de consolas, **copia esta línea**, pega
en la consola de Windows (tecla Windows, escribe `cmd`, Intro) y dale a Intro:

```
powershell -c "$d=[Environment]::GetFolderPath('Desktop'); iwr 'https://raw.githubusercontent.com/Yokekomo/POS-Point-of-sale-system-100-Python/claude/cool-bohr-jct64l/carnes.bat' -OutFile (Join-Path $d 'carnes.bat'); explorer $d"
```

Te deja un **`carnes.bat` en el Escritorio** y te abre la carpeta para que lo
veas. A partir de ahí, doble clic y ya: se baja el programa la primera vez, se
pone al día las siguientes y lo arranca. Da igual desde dónde esté el fichero
—Escritorio, Descargas, donde sea—: él sabe adónde ir.

Se pregunta al propio Windows dónde está tu Escritorio en vez de darlo por
supuesto: con OneDrive no es `C:\Users\TuNombre\Desktop`, y un fichero
guardado ahí no aparece por ningún lado.

Y cuando le des doble clic: **espera y no abras el navegador**. La primera vez
tarda un par de minutos montando el mes de trabajo, y hasta que no acaba el
navegador solo sabe decir «no se puede acceder a este sitio web». **Se abre
sola** cuando está lista.

Solo hace falta tener **git** (una vez, desde <https://git-scm.com/download/win>,
siguiente-siguiente) y **Python** (desde <https://www.python.org/downloads/>,
marcando **«Add python.exe to PATH»** al instalarlo). Si falta alguno, el
propio `carnes.bat` te lo dice.

> **Lo que suele fallar:** escribir `python -m thegrill.cli ...` desde
> `C:\Users\TuNombre`. Ahí no está el programa, así que Python contesta
> `No module named 'thegrill'`. Hay que estar **dentro de su carpeta** — o usar
> el `carnes.bat` de arriba, que se encarga él.

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

## Traerte la última versión

Los arreglos salen casi a diario. Para ponerte al día:

- **Doble clic en `actualizar.bat`** (o `git pull` escrito **dentro de la
  carpeta del programa**, no en `C:\Users\TuNombre`).
- Si bajaste el ZIP no hay nada que actualizar: baja el ZIP otra vez. Tus datos
  están en `demo.db`; copia ese fichero a la carpeta nueva y sigues donde
  estabas.

Para encontrar dónde la tienes, en la consola:

```
dir /s /b /ad C:\Users\%USERNAME%\POS-Point-of-sale-system*
```

y luego `cd /d` con la ruta que salga.

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

## Probar lo último: dos personas a la vez

Esto se ve mejor con **dos ventanas abiertas a la vez**, cada una con una
persona distinta. En el mismo navegador no vale —la sesión es la misma—, así
que: una ventana normal y otra **de incógnito** (Ctrl+Mayús+N en Chrome,
Ctrl+Mayús+P en Firefox). O mejor todavía: el ordenador y el móvil.

1. **Contar la cámara entre dos.** Entra como Ana (`ana0@demo.com`) en una
   ventana y como Paco (`paco0@demo.com`) en la otra. Ana abre el inventario en
   **Inventario → Abrir**. Ahora los dos tenéis la misma hoja.
   - Que cada uno escriba piezas distintas y guarde: no se pierde ninguna, y en
     la columna **Quién** se ve de quién es cada número y a qué hora.
   - Ahora escribid **la misma pieza con pesos distintos**. Manda el último,
     pero la línea sale marcada **en discusión** con el otro número y el nombre
     de quien lo puso. Al cerrar, el aviso las nombra.
2. **Cerrar el inventario entre dos.** Con la hoja abierta en las dos ventanas,
   dadle a **Cerrar** casi a la vez. Lo cierra uno; al otro le sale «otra
   persona acaba de cerrar este inventario». Antes los dos escribían el mismo
   ajuste y el mes salía el doble de malo de lo que fue.
3. **Despiezar la misma pieza.** Ana y Paco abren **Despiece**, los dos eligen
   la misma pieza y le dan a guardar. Sale un despiece, no dos: al segundo se
   le dice que mire el que ya está hecho. Y si los dos traían el mismo número
   de despiece, el segundo se corre solo al siguiente libre y no pierde nada de
   lo escrito.
4. **Mandar la misma pieza a dos sitios.** En **Traslados**, que Ana la mande a
   la Playa y Paco a la Sierra a la vez: sale un albarán, y la pieza está donde
   dice el papel.

## Y que va rápida

La demo trae un mes dentro, pero las pantallas están hechas para que la casa
que lleva tres años vaya igual: se pide lo de hoy, no toda la historia. La
portada pasó de 199 ms a 31, la cámara de 376 a 29 y el parte de 364 a 35.

No hay nada que tocar para verlo: se nota al abrir. Y si alguna vez se pusiera
lenta, la prueba `tests/test_speed.py` lo dice antes de que llegue a una cocina.

## Lo que esto no es

Los datos son de mentira, las contraseñas están escritas aquí y va por `http`
sin cifrar. Para trabajar de verdad está `docker-compose.yml`, con su base de
datos, su dominio y su certificado: el detalle está en
**[DESPLIEGUE.md](DESPLIEGUE.md)**.
