import app.core.health as health_module


def test_check_qdrant_ok(monkeypatch):
    import app.ingestion.indexer as indexer_module

    monkeypatch.setattr(indexer_module.client, "get_collection", lambda name: object())

    ok, message = health_module._check_qdrant()
    assert ok is True
    assert message == "ok"


def test_check_qdrant_down(monkeypatch):
    import app.ingestion.indexer as indexer_module

    def boom(name):
        raise RuntimeError("connexion refusée")

    monkeypatch.setattr(indexer_module.client, "get_collection", boom)

    ok, message = health_module._check_qdrant()
    assert ok is False
    assert "connexion refusée" in message


def test_check_redis_ok(monkeypatch):
    class FakeRedis:
        def ping(self):
            return True

    monkeypatch.setattr(health_module.redis, "from_url", lambda *a, **k: FakeRedis())

    ok, message = health_module._check_redis()
    assert ok is True
    assert message == "ok"


def test_check_redis_down(monkeypatch):
    class FakeRedis:
        def ping(self):
            raise ConnectionError("indisponible")

    monkeypatch.setattr(health_module.redis, "from_url", lambda *a, **k: FakeRedis())

    ok, message = health_module._check_redis()
    assert ok is False
    assert "indisponible" in message


def test_check_minio_ok(monkeypatch):
    import app.core.minio as minio_module

    monkeypatch.setattr(minio_module.client, "bucket_exists", lambda bucket: True)

    ok, message = health_module._check_minio()
    assert ok is True
    assert message == "ok"


def test_check_minio_down(monkeypatch):
    import app.core.minio as minio_module

    def boom(bucket):
        raise RuntimeError("bucket inaccessible")

    monkeypatch.setattr(minio_module.client, "bucket_exists", boom)

    ok, message = health_module._check_minio()
    assert ok is False
    assert "bucket inaccessible" in message


def test_check_ollama_ok(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(health_module.requests, "get", lambda *a, **k: FakeResponse())

    ok, message = health_module._check_ollama()
    assert ok is True
    assert message == "ok"


def test_check_ollama_down(monkeypatch):
    def boom(*a, **k):
        raise health_module.requests.exceptions.ConnectionError("hors ligne")

    monkeypatch.setattr(health_module.requests, "get", boom)

    ok, message = health_module._check_ollama()
    assert ok is False
    assert "hors ligne" in message


def test_check_health_all_ok(monkeypatch):
    monkeypatch.setattr(health_module, "_check_qdrant", lambda: (True, "ok"))
    monkeypatch.setattr(health_module, "_check_redis", lambda: (True, "ok"))
    monkeypatch.setattr(health_module, "_check_minio", lambda: (True, "ok"))
    monkeypatch.setattr(health_module, "_check_ollama", lambda: (True, "ok"))

    result = health_module.check_health()

    assert result["status"] == "ok"
    assert result["services"] == {
        "qdrant": "ok",
        "redis": "ok",
        "minio": "ok",
        "ollama": "ok",
    }


def test_check_health_degraded_when_one_service_down(monkeypatch):
    monkeypatch.setattr(health_module, "_check_qdrant", lambda: (True, "ok"))
    monkeypatch.setattr(health_module, "_check_redis", lambda: (False, "connexion refusée"))
    monkeypatch.setattr(health_module, "_check_minio", lambda: (True, "ok"))
    monkeypatch.setattr(health_module, "_check_ollama", lambda: (True, "ok"))

    result = health_module.check_health()

    assert result["status"] == "degraded"
    assert result["services"]["redis"] == "error: connexion refusée"
    assert result["services"]["qdrant"] == "ok"
