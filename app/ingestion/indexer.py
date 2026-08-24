"""
Création de la collection Qdrant (avec quantization scalaire pour économiser la RAM)
et indexation des chunks.
"""

import logging
import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    ScalarQuantization,
    ScalarQuantizationConfig,
    ScalarType,
    VectorParams,
)

from app.core.config import QDRANT_COLLECTION, QDRANT_URL, VECTOR_SIZE

logger = logging.getLogger(__name__)
client = QdrantClient(url=QDRANT_URL)


def ensure_collection():
    existing = [c.name for c in client.get_collections().collections]
    if QDRANT_COLLECTION in existing:
        return

    client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        quantization_config=ScalarQuantization(
            scalar=ScalarQuantizationConfig(
                type=ScalarType.INT8,
                quantile=0.99,
                always_ram=True,
            )
        ),
    )


def find_by_content_hash(content_hash: str) -> str | None:
    """Retourne le source_file déjà indexé pour ce hash de contenu, ou None.

    Complète delete_by_source_file (déduplication par nom) : un même
    fichier renommé ou re-uploadé sous un autre nom a le même hash et est
    ainsi détecté comme doublon avant tout traitement (pas de nouvel
    upload MinIO, pas de ré-embedding).
    """
    ensure_collection()
    try:
        points, _ = client.scroll(
            collection_name=QDRANT_COLLECTION,
            scroll_filter=Filter(
                must=[FieldCondition(key="content_hash", match=MatchValue(value=content_hash))]
            ),
            limit=1,
            with_payload=True,
            with_vectors=False,
        )
        if points:
            return points[0].payload.get("source_file")
        return None
    except Exception as e:
        logger.warning("[find_by_content_hash] Échec de la recherche par hash : %s", e)
        return None


def delete_by_source_file(source_file: str) -> None:
    """Supprime tous les points Qdrant correspondant à un source_file donné.
    Utilisé avant une réindexation pour éviter les doublons.
    """
    try:
        client.delete(
            collection_name=QDRANT_COLLECTION,
            points_selector=Filter(
                must=[FieldCondition(key="source_file", match=MatchValue(value=source_file))]
            ),
        )
        logger.info("[delete_by_source_file] Points supprimés pour : %s", source_file)
    except Exception as e:
        logger.warning("[delete_by_source_file] Échec pour %s : %s", source_file, e)


def delete_by_doc_id(doc_id: str) -> None:
    """Supprime tous les points Qdrant correspondant à un doc_id donné.

    Contrairement à delete_by_source_file, ne dépend pas du nom de fichier --
    utile quand celui-ci peut changer entre deux synchros (ex: mongo_sync.py,
    où le nom de fichier inclut un slug du nom de l'entreprise/contact qui
    peut être renommé, mais dont le doc_id -- l'_id Mongo -- reste stable).
    """
    try:
        client.delete(
            collection_name=QDRANT_COLLECTION,
            points_selector=Filter(
                must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
            ),
        )
        logger.info("[delete_by_doc_id] Points supprimés pour : %s", doc_id)
    except Exception as e:
        logger.warning("[delete_by_doc_id] Échec pour %s : %s", doc_id, e)


def index_chunks(chunks: list[dict], vectors: list[list[float]]):
    """
    chunks : sortie de chunk_document() -> [{"text": str, "metadata": {...}}, ...]
    vectors : embeddings correspondants (même ordre, même longueur)
    """
    ensure_collection()

    points = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        points.append(
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "text": chunk["text"],
                    **chunk["metadata"],
                },
            )
        )

    # Upsert par batch pour ne pas saturer la mémoire / réseau
    batch_size = 256
    for i in range(0, len(points), batch_size):
        client.upsert(collection_name=QDRANT_COLLECTION, points=points[i : i + batch_size])
