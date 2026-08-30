"""Gestion des conversations persistées en Redis.

Chaque conversation = un session_id (UUID), rattaché à un owner (identifiant
utilisateur stable, cf. _get_owner() dans app/ragchain/routes.py -- lu depuis
un header optionnel posé par un éventuel reverse-proxy en amont, distinct du
nom affiché qui n'est jamais garanti unique). owner="" (header absent, usage
direct/dev sans reverse-proxy) : tout le monde partage un espace "anonyme"
unique -- comportement par défaut d'Oracle en app standalone.

Structure Redis :
  - ``conversations:{owner}``     sorted set  {session_id: timestamp_last_activity}
  - ``conv_meta:{session_id}``    hash        {session_id, owner, title, created_at, last_activity}
  - ``chat_history:{session_id}`` string JSON (géré par memory.py)

TTL : réglage admin ``conversation_ttl_days`` (rag_settings), appliqué à la
fois sur chat_history (memory.py) et conv_meta (ci-dessous) -- les deux
expirent en même temps, ce qui permet au nettoyage des entrées orphelines
dans list_conversations() de fonctionner réellement (sinon la meta ne
disparaissant jamais, l'entrée restait indéfiniment dans le sorted set).
"""

from __future__ import annotations

import time
import uuid

import redis

from app.core.config import REDIS_URL
from app.core.rag_settings import get_rag_settings

r = redis.from_url(REDIS_URL, decode_responses=True)

_CONV_LIST_PREFIX = "conversations:"
_META_PREFIX = "conv_meta:"
_HISTORY_PREFIX = "chat_history:"
_MAX_TITLE_LEN = 60


def _meta_key(session_id: str) -> str:
    return f"{_META_PREFIX}{session_id}"


def _conv_list_key(owner: str) -> str:
    return f"{_CONV_LIST_PREFIX}{owner}"


def _ttl_seconds() -> int:
    return get_rag_settings()["conversation_ttl_days"] * 86400


def _owner_of(session_id: str) -> str:
    """Owner enregistré pour cette conversation, "" si absente ou créée
    avant ce fix (pas de champ owner)."""
    return r.hget(_meta_key(session_id), "owner") or ""


def session_belongs_to(session_id: str, owner: str = "") -> bool:
    """True si cette conversation existe et appartient bien à `owner` --
    utilisé par chat_stream() pour ne jamais écrire dans l'historique d'un
    session_id fourni par le client sans vérifier qu'il appartient
    effectivement à l'utilisateur courant (ex: localStorage du navigateur
    encore sur l'id d'un précédent utilisateur -- c'est ce qui causait tout
    le monde dans le même fil avant ce fix)."""
    meta = r.hgetall(_meta_key(session_id))
    if not meta:
        return False
    return (meta.get("owner") or "") == owner


# ── CRUD ──────────────────────────────────────────────────────────────────────


def create_conversation(owner: str = "") -> str:
    """Crée une nouvelle conversation vide, rattachée à `owner`. Retourne son
    session_id."""
    session_id = str(uuid.uuid4())
    now = time.time()
    pipe = r.pipeline()
    # Pas de champ "title" ici : update_last_activity() le pose via HSETNX,
    # qui n'écrit que si le champ est absent (une valeur vide pré-existante
    # bloquait silencieusement le titre auto).
    pipe.hset(
        _meta_key(session_id),
        mapping={
            "session_id": session_id,
            "owner": owner,
            "created_at": now,
            "last_activity": now,
        },
    )
    pipe.expire(_meta_key(session_id), _ttl_seconds())
    pipe.zadd(_conv_list_key(owner), {session_id: now})
    pipe.execute()
    return session_id


def list_conversations(owner: str = "", limit: int = 100) -> list[dict]:
    """Retourne les conversations de `owner`, triées par activité décroissante
    (la plus récente en premier)."""
    session_ids = r.zrevrange(_conv_list_key(owner), 0, limit - 1)
    result = []
    orphans = []
    for sid in session_ids:
        meta = r.hgetall(_meta_key(sid))
        if not meta:
            orphans.append(sid)
            continue
        result.append(
            {
                "session_id": sid,
                "title": meta.get("title") or "Nouvelle conversation",
                "created_at": float(meta.get("created_at") or 0),
                "last_activity": float(meta.get("last_activity") or 0),
            }
        )
    # nettoyage des entrées orphelines (meta expirée)
    if orphans:
        r.zrem(_conv_list_key(owner), *orphans)
    return result


