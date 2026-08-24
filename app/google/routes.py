import logging
import uuid

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.minio import category_path, download_bytes
from app.google.services.drive import (
    download_file_bytes,
    get_file_metadata,
    with_export_extension,
)
from app.ingestion.run_ingestion import process_document
from app.ingestion.upload_status import set_status

logger = logging.getLogger(__name__)
google_router = APIRouter(prefix="/drive")


class DriveIngestRequest(BaseModel):
    file_id: str
    category: str = "documents"
    force: bool = False  # bypass dédup MinIO et force la réindexation Qdrant


def _run_drive_task(file_id: str, filename: str, category: str, doc_id: str, force: bool = False):
    try:
        content, _, _ = download_file_bytes(file_id)
        set_status(doc_id, filename, "processing")

        if force:
            # Purge les chunks Qdrant existants pour éviter les doublons
            from app.ingestion.indexer import delete_by_source_file

            delete_by_source_file(category_path(category, filename))

        result = process_document(
            content, filename, object_name=filename, doc_id=doc_id, category=category
        )
        set_status(doc_id, filename, result["status"], chunks=result["chunks"])
    except Exception as e:
        logger.error("[drive_ingest][%s] Erreur : %s", filename, e)
        set_status(doc_id, filename, "error", error=str(e))


@google_router.post("/ingest")
async def drive_ingest(req: DriveIngestRequest, background_tasks: BackgroundTasks):
    meta = get_file_metadata(req.file_id)

    if meta.get("mimeType") == "application/vnd.google-apps.folder":
        return JSONResponse(status_code=400, content={"error": "file_id est un dossier."})

    mime = meta.get("mimeType", "")
    filename = with_export_extension(meta["name"], mime)

    # Déduplication MinIO (sauf si force=True)
    object_name = category_path(req.category, filename)
    if not req.force:
        try:
            download_bytes(object_name)
            logger.info("[drive_ingest] %s déjà dans MinIO, skip.", filename)
            return {"status": "already_exists", "object_name": object_name}
        except RuntimeError:
            pass
    else:
        logger.info("[drive_ingest] force=True — réindexation forcée de %s", filename)

    doc_id = str(uuid.uuid4())
    background_tasks.add_task(
        _run_drive_task, req.file_id, filename, req.category, doc_id, req.force
    )
    logger.info("[drive_ingest] %s → doc_id=%s (force=%s)", filename, doc_id, req.force)
    return {"doc_id": doc_id, "filename": filename, "status": "processing"}


@google_router.get("/metadata/{file_id}")
async def drive_metadata(file_id: str):
    meta = get_file_metadata(file_id)
    mime = meta.get("mimeType", "")
    name = with_export_extension(meta.get("name", ""), mime)
    return {"name": name, "mimeType": mime}
