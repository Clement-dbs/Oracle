from unittest.mock import MagicMock

import app.ingestion.indexer as indexer
from app.core.config import QDRANT_COLLECTION


def _fake_collections(names):
    coll = MagicMock()
    coll.collections = [MagicMock(name=n) for n in names]
    # MagicMock(name=...) réserve l'attribut interne du mock -- on le
    # redéfinit explicitement pour que `.name` renvoie bien la valeur voulue.
    for m, n in zip(coll.collections, names, strict=True):
        m.name = n
    return coll


def test_ensure_collection_skips_creation_if_exists(monkeypatch):
    monkeypatch.setattr(indexer, "client", MagicMock())
    indexer.client.get_collections.return_value = _fake_collections([QDRANT_COLLECTION])

    indexer.ensure_collection()

    indexer.client.create_collection.assert_not_called()


def test_ensure_collection_creates_if_missing(monkeypatch):
    monkeypatch.setattr(indexer, "client", MagicMock())
    indexer.client.get_collections.return_value = _fake_collections(["autre_collection"])

    indexer.ensure_collection()

    indexer.client.create_collection.assert_called_once()
    kwargs = indexer.client.create_collection.call_args.kwargs
    assert kwargs["collection_name"] == QDRANT_COLLECTION


def test_delete_by_source_file_calls_client_delete(monkeypatch):
    monkeypatch.setattr(indexer, "client", MagicMock())

    indexer.delete_by_source_file("ingestion/documents/rapport.pdf")

    indexer.client.delete.assert_called_once()
    assert indexer.client.delete.call_args.kwargs["collection_name"] == QDRANT_COLLECTION


def test_delete_by_source_file_swallows_errors(monkeypatch):
    """Une erreur lors de la suppression ne doit pas remonter -- elle est
    journalisée (comportement existant, préservé)."""
    fake_client = MagicMock()
    fake_client.delete.side_effect = RuntimeError("qdrant down")
    monkeypatch.setattr(indexer, "client", fake_client)

    indexer.delete_by_source_file("x.pdf")  # ne doit pas lever


def test_find_by_content_hash_returns_source_file_when_found(monkeypatch):
    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections([QDRANT_COLLECTION])
    point = MagicMock()
    point.payload = {"source_file": "ingestion/documents/original.pdf"}
    fake_client.scroll.return_value = ([point], None)
    monkeypatch.setattr(indexer, "client", fake_client)

    result = indexer.find_by_content_hash("deadbeef")

    assert result == "ingestion/documents/original.pdf"


def test_find_by_content_hash_returns_none_when_not_found(monkeypatch):
    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections([QDRANT_COLLECTION])
    fake_client.scroll.return_value = ([], None)
    monkeypatch.setattr(indexer, "client", fake_client)

    assert indexer.find_by_content_hash("inexistant") is None


def test_find_by_content_hash_returns_none_on_error(monkeypatch):
    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections([QDRANT_COLLECTION])
    fake_client.scroll.side_effect = RuntimeError("boom")
    monkeypatch.setattr(indexer, "client", fake_client)

    assert indexer.find_by_content_hash("x") is None


def test_index_chunks_builds_points_with_payload(monkeypatch):
    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections([QDRANT_COLLECTION])
    monkeypatch.setattr(indexer, "client", fake_client)

    chunks = [
        {
            "text": "chunk 1",
            "metadata": {"doc_id": "abc", "source_file": "f.pdf", "chunk_index": 0},
        },
        {
            "text": "chunk 2",
            "metadata": {"doc_id": "abc", "source_file": "f.pdf", "chunk_index": 1},
        },
    ]
    vectors = [[0.1] * 1024, [0.2] * 1024]

    indexer.index_chunks(chunks, vectors)

    fake_client.upsert.assert_called_once()
    points = fake_client.upsert.call_args.kwargs["points"]
    assert len(points) == 2
    assert points[0].payload["text"] == "chunk 1"
    assert points[0].payload["doc_id"] == "abc"


def test_index_chunks_batches_large_inputs(monkeypatch):
    """batch_size=256 dans index_chunks : 300 chunks doivent produire 2 appels upsert."""
    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections([QDRANT_COLLECTION])
    monkeypatch.setattr(indexer, "client", fake_client)

    n = 300
    chunks = [{"text": f"c{i}", "metadata": {"chunk_index": i}} for i in range(n)]
    vectors = [[0.0] * 1024 for _ in range(n)]

    indexer.index_chunks(chunks, vectors)

    assert fake_client.upsert.call_count == 2
