import io
import logging

from minio import Minio
from minio.error import S3Error

from app.core.config import (
    MINIO_BUCKET,
    MINIO_ENDPOINT,
    MINIO_ROOT_PASSWORD,
    MINIO_ROOT_USER,
    MINIO_SECURE,
)

logger = logging.getLogger(__name__)

# ── Préfixes MinIO ───────────────────────────────────────────────────────
PREFIX_INGESTION = "ingestion/"
# Préfixe distinct de PREFIX_INGESTION : /documents/reindex ne scanne que
# "ingestion/" et ne doit jamais reprendre un fichier parsed/ pour un
# document à réindexer.
PREFIX_PARSED = "parsed/"

client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ROOT_USER,
    secret_key=MINIO_ROOT_PASSWORD,
    secure=MINIO_SECURE,
)


def ensure_bucket():
    try:
        if not client.bucket_exists(MINIO_BUCKET):
            client.make_bucket(MINIO_BUCKET)
    except S3Error as e:
        raise RuntimeError(f"Erreur bucket '{MINIO_BUCKET}': {e}") from e


# ── Helpers de chemin ────────────────────────────────────────────────────


def category_path(category: str, filename: str) -> str:
    return f"{PREFIX_INGESTION}{category}/{filename}"


def parsed_path(object_name: str) -> str:
    """Chemin de la version markdown parsée d'un objet 'ingestion/...' : même
    arborescence sous parsed/, avec .md en plus. Couche "silver" de la
    pipeline (bronze = ingestion/, gold = Qdrant) : persistée durablement
    pour éviter de relancer l'extraction/OCR à chaque réindexation."""
    if not object_name.startswith(PREFIX_INGESTION):
        raise ValueError(f"object_name doit commencer par {PREFIX_INGESTION!r}")
    return PREFIX_PARSED + object_name[len(PREFIX_INGESTION) :] + ".md"


# ── Upload ───────────────────────────────────────────────────────────────


def upload_bytes(
    data: bytes, object_name: str, content_type: str = "application/octet-stream"
) -> str:
    """Upload un fichier sur MinIO"""

    try:
        ensure_bucket()
        client.put_object(
            MINIO_BUCKET,
            object_name,
            io.BytesIO(data),
            len(data),
            content_type=content_type,
        )
        return object_name
    except S3Error as e:
        raise RuntimeError(f"Erreur upload bytes '{object_name}': {e}") from e


# ── Download ─────────────────────────────────────────────────────────────


def object_exists(object_name: str) -> bool:
    """Vérifie l'existence d'un objet MinIO via stat (sans le télécharger)."""
    try:
        client.stat_object(MINIO_BUCKET, object_name)
        return True
    except S3Error:
        return False


def download_bytes(object_name: str) -> bytes:
    response = None
    try:
        response = client.get_object(MINIO_BUCKET, object_name)
        return response.read()
    except S3Error as e:
        raise RuntimeError(f"Erreur download '{object_name}': {e}") from e
    finally:
        if response:
            response.close()
            response.release_conn()


# ── Suppression ──────────────────────────────────────────────────────────


def delete_object(object_name: str) -> None:
    """Supprime un objet MinIO. Idempotent : ne lève pas si l'objet a déjà
    été supprimé (cohérent avec delete_by_source_file côté Qdrant, qui ne
    lève pas non plus si aucun point ne correspond)."""
    try:
        client.remove_object(MINIO_BUCKET, object_name)
    except S3Error as e:
        raise RuntimeError(f"Erreur suppression '{object_name}': {e}") from e
