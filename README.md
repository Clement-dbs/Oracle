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

### Prérequis : Ollama

Le backend ne fournit pas le LLM : il dépend d'[Ollama](https://ollama.com/),
qui doit tourner **en natif sur la machine hôte** (pas dans un conteneur) et
servir le modèle de votre choix.

1. **Installer Ollama** sur la machine hôte : [ollama.com/download](https://ollama.com/download)
   (ou `curl -fsSL https://ollama.com/install.sh | sh` sous Linux).
2. **Lancer Ollama** en l'exposant aux conteneurs Docker :

   ```bash
   OLLAMA_KEEP_ALIVE=-1 OLLAMA_HOST=0.0.0.0 ollama serve
   ```

3. **Télécharger le modèle** choisi, par exemple `qwen2.5:14b` :

   ```bash
   ollama pull qwen2.5:14b
   ```

4. **Reporter ce même nom de modèle** dans `.env`, variable `OLLAMA_MODEL` :

   ```dotenv
   OLLAMA_MODEL=qwen2.5:14b
   ```

Le nom du modèle est libre (tout modèle disponible sur [ollama.com/search](https://ollama.com/search)
fonctionne) tant qu'il est identique entre l'étape `ollama pull` et la
variable `OLLAMA_MODEL` du `.env`.

Au premier démarrage, BGE-M3 et le reranker sont téléchargés automatiquement
(~3.5 Go) et persistés dans un volume dédié.

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
| [`CLAUDE.md`](CLAUDE.md) | **Référence technique** : architecture, stockage (Qdrant/Redis/MinIO), conventions, sécurité, dépendances |

## Déploiement

Image Docker `python:3.11-slim` + Uvicorn, port 8000. Détails complets dans [`CLAUDE.md`](CLAUDE.md).

---

<div align="center">
<sub>Application interne Strattt — non distribuée publiquement.</sub>
</div>
