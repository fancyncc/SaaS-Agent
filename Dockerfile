FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
RUN mkdir -p backend && touch backend/__init__.py
RUN pip install --no-cache-dir .
COPY backend backend
COPY alembic alembic
COPY alembic.ini ./
COPY knowledge knowledge
COPY evaluations evaluations
CMD ["sh","-c","python -m backend.db_role ensure && DATABASE_URL=$MIGRATION_DATABASE_URL alembic upgrade head && python -m backend.db_role grant && uvicorn backend.main:app --host 0.0.0.0 --port 8000"]
