# Driver.js, aquí dentro y no en un CDN

El tutorial guiado usa **Driver.js 1.3.6**, licencia **MIT** (el texto está al
lado, en `LICENSE`). Está copiado aquí a propósito: la web de este programa no
carga **nada** de fuera —ni una tipografía—, y eso no se rompe por un tutorial.
Copiado también significa que funciona dentro de la cámara, sin cobertura: el
ayudante que guarda las pantallas guarda estos dos ficheros con ellas.

Tal y como se bajó del registro de npm:

    https://registry.npmjs.org/driver.js/-/driver.js-1.3.6.tgz

    driver.js   sha256 31d6a387715585cc5507a3dded09eb97969f8167fabfb272648f84c6b2608325
    driver.css  sha256 4b398481a7ce8375af4d9f58f39410c73a0b70726fe513686d3d51c10ad76cb5

`driver.js` es el fichero `dist/driver.js.iife.js` del paquete, renombrado: es
la versión que se carga con una etiqueta `<script>` de toda la vida y deja
`window.driver`. Para subir de versión: bajar el paquete, copiar esos dos
ficheros y su licencia, y apuntar aquí las huellas nuevas.
