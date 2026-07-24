FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY atlas ./atlas
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY alembic ./alembic

# Migrations run on boot; the container is the only writer of DDL.
CMD ["sh", "-c", "alembic upgrade head && uvicorn atlas.main:app --host 0.0.0.0 --port 8000"]
