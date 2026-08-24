import json

from app.core.minio import download_bytes, upload_json_output
from app.ingestion.parsed import load_parsed_pages


def json_exists(json_object_name: str) -> bool:
    try:
        download_bytes(json_object_name)
        return True
    except RuntimeError:
        return False


def load_json_from_minio(json_object_name: str) -> dict:
    return json.loads(download_bytes(json_object_name).decode("utf-8"))


def load_text_from_minio(object_name: str) -> str:
    """Charge le texte d'un document archivé dans MinIO -- réutilise parsed/
    (app.ingestion.parsed.load_parsed_pages), écrit par le pipeline RAG à
    l'ingestion, au lieu de relancer une extraction/OCR indépendante pour le
    même fichier."""
    pages = load_parsed_pages(object_name)
    return "\n".join(p["text"] for p in pages)


def save_json_to_minio(json_filename: str, data: dict) -> str:
    upload_json_output(json.dumps(data, ensure_ascii=False).encode(), json_filename)
    return f"json_output/{json_filename}"
