import json
import logging
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.ragchain.services.conversations import (
    create_conversation,
    delete_conversation,
    get_conversation,
    get_conversation_any,
    list_conversations,
    session_belongs_to,
    update_title,
)
from app.ragchain.services.feedback import (
    delete_feedback,
    get_feedback,
    list_feedback,
    save_feedback,
)
from app.ragchain.services.memory import add_turn
from app.ragchain.services.rag_chain import generate_stream_answer
from app.ragchain.services.schema import ChatRequest

logger = logging.getLogger(__name__)
ragchain_router = APIRouter(prefix="/chat")


def _get_owner(request: Request) -> str:
    """Identifiant utilisateur stable posé par LeCockpittt (header
    X-Oracle-User-Id, cf. _trust_headers côté LeCockpittt) -- utilisé pour
    scoper les conversations par utilisateur (cf. conversations.py). Distinct
    de X-Oracle-Username, qui porte le prénom affiché et n'est pas garanti
    unique. Header absent (accès direct/dev sans LeCockpittt devant) : ""
    -- tout le monde partage alors un espace "anonyme" unique, comme avant
    ce fix."""
    return request.headers.get("x-oracle-user-id") or ""


# ── Conversations CRUD ────────────────────────────────────────────────────────


@ragchain_router.get("/conversations")
def get_conversations(request: Request):
    """Liste les conversations de l'utilisateur courant, triées par activité
    décroissante."""
    return {"conversations": list_conversations(owner=_get_owner(request))}


@ragchain_router.post("/conversations")
def new_conversation(request: Request):
    """Crée une nouvelle conversation vide rattachée à l'utilisateur courant
    et retourne son session_id."""
    session_id = create_conversation(owner=_get_owner(request))
    return {"session_id": session_id}


@ragchain_router.get("/conversations/{session_id}/history")
def get_conversation_history(session_id: str, request: Request):
    """Retourne les métadonnées + l'historique complet d'une conversation --
    404 si elle n'existe pas ou n'appartient pas à l'utilisateur courant."""
    conv = get_conversation(session_id, owner=_get_owner(request))
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation introuvable.")
    return conv


class TitleUpdate(BaseModel):
    title: str


@ragchain_router.patch("/conversations/{session_id}/title")
def rename_conversation(session_id: str, body: TitleUpdate, request: Request):
    """Renomme une conversation (no-op silencieux si elle n'appartient pas à
    l'utilisateur courant)."""
    update_title(session_id, body.title, owner=_get_owner(request))
    return {"ok": True}


@ragchain_router.delete("/conversations/{session_id}")
def delete_conversation_route(session_id: str, request: Request):
    """Supprime une conversation (métadonnées + historique) -- no-op
    silencieux si elle n'appartient pas à l'utilisateur courant."""
    delete_conversation(session_id, owner=_get_owner(request))
    return {"status": "ok", "message": f"Conversation {session_id} supprimée."}


# ── Chat (streaming) ──────────────────────────────────────────────────────────


@ragchain_router.post("/new")
def chat_stream(req: ChatRequest, request: Request):
    owner = _get_owner(request)
    session_id = req.session_id or ""
    if not session_id or not session_belongs_to(session_id, owner):
        # Pas d'id fourni, ou id d'une conversation qui n'existe pas /
        # n'appartient pas à l'utilisateur courant (ex : localStorage du
        # navigateur encore sur l'id d'un précédent utilisateur -- c'est ce
        # qui faisait atterrir tout le monde dans le même fil avant ce fix) :
        # on repart sur un nouveau session_id plutôt que d'écrire dans
        # l'historique de quelqu'un d'autre.
        session_id = str(uuid.uuid4())

    def generator():
        # Envoyé en premier événement : si le session_id a dû être remplacé
        # ci-dessus, le front doit le savoir pour continuer à écrire au bon
        # endroit (cf. gestion de data.session_id côté script.js).
        yield f"data: {json.dumps({'session_id': session_id})}\n\n"

        full_answer = []
        sources: list[str] = []
        message_id: str | None = None
        try:
            for event in generate_stream_answer(
                session_id,
                req.message,
                allowed_categories=req.allowed_categories,
                is_admin=req.is_admin,
                attachment_filename=req.attachment.filename if req.attachment else None,
                attachment_text=req.attachment.text if req.attachment else None,
            ):
                yield event
                raw = event.replace("data: ", "").strip()
                if raw and raw != "[DONE]":
                    try:
                        data = json.loads(raw)
                        # if indépendants (pas elif) : final_payload porte
                        # sources/message_id ensemble dans le même événement.
                        if "token" in data:
                            full_answer.append(data["token"])
                        if "sources" in data:
                            sources = data["sources"]
                        if "message_id" in data:
                            message_id = data["message_id"]
                    except Exception:
                        pass
        except Exception as exc:
            # Erreur inattendue (Qdrant down, LLM injoignable, etc.)
            # On yield un token d'erreur pour que le navigateur reçoive
            # toujours une réponse SSE complète, jamais une coupure.
            logger.error("[stream][%s] Erreur non rattrapée : %s", session_id, exc)
            err_msg = "Désolé, une erreur est survenue. Le service de recherche documentaire est peut-être indisponible."
            full_answer = [err_msg]
            yield f"data: {json.dumps({'token': err_msg})}\n\n"
            yield f"data: {json.dumps({'sources': [], 'standalone_question': req.message})}\n\n"
            yield "data: [DONE]\n\n"

        answer = "".join(full_answer)
        add_turn(
            session_id,
            "user",
            req.message,
            attachment_filename=req.attachment.filename if req.attachment else None,
            owner=owner,
        )
        add_turn(
            session_id,
            "assistant",
            answer,
            sources=sources,
            message_id=message_id,
            owner=owner,
        )

    return StreamingResponse(generator(), media_type="text/event-stream")


