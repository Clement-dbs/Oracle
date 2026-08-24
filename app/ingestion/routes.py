import logging
import unicodedata
import urllib.parse
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import Response

from app.core.rag_settings import get_rag_settings
from app.ingestion.extract import SUPPORTED_EXTENSIONS, extract_document
from app.ingestion.run_ingestion import process_document
from app.ingestion.upload_status import get_status, list_statuses, set_status

logger = logging.getLogger(__name__)
ingestion_router = APIRouter(prefix="/documents")

ALLOWED_EXTENSIONS = SUPPORTED_EXTENSIONS

_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

_INLINE_MEDIA_TYPES = {"application/pdf", "image/png", "image/jpeg"}


def _run_ingestion_task(data: bytes, original_filename: str, doc_id: str, category: str):
    try:
        set_status(doc_id, original_filename, "processing")
        result = process_document(
            data,
            original_filename,
            doc_id=doc_id,
            category=category,
            corpus="production",
        )
        set_status(doc_id, original_filename, result["status"], chunks=result["chunks"])
    except Exception as e:
        logger.error(f"[{original_filename}] Erreur ingestion: {e}", exc_info=True)
        set_status(doc_id, original_filename, "error", error=str(e))


@ingestion_router.post("/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    category: str = "documents",  # "reunion" | "documents"
):
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        formats = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise HTTPException(status_code=400, detail=f"Formats acceptés : {formats}.")

    doc_id = str(uuid.uuid4())

    data = await file.read()
    filename = file.filename

    max_file_size_mb = get_rag_settings()["max_file_size_mb"]
    size_mb = len(data) / (1024 * 1024)
    if size_mb > max_file_size_mb:
        raise HTTPException(
            status_code=400,
            detail=f"Fichier trop volumineux (max {max_file_size_mb}MB).",
        )

    set_status(doc_id, filename, "processing")
    background_tasks.add_task(_run_ingestion_task, data, filename, doc_id, category)

    return {"doc_id": doc_id, "filename": filename, "status": "processing"}


@ingestion_router.post("/extract-preview")
async def extract_preview(file: UploadFile = File(...)):
    """Extraction de texte à la volée pour une pièce jointe de chat éphémère
    (glisser-déposer / trombone dans la zone de message"""
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        formats = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise HTTPException(status_code=400, detail=f"Formats acceptés : {formats}.")

    data = await file.read()
    max_file_size_mb = get_rag_settings()["max_file_size_mb"]
    size_mb = len(data) / (1024 * 1024)
    if size_mb > max_file_size_mb:
        raise HTTPException(
            status_code=400,
            detail=f"Fichier trop volumineux (max {max_file_size_mb}MB).",
        )

    try:
        pages = extract_document(data, file.filename)
    except Exception as e:
        logger.warning("[extract_preview] Échec extraction %s : %s", file.filename, e)
        raise HTTPException(
            status_code=422, detail="Impossible d'extraire le texte de ce fichier."
        ) from None

    attachment_max_chars = get_rag_settings()["attachment_max_chars"]
    text = "\n\n".join(p["text"] for p in pages if p.get("text", "").strip())
    truncated = len(text) > attachment_max_chars
    if truncated:
        text = text[:attachment_max_chars]

    return {"filename": file.filename, "text": text, "truncated": truncated}


@ingestion_router.get("/status/{doc_id}")
def document_status(doc_id: str):
    status = get_status(doc_id)
    if not status:
        raise HTTPException(status_code=404, detail="doc_id inconnu.")
    return status


@ingestion_router.get("")
def list_documents():
    return list_statuses()


@ingestion_router.get("/serve")
def serve_document(object_name: str):
    """
    Sert un fichier depuis MinIO directement dans le navigateur.
    object_name doit commencer par 'ingestion/' (protection path traversal).
    Exemple : /documents/serve?object_name=ingestion/reunion/Mon%20fichier.pdf
    """
    if not object_name.startswith("ingestion/"):
        raise HTTPException(status_code=400, detail="Chemin non autorisé.")

    from app.core.minio import download_bytes

    try:
        data = download_bytes(object_name)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail="Fichier introuvable.") from e

    filename = object_name.split("/")[-1]
    media_type = _MEDIA_TYPES.get(Path(filename).suffix.lower(), "application/octet-stream")

    disposition = "inline" if media_type in _INLINE_MEDIA_TYPES else "attachment"

    normalized_filename = unicodedata.normalize("NFC", filename)
    ascii_fallback = normalized_filename.encode("ascii", "ignore").decode() or "document"
    ascii_fallback = ascii_fallback.replace('"', "")
    encoded_filename = urllib.parse.quote(normalized_filename)

    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f"{disposition}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded_filename}"
            )
        },
    )


@ingestion_router.delete("")
def delete_document(object_name: str):
    """
    Supprime un document de MinIO (fichier brut + version parsed/) et de
    Qdrant (ses chunks indexés). Contrairement à une suppression faite
    directement dans la console MinIO, ceci garantit que Qdrant ne garde
    jamais de chunks "orphelins" pointant vers un fichier qui n'existe plus.
    object_name doit commencer par 'ingestion/' (protection path traversal),
    même garde-fou que /documents/serve.
    """
    if not object_name.startswith("ingestion/"):
        raise HTTPException(status_code=400, detail="Chemin non autorisé.")

    from app.core.minio import delete_object, parsed_path
    from app.ingestion.indexer import delete_by_source_file

    delete_by_source_file(object_name)

    for obj in (object_name, parsed_path(object_name)):
        try:
            delete_object(obj)
        except RuntimeError as e:
            logger.warning("[delete_document] Suppression MinIO échouée pour %s : %s", obj, e)

    logger.info("[delete_document] %s supprimé (MinIO + Qdrant).", object_name)
    return {"status": "ok", "object_name": object_name}


@ingestion_router.get("/stats")
def qdrant_stats():
    """Nombre de points dans Qdrant + liste des source_files indexés."""
    try:
        from qdrant_client import QdrantClient

        from app.core.config import QDRANT_COLLECTION, QDRANT_URL

        qc = QdrantClient(url=QDRANT_URL)
        info = qc.get_collection(QDRANT_COLLECTION)

        result = qc.scroll(
            collection_name=QDRANT_COLLECTION,
            limit=1000,
            with_payload=["source_file", "indexed_at"],
            with_vectors=False,
        )
        indexed_at_by_source = {
            p.payload["source_file"]: p.payload.get("indexed_at")
            for p in result[0]
            if p.payload.get("source_file")
        }
        # Plus récent en premier ; les documents sans indexed_at (indexés avant
        # l'ajout de ce champ) tombent en dernier plutôt que de fausser le tri.
        sources = [
            {"source_file": source, "indexed_at": indexed_at_by_source[source]}
            for source in sorted(
                indexed_at_by_source, key=lambda s: indexed_at_by_source[s] or "", reverse=True
            )
        ]

        return {
            "collection": QDRANT_COLLECTION,
            "points_count": info.points_count,
            "sources_count": len(sources),
            "sources": sources,
        }
    except Exception as e:
        logger.error("[qdrant_stats] %s", e)
        return {"error": str(e)}
