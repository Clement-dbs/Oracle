import logging
from datetime import UTC, datetime

from langchain_text_splitters import MarkdownTextSplitter

from app.core.rag_settings import get_rag_settings

logger = logging.getLogger(__name__)


def _build_splitter() -> MarkdownTextSplitter:
    """Construit un splitter markdown à partir de rag_settings -- les pages
    viennent de LiteParse en markdown (titres, tableaux, listes)."""
    settings = get_rag_settings()
    return MarkdownTextSplitter(
        chunk_size=settings["chunk_size"],
        chunk_overlap=settings["chunk_overlap"],
    )


def chunk_document(
    pages: list[dict],
    source_file: str,
    doc_id: str,
    content_hash: str | None = None,
    corpus: str = "production",
) -> list[dict]:
    chunks = []
    splitter = _build_splitter()
    indexed_at = datetime.now(UTC).isoformat()

    for page_data in pages:
        page_text = page_data["text"]
        if not page_text.strip():
            continue

        page_chunks = splitter.split_text(page_text)

        for idx, chunk_text in enumerate(page_chunks):
            chunks.append(
                {
                    "text": chunk_text,
                    "metadata": {
                        "doc_id": doc_id,
                        "source_file": source_file,
                        "content_hash": content_hash,
                        "page": page_data["page"],
                        "extraction_method": page_data["method"],
                        "chunk_index": idx,
                        "corpus": corpus,
                        "indexed_at": indexed_at,
                    },
                }
            )

    return chunks
