"""
Cœur du pipeline RAG agentique :
1. Reformulation de la question avec l'historique
2. Classification : la question nécessite-t-elle une recherche documentaire ?
3. Retrieval + reranking uniquement si nécessaire
4. Génération en streaming avec le contexte adapté
"""

import json
import logging
import re
import time
import uuid
from collections.abc import Generator

from langchain_core.messages import HumanMessage, SystemMessage
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

from app.core.config import QDRANT_COLLECTION, QDRANT_URL
from app.core.llm import get_generation_llm, llm
from app.core.rag_settings import get_rag_settings
from app.ingestion.embeddings import embed_query
from app.ingestion.reranker_model import load_reranker_model
from app.ragchain.services.category_filter import category_allowed
from app.ragchain.services.memory import format_history_for_prompt, get_history

logger = logging.getLogger(__name__)

qdrant_client = QdrantClient(url=QDRANT_URL)


# ---------- Étape 1 : reformulation de la requête avec l'historique ----------


def rewrite_query(session_id: str, question: str) -> str:
    history = get_history(session_id)
    if not history:
        return question

    settings = get_rag_settings()
    formatted_history = format_history_for_prompt(history)
    # settings["rewrite_prompt"] : uniquement la consigne éditable par l'admin
    # (sans placeholder) -- l'historique et la question sont ajoutés ici, en
    # dur, pour que l'admin n'ait jamais à manipuler {history}/{question}.
    prompt = (
        f"{settings['rewrite_prompt']}\n\n"
        f"Historique :\n{formatted_history}\n\n"
        f"Question utilisateur : {question}\n\n"
        "Question reformulée :"
    )
    messages = [
        SystemMessage(content=settings["oracle_identity"]),
        HumanMessage(content=prompt),
    ]
    response = llm.invoke(messages)
    logger.info("[%s] Question reformulée", session_id)
    return response.content.strip()


# ---------- Étape 2 : classification — recherche documentaire nécessaire ? ----------


def _match_source_name(name: str, available_sources: list[str]) -> str | None:
    """Retrouve le source_file exact désigné par classify_question() (tolérant
    casse/accents/correspondance partielle)."""
    name_norm = _normalize(name)
    if not name_norm:
        return None
    for source in available_sources:
        if _normalize(source.split("/")[-1]) == name_norm:
            return source
    for source in available_sources:
        if name_norm in _normalize(source.split("/")[-1]):
            return source
    return None


_SEARCH_MARKER = "OUI"
# Sépare la requête de recherche du nom de document ciblé, insensible à la
# casse et aux espaces autour de ":" (ex: "| document :", "| DOCUMENT:"...).
_DOCUMENT_MARKER_RE = re.compile(r"\|\s*DOCUMENT\s*:", re.IGNORECASE)


def classify_question(
    question: str, available_sources: list[str] | None = None
) -> tuple[bool, str, str | None]:
    """Décide si une recherche documentaire est nécessaire, via un LLM.

    Volontairement SANS `SystemMessage(settings["oracle_identity"])` ici,
    contrairement à rewrite_query()/génération : cette phrase d'identité
    ("Tu es Oracle, l'assistant IA interne de Strattt, cabinet d'expertise
    comptable") donnait au classifieur une fausse impression de
    "connaissance générale" sur Strattt elle-même. Constaté en prod : pour
    "Dis moi ce que tu sais sur Strattt", il répondait NON (pas de recherche)
    et la génération se contentait de reformuler cette même phrase d'identité
    -- alors que des documents internes sur Strattt sont bien indexés. La
    classification n'a pas besoin de personnalité, seulement de juger s'il
    faut chercher."""

    settings = get_rag_settings()
    sources_block = "\n".join(f"- {s}" for s in (available_sources or [])) or (
        "(aucun document indexé)"
    )

    prompt = (
        f"{settings['classify_prompt']}\n\n"
        f"Documents disponibles :\n{sources_block}\n\n"
        f"Question : {question}"
    )
    messages = [HumanMessage(content=prompt)]
    response = llm.invoke(messages)
    answer = response.content.strip()

    # Cas 1 : le LLM juge qu'aucune recherche documentaire n'est nécessaire.
    if not answer.upper().startswith(_SEARCH_MARKER):
        logger.info("Classification : réponse depuis connaissances générales")
        return False, question, None

    # Cas 2 : recherche nécessaire -- on extrait la requête, et le document ciblé si le LLM en a identifié un.
    body = answer[len(_SEARCH_MARKER) :].lstrip(": ").strip()
    query_part, *document_part = _DOCUMENT_MARKER_RE.split(body, maxsplit=1)

    search_query = query_part.strip() or question
    source_filter = None
    if document_part:
        doc_name = document_part[0].strip()
        source_filter = _match_source_name(doc_name, available_sources or [])

    logger.info(
        "Classification : recherche documentaire requise — '%s' (document ciblé : %s)",
        search_query,
        source_filter,
    )
    return True, search_query, source_filter


