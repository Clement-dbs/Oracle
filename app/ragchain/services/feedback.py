"""Feedback utilisateur (pouce haut/bas + catégorie + commentaire) sur une
réponse assistant précise, identifiée par (session_id, message_id).

Stockage Redis, volontairement séparé de chat_history/conv_meta : le TTL de
ces derniers suit le réglage admin ``conversation_ttl_days`` (cf. memory.py /
conversations.py) et une conversation peut expirer sans que son feedback ne
disparaisse avec elle (signal produit conservé, même sans le contexte
question/réponse). Aucune expiration posée ici.

En revanche, la suppression explicite d'une conversation par l'utilisateur
(delete_conversation()) supprime aussi son feedback en cascade -- voir
delete_feedback_for_session(), appelée depuis conversations.py.

Structure Redis :
  - ``feedback:{session_id}:{message_id}``  hash  {rating, category, comment, created_at}
  - ``feedback:all``                         sorted set  {"session_id:message_id": timestamp}
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlsplit, urlunsplit

import redis

from app.core.config import REDIS_URL

logger = logging.getLogger(__name__)

r = redis.from_url(REDIS_URL, decode_responses=True)


def _redacted_redis_url() -> str:
    """host:port (+ index DB) sans identifiants, pour du logging diagnostic
    sans risquer de faire fuiter un mot de passe dans les logs."""
    if not REDIS_URL:
        return "(non défini)"
    parts = urlsplit(REDIS_URL)
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))

_INDEX_KEY = "feedback:all"
_KEY_PREFIX = "feedback:"

RATINGS = {"up", "down"}
CATEGORIES = {"trop_long", "incorrect", "bug", "autre"}


def _key(session_id: str, message_id: str) -> str:
    return f"{_KEY_PREFIX}{session_id}:{message_id}"


def save_feedback(
    session_id: str,
    message_id: str,
    rating: str,
    category: str | None = None,
    comment: str | None = None,
) -> dict:
    """Enregistre (ou écrase, si l'utilisateur change d'avis) le feedback sur
    un message donné. category n'a de sens que si rating == "down", mais
    n'est pas rejetée si fournie avec "up" (simplement ignorée côté lecture)."""
    if rating not in RATINGS:
        raise ValueError(f"rating invalide : {rating!r} (attendu : {sorted(RATINGS)})")
    if category is not None and category not in CATEGORIES:
        raise ValueError(f"category invalide : {category!r} (attendu : {sorted(CATEGORIES)})")

    now = time.time()
    entry = {
        "session_id": session_id,
        "message_id": message_id,
        "rating": rating,
        "category": category or "",
        "comment": comment or "",
        "created_at": now,
    }
    pipe = r.pipeline()
    pipe.hset(_key(session_id, message_id), mapping=entry)
    pipe.zadd(_INDEX_KEY, {f"{session_id}:{message_id}": now})
    pipe.execute()
    return entry


def get_feedback(session_id: str, message_id: str) -> dict | None:
    """Feedback existant pour un message donné, ou None si jamais noté."""
    raw = r.hgetall(_key(session_id, message_id))
    if not raw:
        return None
    return raw


def list_feedback(limit: int = 200, rating: str | None = None) -> list[dict]:
    """Tous les feedbacks, plus récents en premier -- réservé à l'admin (cf.
    _require_admin dans routes.py, même garde que app/settings/routes.py).
    rating : filtre optionnel ("up"/"down"), ignoré si valeur inconnue."""
    members = r.zrevrange(_INDEX_KEY, 0, limit - 1)
    if not members:
        return []

    pipe = r.pipeline()
    for member in members:
        session_id, message_id = member.split(":", 1)
        pipe.hgetall(_key(session_id, message_id))
    raw_entries = pipe.execute()

    entries = [e for e in raw_entries if e]
    if rating in RATINGS:
        entries = [e for e in entries if e.get("rating") == rating]
    entries.sort(key=lambda e: float(e.get("created_at") or 0), reverse=True)
    return entries


def delete_feedback(session_id: str, message_id: str) -> bool:
    """Supprime manuellement un retour précis (bouton poubelle du panneau
    admin) -- contrairement à delete_feedback_for_session(), la conversation
    elle-même n'est pas touchée. Retourne False si le retour n'existait déjà
    plus (idempotent)."""
    key = _key(session_id, message_id)
    member = f"{session_id}:{message_id}"

    # Diagnostic temporaire : un premier test en prod a renvoyé 404 sur un
    # retour pourtant bien visible dans le panneau admin juste avant --
    # log détaillé pour comprendre où la clé/l'entrée d'index divergent
    # (mauvaise instance Redis ? entrée d'index orpheline ? autre ?) avant de
    # creuser plus loin à l'aveugle.
    if not r.exists(key):
        similar = [m for m in r.zrange(_INDEX_KEY, 0, -1) if message_id in m]
        logger.warning(
            "[delete_feedback] Clé %s absente de Redis (session=%s, message=%s) -- "
            "membres d'index contenant ce message_id : %s -- redis_url=%s",
            key,
            session_id,
            message_id,
            similar,
            _redacted_redis_url(),
        )

    pipe = r.pipeline()
    pipe.delete(key)
    pipe.zrem(_INDEX_KEY, member)
    deleted, _ = pipe.execute()
    return bool(deleted)


def delete_feedback_for_session(session_id: str) -> None:
    """Supprime tout le feedback d'une conversation explicitement supprimée
    (cf. conversations.delete_conversation) -- sans le message associé
    (jamais dupliqué dans le feedback), un avis orphelin n'a plus de contexte
    exploitable."""
    prefix = f"{session_id}:"
    members = [m for m in r.zrange(_INDEX_KEY, 0, -1) if m.startswith(prefix)]
    if not members:
        return

    pipe = r.pipeline()
    for member in members:
        _, message_id = member.split(":", 1)
        pipe.delete(_key(session_id, message_id))
    pipe.zrem(_INDEX_KEY, *members)
    pipe.execute()


def get_feedback_bulk(session_id: str, message_ids: list[str]) -> dict[str, dict]:
    """Feedback existant pour plusieurs messages d'une même conversation en
    un seul aller-retour Redis (pipeline) -- utilisé au rechargement d'une
    conversation pour restituer l'état des votes déjà posés."""
    if not message_ids:
        return {}
    pipe = r.pipeline()
    for mid in message_ids:
        pipe.hgetall(_key(session_id, mid))
    results = pipe.execute()
    return {mid: raw for mid, raw in zip(message_ids, results, strict=True) if raw}
