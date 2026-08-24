<div align="center">

# Oracle

**L'assistant IA interne de Strattt** — pipeline RAG local : ingestion de documents, vectorisation et chatbot via Ollama

[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Qdrant](https://img.shields.io/badge/Qdrant-DC244C?logoColor=white)](https://qdrant.tech/)
[![Ollama](https://img.shields.io/badge/Ollama-local-black)](https://ollama.com/)

</div>

---

## Démarrage rapide

Deux fichiers compose, deux usages (noms sans le point pour éviter que
Compose les détecte automatiquement — toujours préciser `-f`) :

| Fichier | Usage | Image backend |
|---|---|---|
| `docker-compose-dev.yml` | Poste de dev local — hot-reload | build local (`Dockerfile.dev`) |
| `docker-compose-prod.yml` | Production | pull `ghcr.io/ttt-group/oracle:latest` |

```bash
# Local
docker compose -f docker-compose-dev.yml up -d --build

# Prod
docker compose -f docker-compose-prod.yml pull
docker compose -f docker-compose-prod.yml up -d
```

Ollama doit tourner en natif sur la machine hôte (`OLLAMA_KEEP_ALIVE=-1`,
`OLLAMA_HOST=0.0.0.0`). Au premier démarrage, BGE-M3 et le reranker sont
téléchargés automatiquement (~3.5 Go) et persistés dans un volume dédié.

### Publier une image

```bash
./release_ghcr.sh <version> [--latest]
```

## Tests

```bash
pytest
```

Pas de stack Docker isolée : `tests/conftest.py` fixe les variables d'env
requises et stub les dépendances lourdes (FlagEmbedding) pour rester rapide.

## Documentation

Le README reste volontairement court. La référence technique complète vit dans :

| Où | Quoi |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | **Référence technique** : architecture, stockage (Qdrant/Redis/MinIO), intégrations LeCockpittt/Google, conventions, sécurité, dépendances |

## Déploiement

Image Docker `python:3.11-slim` + Uvicorn, port 8000. Détails complets dans [`CLAUDE.md`](CLAUDE.md).

---

<div align="center">
<sub>Application interne Strattt — non distribuée publiquement.</sub>
</div>