# ---------- Étape 3 : retrieval dans Qdrant ----------


def retrieve(
    query: str,
    top_k: int | None = None,
    source_file: str | None = None,
    corpus: str | None = "production",
    allowed_categories: list[str] | None = None,
) -> list[dict]:
    """corpus="production" par défaut (les fixtures de test restent invisibles
    en prod). allowed_categories est filtré côté Python après la requête
    Qdrant (cf. category_filter). top_k=None : lu depuis rag_settings au
    moment de l'appel plutôt que figé par défaut de fonction."""
    if top_k is None:
        top_k = get_rag_settings()["top_k_retrieval"]
    vector = embed_query(query)
    must = []
    if corpus is not None:
        must.append(FieldCondition(key="corpus", match=MatchValue(value=corpus)))
    if source_file:
        must.append(FieldCondition(key="source_file", match=MatchValue(value=source_file)))
    qdrant_filter = Filter(must=must) if must else None

    results = qdrant_client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=vector,
        limit=top_k,
        with_payload=True,
        query_filter=qdrant_filter,
    )
    chunks = [
        {
            "text": r.payload.get("text", ""),
            "source_file": r.payload.get("source_file"),
            "page": r.payload.get("page"),
            "score": r.score,
        }
        for r in results.points
        if category_allowed(r.payload.get("source_file"), allowed_categories)
    ]

    logger.info(
        "%d chunks récupérés (corpus=%s, source_file=%s, allowed_categories=%s)",
        len(chunks),
        corpus,
        source_file,
        allowed_categories,
    )
    return chunks


# ---------- Étape 4 : reranking ----------


def rerank(query: str, candidates: list[dict]) -> None:
    """Note chaque candidat (cross-encoder) et annote en place
    candidate["rerank_score"] -- ne filtre plus rien : tous les candidats
    sont transmis au LLM de génération, qui juge lui-même la pertinence.
    Le score sert uniquement à déterminer l'ordre ([Source 1] = le mieux noté)
    et à l'affichage dans le panneau de debug admin."""
    if not candidates:
        return

    reranker = load_reranker_model()
    pairs = [[query, c["text"]] for c in candidates]
    scores = reranker.predict(pairs, batch_size=32)

    for c, s in zip(candidates, scores, strict=True):
        c["rerank_score"] = float(s)

    best = max((c["rerank_score"] for c in candidates), default=0.0)
    logger.info("Reranking : %d candidats notés, meilleur score %.3f", len(candidates), best)
    # Log détaillé pour analyse (DEBUG uniquement, pas en prod).
    logger.debug(
        "Scores détaillés (triés) : %s",
        sorted(
            ((round(c["rerank_score"], 3), c.get("source_file", "?")) for c in candidates),
            reverse=True,
        ),
    )


def _list_all_sources() -> list[str]:
    """Source_file distincts en base (corpus="production")."""
    try:
        result = qdrant_client.scroll(
            collection_name=QDRANT_COLLECTION,
            scroll_filter=Filter(
                must=[FieldCondition(key="corpus", match=MatchValue(value="production"))]
            ),
            limit=1000,
            with_payload=["source_file"],
            with_vectors=False,
        )
        seen = set()
        for point in result[0]:
            sf = point.payload.get("source_file")
            if sf:
                seen.add(sf)
        return list(seen)
    except Exception as exc:
        logger.warning("Liste des sources indisponible (%s)", exc)
        return []


def fetch_full_document(source_file: str, corpus: str = "production") -> list[dict]:
    """Récupère tous les chunks d'un document (scroll Qdrant, sans embedding
    ni reranking), triés par page/chunk_index -- utilisé quand
    classify_question() a identifié le document avec confiance."""
    try:
        result = qdrant_client.scroll(
            collection_name=QDRANT_COLLECTION,
            scroll_filter=Filter(
                must=[
                    FieldCondition(key="corpus", match=MatchValue(value=corpus)),
                    FieldCondition(key="source_file", match=MatchValue(value=source_file)),
                ]
            ),
            limit=1000,
            with_payload=True,
            with_vectors=False,
        )
        chunks = [
            {
                "text": point.payload.get("text", ""),
                "source_file": point.payload.get("source_file"),
                "page": point.payload.get("page"),
                "chunk_index": point.payload.get("chunk_index", 0),
                "score": None,
                "rerank_score": None,
            }
            for point in result[0]
        ]
        chunks.sort(key=lambda c: (c["page"] or 0, c["chunk_index"] or 0))
        return chunks
    except Exception as exc:
        logger.warning("Document complet indisponible (%s) — repli sur le RAG classique", exc)
        return []


