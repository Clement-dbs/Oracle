from pathlib import Path
from unittest.mock import MagicMock

import app.ingestion.run_ingestion as run_ingestion


def _patch_happy_path(monkeypatch, chunks=None):
    # `chunks=[]` (liste vide explicite, pour tester le cas "empty") doit
    # être distingué de `chunks=None` (valeur par défaut) : un simple
    # `chunks or [...]` confondrait les deux ([] est falsy).
    result_chunks = (
        chunks if chunks is not None else [{"text": "chunk", "metadata": {"chunk_index": 0}}]
    )

    monkeypatch.setattr(run_ingestion, "find_by_content_hash", lambda h: None)
    monkeypatch.setattr(
        run_ingestion, "upload_bytes", MagicMock(return_value="ingestion/documents/f.pdf")
    )
    monkeypatch.setattr(
        run_ingestion,
        "extract_document",
        lambda data, filename: [{"page": 1, "text": "texte", "method": "native"}],
    )
    monkeypatch.setattr(run_ingestion, "chunk_document", lambda *a, **k: result_chunks)
    monkeypatch.setattr(run_ingestion, "embed_texts", lambda texts: [[0.0] * 1024 for _ in texts])
    monkeypatch.setattr(run_ingestion, "index_chunks", MagicMock())


def test_process_document_happy_path(monkeypatch, native_pdf_path):
    _patch_happy_path(monkeypatch)

    data = Path(native_pdf_path).read_bytes()
    result = run_ingestion.process_document(data, "native.pdf", category="documents")

    assert result["status"] == "done"
    assert result["chunks"] == 1
    # 2 appels : fichier brut + version parsed (app.ingestion.parsed)
    assert run_ingestion.upload_bytes.call_count == 2
    run_ingestion.index_chunks.assert_called_once()


def test_process_document_empty_when_no_chunks(monkeypatch, native_pdf_path):
    _patch_happy_path(monkeypatch, chunks=[])

    data = Path(native_pdf_path).read_bytes()
    result = run_ingestion.process_document(data, "native.pdf", category="documents")

    assert result["status"] == "empty"
    assert result["chunks"] == 0
    # Un document vide ne doit pas déclencher d'embedding/indexation inutile.
    run_ingestion.index_chunks.assert_not_called()


def test_process_document_skips_duplicate_content(monkeypatch, native_pdf_path):
    """Doublon détecté par hash : ni upload, ni extraction, ni embedding,
    ni indexation ne doivent être déclenchés."""
    monkeypatch.setattr(
        run_ingestion, "find_by_content_hash", lambda h: "ingestion/documents/original.pdf"
    )
    monkeypatch.setattr(run_ingestion, "upload_bytes", MagicMock())
    monkeypatch.setattr(run_ingestion, "extract_document", MagicMock())
    monkeypatch.setattr(run_ingestion, "index_chunks", MagicMock())

    data = Path(native_pdf_path).read_bytes()
    result = run_ingestion.process_document(data, "native.pdf", category="documents")

    assert result["status"] == "duplicate"
    assert result["duplicate_of"] == "ingestion/documents/original.pdf"
    run_ingestion.upload_bytes.assert_not_called()
    run_ingestion.extract_document.assert_not_called()
    run_ingestion.index_chunks.assert_not_called()


def test_process_document_allow_duplicate_bypasses_check(monkeypatch, native_pdf_path):
    """allow_duplicate=True force le traitement même si un hash identique
    existe déjà -- utile pour une réingestion volontaire."""
    monkeypatch.setattr(
        run_ingestion,
        "find_by_content_hash",
        MagicMock(return_value="ingestion/documents/original.pdf"),
    )
    _patch_happy_path(monkeypatch)
    # _patch_happy_path a réécrit find_by_content_hash -> on le remet volontairement
    # "trouvé" pour vérifier qu'il est bien ignoré quand allow_duplicate=True.
    monkeypatch.setattr(
        run_ingestion,
        "find_by_content_hash",
        MagicMock(return_value="ingestion/documents/original.pdf"),
    )

    data = Path(native_pdf_path).read_bytes()
    result = run_ingestion.process_document(
        data, "native.pdf", category="documents", allow_duplicate=True
    )

    assert result["status"] == "done"
    assert run_ingestion.upload_bytes.call_count == 2


def test_process_document_passes_content_hash_to_chunk_document(monkeypatch, native_pdf_path):
    captured = {}

    def fake_chunk_document(pages, source_file, doc_id, content_hash=None, corpus="production"):
        captured["content_hash"] = content_hash
        return [{"text": "c", "metadata": {"chunk_index": 0}}]

    monkeypatch.setattr(run_ingestion, "find_by_content_hash", lambda h: None)
    monkeypatch.setattr(run_ingestion, "upload_bytes", MagicMock())
    monkeypatch.setattr(
        run_ingestion,
        "extract_document",
        lambda data, filename: [{"page": 1, "text": "t", "method": "native"}],
    )
    monkeypatch.setattr(run_ingestion, "chunk_document", fake_chunk_document)
    monkeypatch.setattr(run_ingestion, "embed_texts", lambda texts: [[0.0] * 1024 for _ in texts])
    monkeypatch.setattr(run_ingestion, "index_chunks", MagicMock())

    import hashlib

    data = Path(native_pdf_path).read_bytes()
    expected_hash = hashlib.sha256(data).hexdigest()
    run_ingestion.process_document(data, "native.pdf", category="documents")

    assert captured["content_hash"] == expected_hash


def test_process_document_dispatches_real_extraction_by_extension(monkeypatch, sample_docx_path):
    """Contrairement aux autres tests, on ne mocke pas extract_document ici :
    on vérifie que process_document() appelle bien la vraie extraction DOCX
    (et pas extract_pdf) quand le fichier a l'extension .docx."""
    monkeypatch.setattr(run_ingestion, "find_by_content_hash", lambda h: None)
    monkeypatch.setattr(run_ingestion, "upload_bytes", MagicMock())
    monkeypatch.setattr(run_ingestion, "embed_texts", lambda texts: [[0.0] * 1024 for _ in texts])
    monkeypatch.setattr(run_ingestion, "index_chunks", MagicMock())

    data = Path(sample_docx_path).read_bytes()
    result = run_ingestion.process_document(data, "sample.docx", category="documents")

    assert result["status"] == "done"
    assert result["chunks"] >= 1