# ── Feedback (pouce haut/bas sur une réponse) ─────────────────────────────────


def _require_admin(request: Request) -> None:
    """Même garde que app/settings/routes.py::_require_admin -- header
    absent (accès direct/dev) : permissif. Header présent et différent de
    "1" : refusé."""
    is_admin_header = request.headers.get("x-oracle-is-admin")
    if is_admin_header is not None and is_admin_header != "1":
        raise HTTPException(status_code=403, detail="Réservé aux administrateurs.")


class FeedbackBody(BaseModel):
    rating: Literal["up", "down"]
    category: Literal["trop_long", "incorrect", "bug", "autre"] | None = None
    comment: str | None = None


@ragchain_router.post("/{session_id}/messages/{message_id}/feedback")
def post_feedback(session_id: str, message_id: str, body: FeedbackBody):
    """Enregistre (ou remplace) le feedback sur une réponse assistant
    précise. Aucun droit particulier requis : chacun note ses propres
    conversations."""
    try:
        entry = save_feedback(
            session_id, message_id, body.rating, category=body.category, comment=body.comment
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return entry


@ragchain_router.get("/{session_id}/messages/{message_id}/feedback")
def get_feedback_route(session_id: str, message_id: str):
    """Feedback déjà posé sur ce message, ou null si aucun -- utilisé au
    rechargement d'une conversation pour restituer l'état du vote (cf. aussi
    l'enrichissement en bloc fait par get_conversation())."""
    return get_feedback(session_id, message_id)


@ragchain_router.get("/feedback")
def list_feedback_route(request: Request, rating: str | None = None):
    """Écran admin de consultation des retours -- réservé à l'admin. Enrichit
    chaque entrée avec le titre de conversation et la question/réponse
    associées, quand elles sont encore disponibles (une conversation peut
    avoir expiré -- cf. conversation_ttl_days -- ou le message être sorti de
    la fenêtre de mémoire active -- cf. max_history_turns -- sans que le
    feedback lui-même ne disparaisse, puisqu'il n'a pas de TTL). Utilise
    get_conversation_any() (pas get_conversation()) : l'admin doit voir
    l'enrichissement des retours de tout le monde, pas seulement les siens."""
    _require_admin(request)
    entries = list_feedback(rating=rating)

    enriched = []
    for entry in entries:
        sid = entry.get("session_id")
        mid = entry.get("message_id")
        conv = get_conversation_any(sid) if sid else None
        title = conv["title"] if conv else None
        question = None
        answer = None
        if conv:
            messages = conv["messages"]
            for i, m in enumerate(messages):
                if m.get("message_id") == mid and m.get("role") == "assistant":
                    answer = m.get("content")
                    if i > 0 and messages[i - 1].get("role") == "user":
                        question = messages[i - 1].get("content")
                    break
        enriched.append(
            {
                **entry,
                "conversation_title": title,
                "question": question,
                "answer": answer,
            }
        )

    return {"feedback": enriched}


@ragchain_router.delete("/feedback/{session_id}/{message_id}")
def delete_feedback_route(session_id: str, message_id: str, request: Request):
    """Suppression manuelle d'un retour depuis le panneau admin (bouton
    poubelle) -- ne touche pas à la conversation elle-même, contrairement à la
    suppression en cascade faite par delete_conversation()."""
    _require_admin(request)
    deleted = delete_feedback(session_id, message_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Retour introuvable.")
    return {"success": True}