def _normalize(text: str) -> str:
    """Minuscules + suppression des accents pour comparaison robuste."""
    import unicodedata

    return unicodedata.normalize("NFD", text.lower()).encode("ascii", "ignore").decode()


# ---------- Étape 5 : génération de la réponse ----------


def _system_prompt(settings: dict) -> str:
    """Prompt système du RAG classique : identité (settings["oracle_identity"])
    + corps éditable par un admin (settings["system_prompt"])."""
    return f"{settings['oracle_identity']}\n\n{settings['system_prompt']}"


def _system_prompt_attachment(settings: dict) -> str:
    """Variante allégée pour les pièces jointes utilisateur : les règles
    strictes du prompt système RAG (grounding, citation) visent les documents
    internes Strattt, pas un fichier fourni directement par l'utilisateur."""
    return f"{settings['oracle_identity']}\n\n{settings['system_prompt_attachment']}"


def build_human_content(
    *,
    question: str,
    history: str | None = None,
    context: str | None = None,
    context_label: str | None = None,
    notice: str | None = None,
) -> str:
    """Compose le message humain à partir de blocs optionnels (historique,
    contexte documentaire avec son intitulé, notice informative) -- remplace
    les anciens templates statiques quasi identiques (un par combinaison de
    blocs présents/absents)."""
    parts = []
    if history is not None:
        parts.append(f"Historique :\n{history}")
    if context is not None:
        label = f"{context_label} :\n" if context_label else ""
        parts.append(f"{label}{context}")
    if notice:
        parts.append(notice)
    parts.append(f"Question : {question}")
    return "\n\n".join(parts)


def build_context(chunks: list[dict]) -> str:
    """Numérote chaque chunk ([Source N]) -- repère utile au LLM pour ne pas
    mélanger des informations issues de documents différents."""
    blocks = []
    for i, c in enumerate(chunks, 1):
        blocks.append(f"[Source {i}: {c['source_file']}, page {c['page']}]\n{c['text']}")
    return "\n\n---\n\n".join(blocks)


