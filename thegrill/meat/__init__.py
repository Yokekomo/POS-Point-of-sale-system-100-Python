"""Edición solo carne: control de carnes para restaurantes y hoteles.

Misma casa que la plataforma de cocina, mismo motor probado —despiece, coste,
FEFO, descongelado, inventario, trazabilidad y merma—, pero con una sola
puerta: la carne. Sin plantillas HACCP generales, sin recetas de cocina, sin
escandallos de platos que no llevan carne.

Corre por su cuenta, con su propia base de datos y su propio acceso:

    python -m thegrill.cli --db sqlite:///carnes.db serve-carne --port 8001
"""
