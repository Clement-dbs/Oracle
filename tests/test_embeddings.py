from unittest.mock import MagicMock

import app.ingestion.embeddings as embeddings


def test_embed_texts_returns_dense_vectors(monkeypatch):
    fake_model = MagicMock()
    fake_model.encode.return_value = {
        "dense_vecs": MagicMock(tolist=lambda: [[0.1, 0.2], [0.3, 0.4]])
    }
    monkeypatch.setattr(embeddings, "get_model", lambda: fake_model)

    vectors = embeddings.embed_texts(["texte un", "texte deux"])

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    fake_model.encode.assert_called_once()
    kwargs = fake_model.encode.call_args.kwargs
    assert kwargs["return_dense"] is True
    assert kwargs["return_sparse"] is False


def test_embed_query_returns_single_vector(monkeypatch):
    fake_model = MagicMock()
    fake_model.encode.return_value = {"dense_vecs": MagicMock(tolist=lambda: [[0.5, 0.6]])}
    monkeypatch.setattr(embeddings, "get_model", lambda: fake_model)

    vector = embeddings.embed_query("une question")

    assert vector == [0.5, 0.6]


def test_get_model_downloads_only_if_missing(monkeypatch, tmp_path):
    missing_path = tmp_path / "does_not_exist"
    monkeypatch.setattr(embeddings, "EMBEDDINGS_MODEL_LOCAL_PATH", str(missing_path))
    monkeypatch.setattr(embeddings, "_model", None)

    download_calls = {"n": 0}
    monkeypatch.setattr(
        embeddings,
        "_download_model",
        lambda: download_calls.__setitem__("n", download_calls["n"] + 1),
    )
    monkeypatch.setattr(embeddings, "BGEM3FlagModel", lambda *a, **k: MagicMock())

    embeddings.get_model()

    assert download_calls["n"] == 1
