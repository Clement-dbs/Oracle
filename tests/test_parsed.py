import pytest

from app.ingestion.parsed import dump_pages, load_parsed_pages, parse_pages


def test_dump_parse_roundtrip_multiple_pages():
    pages = [
        {"page": 1, "text": "Premier contenu.", "method": "native"},
        {"page": 2, "text": "Second contenu,\navec une ligne.", "method": "ocr"},
    ]

    parsed = parse_pages(dump_pages(pages))

    assert parsed == pages


def test_dump_parse_roundtrip_empty_page_text():
    pages = [{"page": 1, "text": "", "method": "ocr"}]

    parsed = parse_pages(dump_pages(pages))

    assert parsed == pages


def test_dump_pages_embeds_page_and_method_markers():
    data = dump_pages([{"page": 3, "text": "x", "method": "native"}])

    assert b"<!-- oracle:page=3 method=native -->" in data


# ── load_parsed_pages : réutilisé par le parseur JSON CRM ───────────────────


def test_load_parsed_pages_reads_and_deserializes(monkeypatch):
    """Lit parsed/<...>.md (écrit par process_document() à l'ingestion) et le
    désérialise en pages."""
    import app.core.minio as minio_module

    pages = [{"page": 1, "text": "Contenu déjà extrait.", "method": "native"}]

    monkeypatch.setattr(minio_module, "download_bytes", lambda obj: dump_pages(pages))

    result = load_parsed_pages("ingestion/reunion/f.pdf")

    assert result == pages


def test_load_parsed_pages_propagates_missing_cache(monkeypatch):
    """Si parsed/<...>.md n'existe pas (document jamais ingéré via
    process_document), l'erreur MinIO remonte telle quelle -- pas de fallback
    d'extraction silencieux."""
    import app.core.minio as minio_module

    def boom(_obj):
        raise RuntimeError("introuvable")

    monkeypatch.setattr(minio_module, "download_bytes", boom)

    with pytest.raises(RuntimeError):
        load_parsed_pages("ingestion/reunion/f.pdf")
