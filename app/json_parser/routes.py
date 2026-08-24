import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.core.minio import json_output_exists_by_stem
from app.json_parser.services.llm import ParserLLM
from app.json_parser.services.loader import (
    json_exists,
    load_json_from_minio,
    load_text_from_minio,
    save_json_to_minio,
)

logger = logging.getLogger(__name__)
parser_router = APIRouter(prefix="/document-parser")


@parser_router.get("/exists/{stem}")
def json_exists_route(stem: str):
    """Vérifie si json_output/<stem>.json existe dans MinIO — stat uniquement, sans extraction."""
    return {"exists": json_output_exists_by_stem(stem)}


@parser_router.get("/{object_name:path}")
async def get_or_parse(object_name: str, force: bool = False):
    try:
        json_filename = Path(object_name).stem + ".json"
        json_object = f"json_output/{json_filename}"

        if force or not json_exists(json_object):
            logger.info("Extraction en cours pour : %s (force=%s)", object_name, force)
            text = load_text_from_minio(object_name)
            logger.info("Texte extrait (%d chars)", len(text))
            result = ParserLLM(docs=text).generate_json()
            logger.info("LLM OK, sauvegarde dans MinIO...")
            save_json_to_minio(json_filename, result)
            logger.info("JSON sauvegardé : %s", json_object)

        return load_json_from_minio(json_object)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
