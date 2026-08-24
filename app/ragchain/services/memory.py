"""
Gestion de la mémoire conversationnelle par session via Redis.
Fenêtre glissante des N derniers échanges (pas de résumé pour rester simple ici,
facilement extensible avec ConversationSummaryMemory si les conversations sont longues).

add_turn() synchronise aussi les métadonnées de conversation (last_activity, auto-titre).
"""

import json
import logging
import uuid

import redis

from app.core.config import REDIS_URL
from app.core.rag_settings import get_rag_settings

logger = logging.getLogger(__name__)
r = redis.from_url(REDIS_URL, decode_responses=True)


def _key(session_id: str) -> str:
    return f"chat_history:{session_id}"


def get_history(session_id: str) -> list[dict]:
    """Retourne la liste des échanges [{"role": "user"/"assistant", "content": "..."}]"""
    raw = r.get(_key(session_id))
    if not raw:
        return []
    return json.loads(raw)


def add_turn(
    session_id: str,
    role: str,
    content: str,
    sources: list[str] | None = None,
    attachment_filename: str | None = None,
    message_id: str | None = None,
    owner: str = "",
):
    """message_id : passé par chat_stream() pour le tour "assistant" -- généré
    par generate_stream_answer() et déjà envoyé au front via le SSE
    final_payload, pour que le feedback (thumbs up/down) posé côté client
    référence le même identifiant que celui persisté ici. Généré ici si
    absent (tour "user", pas de feedback dessus mais ID gardé par cohérence).

    owner : identifiant utilisateur stable (cf. conversations.py) -- transmis
    ici uniquement pour amorcer les métadonnées d'une conversation démarrée
    sans create_conversation() préalable (cf. chat_stream())."""
    history = get_history(session_id)
    turn: dict = {"role": role, "content": content, "message_id": message_id or str(uuid.uuid4())}
    if sources:
        turn["sources"] = sources
    if attachment_filename:
        # Seul le NOM du fichier joint est persisté (pas son texte extrait) :
        # juste assez pour réafficher le chip "📎 fichier" au rechargement
        # d'une conversation -- le contenu lui-même reste éphémère, jamais
        # retrouvable au-delà de la question pour laquelle il a été fourni.
        turn["attachment_filename"] = attachment_filename
    history.append(turn)

    # Fenêtre glissante : on garde les N derniers échanges (user+assistant comptés ensemble)
    max_messages = get_rag_settings()["max_history_turns"] * 2
    if len(history) > max_messages:
        history = history[-max_messages:]

    ttl_seconds = get_rag_settings()["conversation_ttl_days"] * 86400
    r.set(_key(session_id), json.dumps(history), ex=ttl_seconds)

    # Synchronise les métadonnées de conversation
    try:
        from app.ragchain.services.conversations import update_last_activity

        # Auto-titre : premier message de l'utilisateur (tronqué à 60 chars)
        auto_title: str | None = None
        if role == "user" and len(history) == 1:
            auto_title = content.strip()

        update_last_activity(session_id, owner=owner, auto_title=auto_title)
    except Exception as exc:
        logger.warning("Impossible de mettre à jour les métadonnées conversation : %s", exc)


def format_history_for_prompt(history: list[dict]) -> str:
    """Formatte l'historique pour l'injecter dans un prompt texte."""
    lines = []
    for turn in history:
        prefix = "Utilisateur" if turn["role"] == "user" else "Assistant"
        lines.append(f"{prefix}: {turn['content']}")
    return "\n".join(lines)
