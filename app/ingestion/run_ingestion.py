import hashlib
import logging
import uuid

from app.core.minio import category_path, parsed_path, upload_bytes
from app.ingestion.chunking import chunk_document
from app.ingestion.embeddings import embed_texts
from app.ingestion.extract import extract_document
from app.ingestion.indexer import find_by_content_hash, index_chunks
from app.ingestion.parsed import dump_pages

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

EMBED_BATCH_SIZE = 32


def index_extracted_pages(
    pages: list[dict],
    source_file: str,
    doc_id: str,
    content_hash: str | None = None,
    corpus: str = "production",
) -> dict:
    """Chunking + embedding + indexation Qdrant à partir de pages déjà
    extraites -- partagé par process_document() (extraction fraîche) et la
    réindexation depuis parsed/ (app.ingestion.routes)."""
    chunks = chunk_document(
        pages,
        source_file=source_file,
        doc_id=doc_id,
        content_hash=content_hash,
        corpus=corpus,
    )

    if not chunks:
        logger.warning(f"[{source_file}] Aucun chunk généré (fichier vide ou OCR en échec).")
        return {"doc_id": doc_id, "status": "empty", "chunks": 0}

    logger.info(f"[{source_file}] {len(chunks)} chunks générés. Embedding...")
    all_vectors = []
    for i in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[i : i + EMBED_BATCH_SIZE]
        texts = [c["text"] for c in batch]
        vectors = embed_texts(texts)
        all_vectors.extend(vectors)

    logger.info(f"[{source_file}] Indexation dans Qdrant...")
    index_chunks(chunks, all_vectors)

    logger.info(f"[{source_file}] Terminé ({len(chunks)} chunks indexés, doc_id={doc_id})")
    return {"doc_id": doc_id, "status": "done", "chunks": len(chunks)}


def process_document(
    data: bytes,
    filename: str,
    object_name: str | None = None,
    doc_id: str | None = None,
    category: str = "reunion",  # chaîne libre : "reunion", "documents", ou toute autre catégorie
    corpus: str = "production",  # "production" (vrais documents) | "test" (fixtures/éval)
    allow_duplicate: bool = False,
    pages: list[dict] | None = None,
) -> dict:
    """
    Traite un document de bout en bout à partir de ses bytes en mémoire, quel
    que soit son format (cf. app.ingestion.extract.SUPPORTED_EXTENSIONS) :
      0. Vérification de doublon par hash de contenu (SHA-256)
      1. Upload dans MinIO (ingestion/<category>/<filename>) -- fichier brut,
         conservé tel quel pour servir de lien direct vers la source
      2. Extraction du texte (sauf si `pages` déjà fourni, cf. plus bas) +
         persistance markdown (parsed/<category>/<filename>.md)
      3. Chunking / embedding / indexation (index_extracted_pages)

    `pages` : pages déjà extraites, au même format que
    `extract.extract_via_liteparse()` (`[{"page", "text", "method"}, ...]`) --
    permet d'indexer un texte déjà en clair (ex : fiche CRM reformatée par
    app.cockpittt.mongo_sync) sans repasser par `extract_document()`, qui ne
    gère que les formats binaires listés dans `SUPPORTED_EXTENSIONS` et
    rejetterait par exemple un `filename` en `.txt` -- alors qu'il n'y a
    justement rien à « extraire » d'un texte déjà en clair.
    """
    doc_id = doc_id or str(uuid.uuid4())
    dest_name = object_name or filename
    object_name = category_path(category, dest_name)

    content_hash = hashlib.sha256(data).hexdigest()

    if not allow_duplicate:
        existing_source = find_by_content_hash(content_hash)
        if existing_source:
            logger.info(
                f"[{filename}] Doublon détecté (hash identique à {existing_source}) — ingestion ignorée."
            )
            return {
                "doc_id": doc_id,
                "status": "duplicate",
                "chunks": 0,
                "duplicate_of": existing_source,
            }

    logger.info(f"[{filename}] Upload vers MinIO ({object_name})...")
    upload_bytes(data, object_name)

    if pages is None:
        logger.info(f"[{filename}] Extraction du texte...")
        pages = extract_document(data, filename)
    upload_bytes(dump_pages(pages), parsed_path(object_name), content_type="text/markdown")

    return index_extracted_pages(
        pages, source_file=object_name, doc_id=doc_id, content_hash=content_hash, corpus=corpus
    )