def generate_stream_answer(
    session_id: str,
    question: str,
    *,
    allowed_categories: list[str] | None = None,
    is_admin: bool = False,
    attachment_filename: str | None = None,
    attachment_text: str | None = None,
) -> Generator[str]:
    """allowed_categories/is_admin viennent des droits de l'utilisateur
    (Oracle n'a pas son propre système de comptes -- standalone, toujours
    admin par défaut, cf. /session-info). is_admin=False par défaut ici :
    le panneau de debug retrieval n'est jamais transmis sinon.

    attachment_filename/attachment_text : pièce jointe éphémère injectée telle
    quelle dans le prompt, sans passer par retrieve()/rerank()."""
    request_start = time.monotonic()
    settings = get_rag_settings()
    generation_llm = get_generation_llm()
    standalone_question = rewrite_query(session_id, question)

    top_chunks: list[dict] = []
    all_candidates: list[dict] = []
    needs_search = False
    search_query = standalone_question
    full_document_mode = False

    # Sautée si pièce jointe : le retrieval n'a aucune raison d'aboutir dans
    # ce cas (0 retenus observé en pratique).
    if not attachment_text:
        all_sources = _list_all_sources()
        needs_search, search_query, source_filter = classify_question(
            standalone_question, all_sources
        )
        if needs_search:
            try:
                # Le filtrage par droits s'applique aussi en mode document
                # complet -- sinon, repli sur le RAG classique.
                if source_filter and not category_allowed(source_filter, allowed_categories):
                    source_filter = None

                if source_filter:
                    # Document identifié avec confiance : on lui donne tout
                    # son contenu, le LLM final juge lui-même la pertinence
                    # (comme pour une pièce jointe).
                    top_chunks = fetch_full_document(source_filter)
                    all_candidates = top_chunks
                    full_document_mode = bool(top_chunks)

                if not top_chunks:
                    # Repli RAG classique : aucun document ciblé, ou document
                    # ciblé introuvable/vide.
                    all_candidates = retrieve(
                        search_query, source_file=None, allowed_categories=allowed_categories
                    )
                    # rerank() annote les scores mais ne filtre plus ce qui
                    # est transmis au LLM : un chunk clairement pertinent peut
                    # être mal noté si le reranker ne fait pas le lien entre
                    # documents (ex: client mentionné dans un fichier nommé
                    # autrement). On passe tous les candidats, triés par
                    # score décroissant, et le LLM juge lui-même.
                    rerank(search_query, all_candidates)
                    top_chunks = sorted(
                        all_candidates, key=lambda c: c.get("rerank_score") or 0.0, reverse=True
                    )
            except Exception as exc:
                # exc_info=True temporairement (diagnostic) : ce message ne
                # loguait que str(exc), vide pour certaines exceptions (ex :
                # AssertionError sans message) -- impossible de savoir où ni
                # pourquoi ça plantait réellement (Qdrant ? reranker ? autre ?).
                logger.warning(
                    "Retrieval Qdrant indisponible (%s) — réponse sans contexte documentaire",
                    exc,
                    exc_info=True,
                )

    # Panneau "Sources détaillées" calculé après génération, à partir de ce
    # que le LLM a réellement cité (cf. plus bas).

    # Panneau de debug complet (scores, candidats écartés) -- admin uniquement.
    if is_admin:
        # rerank_score peut être None (mode document complet) -- "or 0.0" gère ce cas.
        debug_candidates = sorted(
            all_candidates, key=lambda c: c.get("rerank_score") or 0.0, reverse=True
        )
        debug_payload = {
            "debug_retrieval": {
                "needs_search": needs_search,
                "search_query": search_query,
                "candidates": [
                    {
                        "source_file": c.get("source_file"),
                        "page": c.get("page"),
                        "text": c.get("text"),
                        "vector_score": c.get("score"),
                        "rerank_score": c.get("rerank_score"),
                    }
                    for c in debug_candidates
                ],
            }
        }
        yield f"data: {json.dumps(debug_payload)}\n\n"

    history = get_history(session_id)
    formatted_history = format_history_for_prompt(history) or "(aucun échange précédent)"

    # Pièce jointe : jamais numérotée [Source N], toujours incluse si présente.
    attachment_block = ""
    if attachment_text:
        attachment_block = (
            f"Pièce jointe fournie par l'utilisateur pour cette question "
            f"({attachment_filename or 'fichier'}) :\n{attachment_text}\n\n"
        )

    system_prompt = _system_prompt(settings)
    if top_chunks and full_document_mode:
        context = attachment_block + build_context(top_chunks)
        human_content = build_human_content(
            question=question,
            history=formatted_history,
            context=context,
            context_label="Contexte documentaire (document complet fourni ci-dessous)",
        )
    elif top_chunks:
        context = attachment_block + build_context(top_chunks)
        human_content = build_human_content(
            question=question,
            history=formatted_history,
            context=context,
            context_label="Contexte documentaire (chaque bloc est numéroté [Source N])",
        )
    elif attachment_text:
        # Pas d'historique : pas de bloc "Historique :" artificiel, au plus
        # proche d'un envoi brut dans un chat Ollama.
        system_prompt = _system_prompt_attachment(settings)
        human_content = build_human_content(
            question=question,
            history=formatted_history if history else None,
            context=attachment_block,
        )
    elif needs_search:
        # Recherche tentée mais rien de pertinent trouvé -- on le signale
        # explicitement au LLM plutôt que de le laisser deviner.
        human_content = build_human_content(
            question=question,
            history=formatted_history,
            notice=(
                "Une recherche a été effectuée dans les documents internes pour "
                "répondre à cette question, mais aucun document pertinent n'a été trouvé."
            ),
        )
    else:
        human_content = build_human_content(question=question, history=formatted_history)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_content),
    ]

    for chunk in generation_llm.stream(messages):
        token = chunk.content
        if not token:
            continue
        yield f"data: {json.dumps({'token': token})}\n\n"

    # Sources = tous les chunks passés au LLM (plus de marqueur de citation à
    # parser -- cf. build_context, chaque bloc [Source N] reste distinct dans
    # le contexte mais le LLM n'a plus à en rendre compte explicitement).
    sources = list({c["source_file"] for c in top_chunks})

    total_seconds = round(time.monotonic() - request_start, 1)
    logger.info(
        "[%s] Réponse générée (search=%s, sources=%d, total=%.1fs)",
        session_id,
        needs_search,
        len(sources),
        total_seconds,
    )
    # message_id : généré ici (pas dans add_turn(), appelé par routes.py
    # seulement après consommation complète du générateur) pour que le
    # front reçoive le même identifiant que celui persisté ensuite --
    # nécessaire pour accrocher un feedback (thumbs up/down) à cette
    # réponse précise, cf. app/ragchain/services/feedback.py.
    message_id = str(uuid.uuid4())
    final_payload = {
        "sources": sources,
        "standalone_question": standalone_question,
        "timing": {"total_seconds": total_seconds},
        "message_id": message_id,
    }
    yield f"data: {json.dumps(final_payload)}\n\n"
    yield "data: [DONE]\n\n"
