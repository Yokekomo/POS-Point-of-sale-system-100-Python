# Las fotos de la portada

Aquí van las fotos, y solo aquí. La portada usa las que encuentre y se ve
terminada sin las que falten: no hay hueco gris ni foto de relleno.

## Los tres sitios

| Archivo | Qué se ve |
|---|---|
| `primal.jpg` | La pieza entera como llega, colgada o sobre la mesa |
| `cortes.jpg` | Lo que sale de ella: los cortes ya separados |
| `plato.jpg` | El plato servido, con su guarnición |

Vale `.webp`, `.jpg`, `.jpeg` o `.png`, en ese orden de preferencia. El nombre
tiene que ser exactamente ese: `primal.webp`, `cortes.jpg`…

## Cómo tienen que ser

- Horizontales, 1600 × 1000 aproximadamente. Se recortan a 260 px de alto.
- Por debajo de 300 KB cada una. En `.webp` al 80 % de calidad sobra.
- Oscuras y de cerca: la portada es oscura y la foto tiene que pegar.

Para convertir y ajustar el tamaño, si hay ImageMagick a mano:

    magick primal-original.jpg -resize 1600x -quality 80 primal.webp

## De dónde sacarlas

Lo mejor son las tuyas: la cámara de tu casa, tu despiece, tu plato. Es más
honesto y no se parece a ninguna otra web.

Si hacen falta bancos de imágenes, estos tres dejan usar las fotos en una web
comercial sin pagar y sin pedir permiso (conviene leer la licencia el día que
se descarga, porque cambian):

- Pexels — https://www.pexels.com/search/beef/
- Unsplash — https://unsplash.com/s/photos/butcher
- Pixabay — https://pixabay.com/images/search/meat/

Lo que **no** vale: fotos sacadas de Google Imágenes, de la web de un
proveedor o de otro restaurante. Eso es de alguien.

## Dónde quedan servidas

En `/static/fotos/<archivo>`. La política de contenido de la web solo permite
imágenes del propio sitio (`img-src 'self'`), así que una foto enlazada desde
fuera no se vería: tiene que estar en esta carpeta.
