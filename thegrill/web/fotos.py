"""[01838] La foto de la etiqueta, dejada como hay que guardarla.

La etiqueta de un primal es la prueba: qué matadero, qué lote, qué fecha de
sacrificio y qué peso traía la pieza. Se hace con el móvil, en la cámara, con
el guante puesto, y hasta hoy se guardaba **tal cual llegaba**: el fichero del
teléfono, byte por byte, hasta doce megas.

Eso trae tres problemas, y ninguno se ve el día que pasa.

**Pesa por nada.** Una foto de móvil son dos megas, y de esos dos megas lo que
se lee —las letras de una etiqueta térmica— cabe en doscientos cincuenta kilos.
El resto es grano de sensor. Y no es solo disco: esa foto entra en la copia de
seguridad y sale en la descarga de datos del cliente.

**Puede venir torcida.** Un móvil en vertical guarda los píxeles tumbados y
apunta en una etiqueta que hay que girarlos. Si esa etiqueta se pierde por el
camino —y se pierde en cuanto alguien toca el fichero—, la prueba queda de
lado para siempre.

**Y trae dónde estaba esa persona.** El EXIF de un móvil lleva GPS con su
fecha y su hora. Eso es un dato personal de un empleado, no dice nada de la
carne, y se estaba guardando dos años y en cada copia.

Las tres se arreglan de una vez abriendo la foto, aplicando el giro a los
píxeles, tirando el EXIF entero y volviendo a guardarla del tamaño que hace
falta para leerla. Los números de abajo no son de oído: están medidos contra
etiquetas fotografiadas a propósito, leyéndolas después con OCR y con dos
lectores de código de barras.
"""
from __future__ import annotations

import io

# Un lado largo de 2400 px. Medido: entre 2000 y 3000 px la etiqueta se lee
# igual de bien que en una copia casi sin pérdida; a 1600 aguanta; a 1200 está
# muerta —uno de cada tres caracteres mal— y ahí un lote «25-0917-4412-A» ya no
# se distingue de «25-0917-4412-4». Se elige 2400 y no 2000 por margen: el
# borde está en 1200, y esto deja el doble. Los ochenta kilobytes que cuesta
# ese margen son el seguro más barato de todo el programa.
LADO_MAX = 2400

# Calidad 80. Medido: entre 92 y 75 no hay diferencia que se pueda medir —el
# error de lectura se queda en el 10-11 % en todos, que es lo que ya se había
# comido el grano antes de comprimir nada—, y de 92 a 80 se ahorran trescientos
# kilobytes por foto. 80 deja un escalón por encima del borde.
CALIDAD = 80

# Color a la mitad (4:2:0). Medido dos veces, la segunda con una etiqueta que
# lleva una línea roja de alérgenos y otra azul de congelado: el color entero
# cuesta un 28 % más y se lee exactamente igual.
SUBMUESTREO = 2

# Un freno antes de abrir nada. Un PNG de cien kilobytes puede desplegarse en
# mil millones de píxeles al abrirlo —está hecho a propósito para eso—, y el
# servidor se queda sin memoria. Ochenta megapíxeles es más del doble de lo que
# hace el móvil más grande que se vende.
TOPE_PIXELES = 80_000_000

# Lo que no es una foto no se toca. Un PDF adjunto a una hoja de registro se
# guarda como viene: aquí solo se arreglan imágenes.
NO_SE_TOCAN = {"application/pdf"}


class FotoIlegible(ValueError):
    """[01839] Dice ser una imagen y no lo es. No se guarda."""


def normaliza(datos: bytes, content_type: str) -> tuple[bytes, str]:
    """[01840] Deja la foto lista para guardar. Devuelve los bytes y su tipo.

    Y de paso cierra una puerta que estaba abierta: hasta ahora solo se miraba
    el tipo **que decía** quien subía el fichero, y la extensión con la que se
    guardaba salía de ese mismo dato. Nadie abría el fichero para ver si de
    verdad era una imagen. Aquí se abre, así que lo que no lo sea se cae en
    esta línea y no llega al disco.
    """
    if content_type in NO_SE_TOCAN:
        return datos, content_type

    from PIL import Image, ImageOps, UnidentifiedImageError
    Image.MAX_IMAGE_PIXELS = TOPE_PIXELES
    try:
        imagen = Image.open(io.BytesIO(datos))
        imagen.load()                      # aquí se cae lo que no es una imagen
    except (UnidentifiedImageError, OSError, ValueError) as porque:
        raise FotoIlegible(str(porque)) from porque

    # [01841] Primero el giro a los píxeles, y después el EXIF fuera. En ese orden.
    # Al revés sale de lado: guardar la imagen sin más deja los píxeles
    # tumbados y se lleva por delante la etiqueta que decía cómo girarlos, y
    # entonces ya no hay manera de saber cómo iba.
    imagen = ImageOps.exif_transpose(imagen)
    # Y con esto se va el EXIF entero, GPS incluido.
    imagen = imagen.convert("RGB")

    if max(imagen.size) > LADO_MAX:
        escala = LADO_MAX / max(imagen.size)
        imagen = imagen.resize((max(1, round(imagen.width * escala)),
                                max(1, round(imagen.height * escala))),
                               Image.LANCZOS)

    fuera = io.BytesIO()
    # Progresivo: en una cámara con media raya de cobertura la foto se va
    # viendo mientras baja, en vez de aparecer de golpe. Medido: no pesa más.
    imagen.save(fuera, "JPEG", quality=CALIDAD, subsampling=SUBMUESTREO,
                progressive=True, optimize=True)
    salida = fuera.getvalue()
    # [01842] Si lo que había ya era más pequeño, se queda lo que había. Volver a
    # comprimir algo ya comprimido pierde otra vez y no ahorra nada.
    if len(salida) >= len(datos):
        return datos, content_type
    return salida, "image/jpeg"
