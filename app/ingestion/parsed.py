"""
Sérialise/désérialise les pages extraites en un seul fichier markdown,
stocké dans MinIO (app.core.minio.parsed_path). Toute ingestion
(app.ingestion.run_ingestion.process_document) écrit systématiquement ce
fichier -- c'est la source que réutilise le parseur JSON CRM pour éviter
une seconde extraction du même document.
"""

import re

_PAGE_MARKER = re.compile(r"<!-- oracle:page=(\d+) method=(\w+) -->\n?")


def dump_pages(pages: list[dict]) -> bytes:
    parts = [f"<!-- oracle:page={p['page']} method={p['method']} -->\n{p['text']}" for p in pages]
    return "\n\n".join(parts).encode("utf-8")


def parse_pages(data: bytes) -> list[dict]:
    text = data.decode("utf-8")
    matches = list(_PAGE_MARKER.finditer(text))
    pages = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        pages.append(
            {
                "page": int(m.group(1)),
                "method": m.group(2),
                "text": text[start:end].strip("\n"),
            }
        )
    return pages


def load_parsed_pages(object_name: str) -> list[dict]:
    """Lit et désérialise la version markdown déjà extraite d'un objet
    'ingestion/...' (écrite par process_document() à l'ingestion, dans
    parsed/). Utilisé par le parseur JSON CRM (app.json_parser.services.
    loader.load_text_from_minio) pour réutiliser l'extraction RAG au lieu
    de la refaire."""
    from app.core.minio import download_bytes, parsed_path

    return parse_pages(download_bytes(parsed_path(object_name)))
