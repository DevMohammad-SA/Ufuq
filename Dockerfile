FROM python:3.12-slim AS base

# System dependencies: WeasyPrint needs GTK/Pango/Cairo libraries that
# don't exist in a bare Python image — this is exactly the gap that
# causes "cannot load library 'libgobject-2.0-0'" locally. Installing
# them here, once, inside the image, means the deployed container never
# hits that error regardless of the host OS.
#
# netcat-openbsd provides `nc`, used by docker/entrypoint.sh to wait for
# PostgreSQL to accept connections before running migrate/collectstatic.
# libpq-dev + gcc build psycopg2-binary's few remaining native bits and
# are also harmless to keep even though the "binary" wheel bundles libpq
# itself (see Dockerfile notes in the deployment report).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libglib2.0-0 \
    libpq-dev \
    gcc \
    curl \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Install uv (the project's package manager) directly into the image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for better Docker layer caching — the
# expensive `uv sync` step only reruns when dependencies actually change,
# not on every code edit.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

RUN chmod +x docker/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["docker/entrypoint.sh"]
