"""
Healthcheck global agrégeant les 4 dépendances externes d'Oracle (Qdrant,
Redis, MinIO, Ollama). Chaque vérification est isolée par son propre
try/except avec un timeout court : une dépendance en panne ne doit ni faire
planter l'appel, ni bloquer la réponse.
"""

import logging

import redis
import requests

from app.core.config import (
    MINIO_BUCKET,
    OLLAMA_HOST,
    QDRANT_COLLECTION,
    REDIS_URL,
)

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 2


def _check_qdrant() -> tuple[bool, str]:
    try:
        from app.ingestion.indexer import client as qdrant_client

        qdrant_client.get_collection(QDRANT_COLLECTION)
        return True, "ok"
    except Exception as e:
        logger.warning("[health] Qdrant indisponible : %s", e)
        return False, str(e)


def _check_redis() -> tuple[bool, str]:
    try:
        r = redis.from_url(REDIS_URL, socket_connect_timeout=_TIMEOUT_SECONDS)
        r.ping()
        return True, "ok"
    except Exception as e:
        logger.warning("[health] Redis indisponible : %s", e)
        return False, str(e)


def _check_minio() -> tuple[bool, str]:
    try:
        from app.core.minio import client as minio_client

        minio_client.bucket_exists(MINIO_BUCKET)
        return True, "ok"
    except Exception as e:
        logger.warning("[health] MinIO indisponible : %s", e)
        return False, str(e)


def _check_ollama() -> tuple[bool, str]:
    try:
        resp = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return True, "ok"
    except Exception as e:
        logger.warning("[health] Ollama indisponible : %s", e)
        return False, str(e)


def check_health() -> dict:
    """Interroge les 4 dépendances externes et renvoie un statut agrégé.
    `status` vaut "ok" seulement si tous les services répondent, "degraded"
    sinon -- le détail par service reste disponible pour le diagnostic."""
    checks = {
        "qdrant": _check_qdrant(),
        "redis": _check_redis(),
        "minio": _check_minio(),
        "ollama": _check_ollama(),
    }

    services = {
        name: (message if ok else f"error: {message}") for name, (ok, message) in checks.items()
    }
    all_ok = all(ok for ok, _ in checks.values())

    return {
        "status": "ok" if all_ok else "degraded",
        "services": services,
    }
