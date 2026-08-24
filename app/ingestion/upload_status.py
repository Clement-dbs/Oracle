"""
Suivi du statut d'ingestion des documents uploadés depuis le front.
Stocké dans Redis (clé par doc_id) pour pouvoir interroger l'avancement.
"""

import json
import logging

import redis

from app.core.config import REDIS_URL

logger = logging.getLogger(__name__)
r = redis.from_url(REDIS_URL, decode_responses=True)

KEY_PREFIX = "doc_status:"
INDEX_KEY = "doc_status_index"  # set des doc_id connus, pour lister facilement


def set_status(doc_id: str, filename: str, status: str, chunks: int = 0, error: str | None = None):
    payload = {
        "doc_id": doc_id,
        "filename": filename,
        "status": status,
        "chunks": chunks,
        "error": error,
    }
    r.set(f"{KEY_PREFIX}{doc_id}", json.dumps(payload), ex=60 * 60 * 24 * 30)
    r.sadd(INDEX_KEY, doc_id)


def get_status(doc_id: str) -> dict | None:
    raw = r.get(f"{KEY_PREFIX}{doc_id}")
    return json.loads(raw) if raw else None


def list_statuses() -> list[dict]:
    doc_ids = r.smembers(INDEX_KEY)
    statuses = []
    for doc_id in doc_ids:
        s = get_status(doc_id)
        if s:
            statuses.append(s)
    return statuses
