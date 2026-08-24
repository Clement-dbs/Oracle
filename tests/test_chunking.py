from app.ingestion.chunking import chunk_document


def test_chunk_document_basic_metadata():
    pages = [{"page": 1, "text": "Un texte simple à découper.", "method": "native"}]

    chunks = chunk_document(pages, source_file="doc.pdf", doc_id="abc-123", content_hash="deadbeef")

    assert len(chunks) == 1
    meta = chunks[0]["metadata"]
    assert meta["doc_id"] == "abc-123"
    assert meta["source_file"] == "doc.pdf"
    assert meta["content_hash"] == "deadbeef"
    assert meta["page"] == 1
    assert meta["extraction_method"] == "native"
    assert meta["chunk_index"] == 0


def test_chunk_document_skips_empty_pages():
    pages = [
        {"page": 1, "text": "   ", "method": "ocr"},  # page vide après strip -> ignorée
        {"page": 2, "text": "Contenu réel ici.", "method": "native"},
    ]

    chunks = chunk_document(pages, source_file="doc.pdf", doc_id="abc")

    assert len(chunks) == 1
    assert chunks[0]["metadata"]["page"] == 2


def test_chunk_document_splits_long_text_into_multiple_chunks():
    # CHUNK_SIZE=800 dans l'environnement de test (conftest.py) -> un texte
    # de plusieurs milliers de caractères doit produire plusieurs chunks,
    # avec un chunk_index strictement croissant.
    long_text = "Phrase de test répétée pour dépasser la taille d'un chunk. " * 200
    pages = [{"page": 1, "text": long_text, "method": "native"}]

    chunks = chunk_document(pages, source_file="doc.pdf", doc_id="abc")

    assert len(chunks) > 1
    indices = [c["metadata"]["chunk_index"] for c in chunks]
    assert indices == list(range(len(chunks)))


def test_chunk_document_content_hash_defaults_to_none():
    pages = [{"page": 1, "text": "texte", "method": "native"}]
    chunks = chunk_document(pages, source_file="doc.pdf", doc_id="abc")
    assert chunks[0]["metadata"]["content_hash"] is None
