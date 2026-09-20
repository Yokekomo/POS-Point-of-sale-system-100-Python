# Imagen de la plataforma. Pequeña, sin compilador y sin correr como root.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

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
