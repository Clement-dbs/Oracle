"""
Routes admin pour les réglages RAG (app.core.rag_settings) : lecture,
mise à jour partielle, réinitialisation. Oracle est une app standalone,
sans système de droits propre -- ces routes sont accessibles sans
restriction (cf. /session-info dans factory.py, toujours is_admin=True).
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.rag_settings import get_rag_settings, reset_rag_settings, save_rag_settings

logger = logging.getLogger(__name__)
settings_router = APIRouter(prefix="/settings")


class RagSettingsUpdate(BaseModel):
    """Mise à jour partielle (cf. save_rag_settings) : seuls les champs
    fournis sont modifiés. Bornes (ge/le) pour éviter qu'une saisie ne casse
    silencieusement le pipeline (ex: top_k_retrieval=0)."""

    chunk_size: int | None = Field(default=None, ge=100, le=4000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=1000)
    top_k_retrieval: int | None = Field(default=None, ge=1, le=200)
    max_history_turns: int | None = Field(default=None, ge=1, le=200)
    conversation_ttl_days: int | None = Field(default=None, ge=1, le=365)
    attachment_max_chars: int | None = Field(default=None, ge=500, le=200_000)
    max_file_size_mb: int | None = Field(default=None, ge=1, le=1000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)

    # rewrite_prompt/classify_prompt : consigne seule, sans placeholder --
    # l'historique/la question/la liste de documents sont ajoutés en dur par
    # rag_chain.py (cf. rewrite_query/classify_question), pas éditables ici.
    oracle_identity: str | None = None
    rewrite_prompt: str | None = None
    classify_prompt: str | None = None
    system_prompt: str | None = None
    system_prompt_attachment: str | None = None


@settings_router.get("/rag")
def get_settings() -> dict:
    return get_rag_settings()


@settings_router.put("/rag")
def update_settings(body: RagSettingsUpdate) -> dict:
    partial = body.model_dump(exclude_none=True)
    if not partial:
        raise HTTPException(status_code=400, detail="Aucun champ à mettre à jour.")
    return save_rag_settings(partial)


@settings_router.post("/rag/reset")
def reset_settings() -> dict:
    return reset_rag_settings()
