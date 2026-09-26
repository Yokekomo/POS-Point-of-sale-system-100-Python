# Imagen de la plataforma. Pequeña, sin compilador y sin correr como root.
#
# La base va con su nombre completo —`trixie`— y no como `3.12-slim` a secas: esa
# etiqueta se mueve sola de una Debian a la siguiente, y con ella se mueve la
# versión de `pg_dump` que se instala abajo. Una imagen de despliegue no puede
# cambiar de herramientas porque haya pasado un año.
FROM python:3.12-slim-trixie AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# [01712] Las herramientas de PostgreSQL: `pg_dump` para hacer la copia de seguridad
# y `psql` para volver de ella. Sin esto, la orden `copia` de un despliegue con
# PostgreSQL se cae en la primera línea, y el día que hiciera falta volver de una
# copia no habría con qué.
#
# La regla que hay que respetar si algún día se toca alguna de las dos versiones:
# **el cliente tiene que ser igual o más nuevo que el servidor**. `pg_dump` se
# niega a volcar de un servidor más nuevo que él. Hoy: Debian trixie trae el
# cliente 17 y `docker-compose.yml` levanta un servidor 16, así que sobra.
RUN apt-get update && \
    apt-get install -y --no-install-recommends postgresql-client && \
    rm -rf /var/lib/apt/lists/*

# Las dependencias primero: así una copia de código no rehace esta capa.
# Se instalan las versiones exactas con las que está probado, no las últimas:
# un despliegue no puede traer una librería distinta solo porque sea martes.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock "psycopg[binary]==3.2.10" && \
    rm -rf /root/.cache

COPY thegrill ./thegrill
COPY tests ./tests

# Un usuario sin privilegios: si alguien entra por la web, entra como nadie.
RUN useradd --create-home --uid 10001 grill && \
    mkdir -p /app/datos && chown -R grill:grill /app
USER grill

ENV GRILL_DB="sqlite:////app/datos/carnes.db" \
    PORT=8000

EXPOSE 8000

# Un solo proceso a propósito: los frenos de contraseña y de formulario viven en
# memoria. Con varios procesos hacen falta en un sitio compartido; hasta
# entonces, se crece con más máquinas detrás del balanceador, no con más
# procesos dentro de una.
CMD ["sh", "-c", "python -m thegrill.cli --db \"$GRILL_DB\" serve-carne --host 0.0.0.0 --port ${PORT}"]
