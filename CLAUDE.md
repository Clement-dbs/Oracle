# Oracle — Référence technique

## Architecture

Pipeline RAG (Retrieval-Augmented Generation) interne pour Strattt (cabinet
d'expertise comptable). FastAPI + Qdrant + Redis + MinIO + BGE-M3/reranker +
Ollama, frontend JS natif (SSE streaming). Application standalone : aucune
dépendance à un système externe (CRM, OAuth tiers) pour fonctionner.

- **Framework** : FastAPI, factory pattern (`factory.py` → `create_app()`), routers enregistrés dans l'ordre : `ingestion_router`, `ragchain_router`, `settings_router`
- **Base vectorielle** : Qdrant, collection unique `documents` (constante, pas configurable par env), vecteurs `1024` dims (BGE-M3), distance cosinus, quantization scalaire INT8 pour la RAM
- **Historique / état éphémère** : Redis — historique de conversation, feedback, statut d'ingestion
- **Stockage fichiers** : MinIO, bucket `oracle` (constante) — fichiers bruts sous `ingestion/<category>/...`, markdown déjà extrait sous `parsed/...`
- **Embeddings** : BGE-M3 (`FlagEmbedding.BGEM3FlagModel`, dense uniquement — `return_sparse=False`), chargé lazy, `use_fp16=True`, auto-téléchargé au premier démarrage
- **Reranking** : BGE-Reranker-v2-M3, chargé en usage réel via `sentence_transformers.CrossEncoder` (le download initial passe par `FlagReranker` puis `save_pretrained`) — **annote un score**
- **Génération** : Ollama (natif sur la machine hôte, hors conteneur), un seul modèle chargé (`OLLAMA_MODEL`)
- **Extraction de documents** : LiteParse (`app/ingestion/extract.py`) — tous les formats qu'il gère nativement sont branchés : PDF, DOCX, PPTX, XLSX, PNG/JPG (`SUPPORTED_EXTENSIONS`). `.txt`/`.md`/`.json` restent hors périmètre (LiteParse les rejette lui-même : "unsupported file format")
- **Auth front** : pas de compte utilisateur propre, pas de reverse-proxy requis -- accès complet par défaut (cf. `GET /session-info` dans `factory.py`). Un header optionnel `x-oracle-user-id` permet de scoper les conversations si Oracle est un jour placé derrière un proxy qui le pose
- **Réglages RAG** : centralisés dans `app/core/rag_settings.py`, persistés en MinIO (pas de collection Mongo/Redis dédiée), cache mémoire 30s
- **Déploiement** : Docker + Uvicorn, image `python:3.11-slim`, port 8000 (mappé 8001 en dev/prod)

## Structure des fichiers clés

Chaque domaine vit dans son propre `app/<domaine>/` (routes.py + services/services.py dédiés).

```
factory.py              # App factory : lifespan (préchargement embeddings/reranker), CORS,
                         # routers, routes /, /oracle/, /session-info, /health
main.py                 # uvicorn.run("main:app", host=0.0.0.0, port=8000, reload=True)
requirements.txt        # Dépendances pip (prod, pinné exhaustivement)
requirements-dev.txt    # + ruff/vulture/pytest + fixtures de test (pymupdf/python-docx/python-pptx/openpyxl/pillow)
Dockerfile              # python:3.11-slim, tesseract-ocr(+fra), libgl1, uv pour l'install
Dockerfile.dev          # idem + requirements-dev.txt
docker-compose-dev.yml         # projet oracle-dev, build local, bind mounts (hot-reload), port 8001
docker-compose-prod.yml        # projet oracle-prod, image :latest, réseau internal isolé, port 8001
release_ghcr.sh         # Publie l'image sur GHCR -- contient un token, ne JAMAIS committer de changement dessus
app/
  core/
    config.py           # os.getenv(...) direct (pas de pydantic-settings malgré la dépendance déclarée)
    health.py            # check_health() -- healthcheck agrégé Qdrant/Redis/MinIO/Ollama, exposé sur GET /health
    llm.py               # Wrapper Ollama (LangChain) -- classe LLM réutilisée par ragchain
    minio.py              # Client MinIO, chemins parsed_path()/ingestion, download/upload/delete
    rag_settings.py       # DEFAULTS + get/save/reset_rag_settings(), cache mémoire 30s, persistance MinIO
    settings_store.py     # get_setting()/set_setting() génériques -- objets S3 settings/{key}.enc
  ingestion/
    routes.py            # /documents : upload, extract-preview, status, list, serve, delete, stats
    run_ingestion.py      # process_document() -- pipeline complet bytes -> parsed/ -> chunks -> Qdrant
    extract.py            # Extraction via LiteParse (PDF/DOCX/PPTX/XLSX/PNG/JPG, texte natif + OCR de secours) + SUPPORTED_EXTENSIONS
    parsed.py             # dump_pages()/parse_pages() -- cache markdown MinIO (parsed/), une seule extraction par document
    chunking.py           # MarkdownTextSplitter (langchain), paramétré par rag_settings.chunk_size/overlap
    embeddings.py          # BGE-M3, embed_texts()/embed_query(), dense uniquement
    reranker_model.py      # Chargement BGE-Reranker-v2-M3 (CrossEncoder)
    indexer.py            # Qdrant : index_chunks(), find_by_content_hash(), delete_by_source_file/doc_id()
    upload_status.py       # Suivi Redis du statut d'ingestion par doc_id (polling, pas SSE)
  ragchain/
    routes.py             # /chat, /conversations, /{session_id}/messages/{message_id}/feedback
    services/
      rag_chain.py         # classify_question() (gate LLM), retrieve(), rerank() (annote sans filtrer),
                           # generate_stream_answer() (SSE : timing/sources/message_id)
      category_filter.py   # GATED_CATEGORIES = {"reunion", "documents"} -- actif seulement si l'appelant
                           # transmet une liste explicite de catégories autorisées
      conversations.py     # CRUD conversations Redis (conversations:{owner}, conv_meta:{sid}), TTL = conversation_ttl_days
      memory.py             # add_turn()/format_history_for_prompt() -- fenêtre glissante chat_history:{sid}
      feedback.py           # save/get/list_feedback() -- feedback:{sid}:{mid}, pas de TTL (sauf cascade suppression)
      schema.py             # ChatRequest/ChatAttachment (Pydantic)
  settings/
    routes.py               # GET/PUT/POST /settings/rag(/reset) -- sans restriction (app standalone)
  static/                 # script.js, style.css (2 fichiers, pas de bundler)
  templates/              # index.html (SPA-like), errors/404.html, errors/500.html
tests/                    # pytest direct (pas de Makefile, pas de stack Docker isolée)
  conftest.py             # Fixtures PDF/DOCX (fitz/python-docx), stub FlagEmbedding, env vars requises
```

## Stockage — détail par backend

**Qdrant** (collection `documents`) — payload par point : `text`, `doc_id`,
`source_file`, `content_hash` (SHA-256, dédup), `page`, `extraction_method`,
`chunk_index`, `corpus`, `indexed_at`.

**MinIO** (bucket `oracle`) — `ingestion/<category>/<fichier>` (brut),
`parsed/<category>/<fichier>.md` (markdown déjà extrait, cf. `parsed.py`),
`settings/{key}.enc` (réglages persistés, ex. `rag_settings`).

**Redis** — `chat_history:{session_id}` (historique), `conversations:{owner}`
(sorted set), `conv_meta:{session_id}` (hash), `feedback:{session_id}:{message_id}`
+ `feedback:all`, `doc_status:{doc_id}` + `doc_status_index` (statut d'ingestion,
TTL 30j).

## Réglages RAG

`GET/PUT/POST /settings/rag(/reset)` (`app/settings/routes.py`) --
`RagSettingsUpdate` valide les bornes (`chunk_size` 100-4000,
`chunk_overlap` 0-1000, `top_k_retrieval` 1-200, `max_history_turns`
1-200, `conversation_ttl_days` 1-365, `attachment_max_chars` 500-200000,
`max_file_size_mb` 1-1000, `temperature` 0.0-2.0) + les prompts texte
libres (`oracle_identity`, `rewrite_prompt`, `classify_prompt`,
`system_prompt`, `system_prompt_attachment`). Store réel dans
`app/core/rag_settings.py` (`DEFAULTS` + override MinIO + cache 30s) --
éditer `DEFAULTS` dans le code n'a d'effet que sur un déploiement qui n'a
jamais eu de valeur personnalisée sauvegardée via l'UI.

## Conventions de sécurité (appliquées, à maintenir)

- **CORS ouvert à `*`** (`allow_origins=["*"]` dans `factory.py`) -- assumé : Oracle reste toujours déployé au sein du réseau interne de l'entreprise, jamais exposé à l'extérieur
- **Path traversal** : `GET/DELETE /documents` exige que `object_name` commence par `ingestion/`
- **Erreurs** : ne jamais exposer de stack trace au client -- logger côté serveur, message générique
- **Uploads** : extension validée contre `SUPPORTED_EXTENSIONS` (`.pdf`/`.docx`/`.pptx`/`.xlsx`/`.png`/`.jpg`/`.jpeg`), taille max `max_file_size_mb` (rag_settings)

## Conventions de code

### Python
- Français dans les commentaires et messages utilisateur, anglais dans le code (variables, fonctions)
- Un router par fichier `routes.py`, logique métier dans `services.py`/`services/`
- Logging : `logger = logging.getLogger(__name__)` par module
- Bare except interdit : toujours `except Exception` ou plus spécifique
- Lint + format : Ruff (`pyproject.toml`, règles `E/F/I/B/UP/SIM`, `E501` ignoré, `line-length=100`, `target-version=py311`)
- Tests : pytest direct (`pythonpath=["."]`, `testpaths=["tests"]`), pas de stack Docker isolée -- `conftest.py` fixe les env vars requises par `config.py` et stub `FlagEmbedding` pour éviter de tirer torch/CUDA aux tests unitaires

### JavaScript (`app/static/script.js`)
- Vanilla JS, pas de framework, pas de bundler
- SSE (Server-Sent Events) pour le streaming des réponses de chat
- `activeStreams` (Map session_id -> DOM détaché) permet de changer de conversation pendant une génération sans la perdre -- réattachée telle quelle par `openConversation()` si elle existe encore

## Dépendances principales

| Package | Usage |
|---|---|
| fastapi / starlette / uvicorn | Framework web |
| qdrant-client | Base vectorielle |
| redis | Historique, feedback, statut d'ingestion |
| minio | Stockage fichiers |
| flagembedding / sentence-transformers / torch / transformers | Embeddings BGE-M3 + reranker |
| langchain-core / langchain-ollama / langchain-text-splitters | Chunking + wrapper LLM Ollama |
| liteparse | Extraction PDF, DOCX, PPTX, XLSX, PNG/JPG |
| pydantic / pydantic-settings | Modèles -- `pydantic-settings` déclaré mais non utilisé (config.py fait de l'`os.getenv` direct) |

## Roadmap / TODO

- [ ] Stratégie de snapshot/backup Qdrant (volume persistant, mais pas de sauvegarde automatique)
- [ ] Authentification propre côté Oracle (aujourd'hui : app standalone, accès complet par défaut, pas de compte utilisateur)

## Points d'attention

- **XSS connu, non corrigé (choix assumé)** : `app/static/script.js` fait `innerHTML = marked.parse(...)` sans sanitize (lignes ~322 et ~881) -- une réponse LLM contenant du HTML/attributs piégés (ex. `onerror`) s'exécute dans le navigateur, et reste stockée dans l'historique de conversation (rejouée à chaque réouverture). Un correctif (DOMPurify vendoré) a été fait puis retiré à la demande -- si on veut le réappliquer : vendorer `marked.min.js`+`purify.min.js` dans `app/static/` (au lieu du CDN pour marked), et envelopper les deux `innerHTML = marked.parse(...)` avec `DOMPurify.sanitize(...)`
- **Rerank sans filtrage** : `rerank()` annote un score mais ne filtre plus aucun candidat -- `top_k_retrieval` (rag_settings) est donc le seul levier qui contrôle le volume envoyé au LLM
- **`release_ghcr.sh`** contient un token GHCR en clair (géré à la main par l'utilisateur) -- ne jamais le committer, ne jamais le lire à voix haute
