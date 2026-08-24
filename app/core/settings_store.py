"""
Store clé/valeur générique pour les réglages du RAG, backé par MinIO.
"""

import io
import logging

from minio.error import S3Error

from app.core.config import MINIO_BUCKET
from app.core.minio import client, ensure_bucket

logger = logging.getLogger(__name__)

_PREFIX = "settings/"

# Codes S3 renvoyés par MinIO quand l'objet/bucket n'existe pas encore --
# pas une vraie erreur ici, juste "aucun réglage enregistré pour cette clé".
_NOT_FOUND_CODES = {"NoSuchKey", "NoSuchBucket"}


def _object_name(key: str) -> str:
    return f"{_PREFIX}{key}.enc"


def get_setting(key: str) -> str | None:
    response = None
    try:
        ensure_bucket()
        response = client.get_object(MINIO_BUCKET, _object_name(key))
        return response.read().decode("utf-8")
    except S3Error as e:
        if e.code in _NOT_FOUND_CODES:
            return None
        raise RuntimeError(f"Erreur lecture setting '{key}': {e}") from e
    finally:
        if response:
            response.close()
            response.release_conn()


def set_setting(key: str, value: str) -> None:
    try:
        ensure_bucket()
        data = value.encode("utf-8")
        client.put_object(
            MINIO_BUCKET,
            _object_name(key),
            io.BytesIO(data),
            len(data),
            content_type="application/octet-stream",
        )
    except S3Error as e:
        raise RuntimeError(f"Erreur écriture setting '{key}': {e}") from e


def delete_setting(key: str) -> None:
    try:
        client.remove_object(MINIO_BUCKET, _object_name(key))
    except S3Error as e:
        if e.code not in _NOT_FOUND_CODES:
            raise RuntimeError(f"Erreur suppression setting '{key}': {e}") from e