def get_conversation_any(session_id: str) -> dict | None:
    """Comme get_conversation(), mais sans vérification d'appartenance --
    réservé à un usage interne de confiance (panneau admin feedback, cf.
    list_feedback_route dans routes.py, qui doit pouvoir retrouver la
    question/réponse d'un feedback quel qu'en soit l'auteur)."""
    meta = r.hgetall(_meta_key(session_id))
    if not meta:
        return None
    from app.ragchain.services.feedback import get_feedback_bulk
    from app.ragchain.services.memory import get_history

    messages = get_history(session_id)
    message_ids = [m["message_id"] for m in messages if m.get("message_id")]
    feedback_by_id = get_feedback_bulk(session_id, message_ids)
    for m in messages:
        m["feedback"] = feedback_by_id.get(m.get("message_id"))

    return {
        "session_id": session_id,
        "title": meta.get("title") or "Nouvelle conversation",
        "created_at": float(meta.get("created_at") or 0),
        "last_activity": float(meta.get("last_activity") or 0),
        "messages": messages,
    }


def get_conversation(session_id: str, owner: str = "") -> dict | None:
    """Retourne les métadonnées + l'historique complet d'une conversation --
    None si elle n'existe pas OU n'appartient pas à `owner` (jamais de fuite
    d'un utilisateur à l'autre, cf. docstring module)."""
    if _owner_of(session_id) != owner:
        return None
    return get_conversation_any(session_id)


def delete_conversation(session_id: str, owner: str = "") -> None:
    """Supprime une conversation (métadonnées + historique Redis) et son
    feedback -- sans le message associé, un avis orphelin n'a plus de
    contexte exploitable (cf. feedback.delete_feedback_for_session). No-op
    silencieux si la conversation n'appartient pas à `owner`."""
    if _owner_of(session_id) != owner:
        return

    from app.ragchain.services.feedback import delete_feedback_for_session

    pipe = r.pipeline()
    pipe.delete(_meta_key(session_id))
    pipe.delete(f"{_HISTORY_PREFIX}{session_id}")
    pipe.zrem(_conv_list_key(owner), session_id)
    pipe.execute()
    delete_feedback_for_session(session_id)


def update_title(session_id: str, title: str, owner: str = "") -> None:
    """Met à jour le titre d'une conversation -- no-op silencieux si elle
    n'appartient pas à `owner`."""
    if _owner_of(session_id) != owner:
        return
    r.hset(_meta_key(session_id), "title", title[:_MAX_TITLE_LEN])


def update_last_activity(
    session_id: str, *, owner: str = "", auto_title: str | None = None
) -> None:
    """Met à jour le timestamp d'activité et (optionnellement) pose le titre
    automatique. Crée les métadonnées si elles n'existent pas encore
    (conversation démarrée directement via POST /chat/new sans passer par
    create_conversation() au préalable, cf. chat_stream()) -- HSETNX pour
    "owner" : ne jamais écraser l'owner déjà enregistré par un appel
    ultérieur. ``auto_title`` n'écrase pas non plus un titre déjà posé
    (HSETNX également).
    """
    now = time.time()
    pipe = r.pipeline()
    pipe.hsetnx(_meta_key(session_id), "session_id", session_id)
    pipe.hsetnx(_meta_key(session_id), "owner", owner)
    pipe.hsetnx(_meta_key(session_id), "created_at", now)
    # Toujours écraser last_activity
    pipe.hset(_meta_key(session_id), "last_activity", now)
    if auto_title:
        pipe.hsetnx(_meta_key(session_id), "title", auto_title[:_MAX_TITLE_LEN])
    # Repousse l'expiration de la meta en même temps que celle de
    # chat_history (memory.py) : les deux doivent rester synchronisées.
    pipe.expire(_meta_key(session_id), _ttl_seconds())
    pipe.execute()
    # Indexé sous l'owner réellement enregistré (celui posé par HSETNX
    # ci-dessus -- pas nécessairement celui passé ici si cette conversation
    # existait déjà sous un autre owner) : jamais indexé au mauvais endroit.
    real_owner = r.hget(_meta_key(session_id), "owner") or owner
    r.zadd(_conv_list_key(real_owner), {session_id: now})
