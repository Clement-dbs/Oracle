from app.ingestion.parsed import dump_pages, parse_pages


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
