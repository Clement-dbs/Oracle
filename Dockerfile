# syntax=docker/dockerfile:1
# Image de prod (buildée par release_ghcr.sh via `docker build .` sans -f,
# donc ce nom de fichier ne doit pas changer). N'installe que
# requirements.txt (runtime), pas les outils de lint/tests/évaluation --
# voir Dockerfile.dev pour l'image de dev.
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /usr/local/bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-fra \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system -r requirements.txt

COPY app ./app
COPY main.py .
COPY factory.py .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
