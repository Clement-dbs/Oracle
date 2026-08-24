# Oracle — Référence technique

## Architecture

Pipeline RAG (Retrieval-Augmented Generation) interne pour Strattt (cabinet
d'expertise comptable). FastAPI + Qdrant + Redis + MinIO + BGE-M3/reranker +
Ollama, frontend JS natif (SSE streaming), intégré à un second projet Flask
("LeCockpittt") via sync MongoDB et embed iframe.

- **Framework** : FastAPI, factory pattern (`factory.py` → `create_app()`), routers enregistrés dans l'ordre : `oauth_router`, `ingestion_router`, `cockpittt_router`, `parser_router`, `ragchain_router`, `google_router`, `settings_router`
- **Base vectorielle** : Qdrant, collection unique `documents` (constante, pas configurable par env), vecteurs `1024` dims (BGE-M3), distance cosinus, quantization scalaire INT8 pour la RAM
- **Historique / état éphémère** : Redis — historique de conversation, feedback, statut d'ingestion, état de sync Mongo (voir détail plus bas)
- **Stockage fichiers** : MinIO, bucket `oracle` (constante) — fichiers bruts sous `ingestion/<category>/...`, markdown déjà extrait sous `parsed/...`, sorties JSON du parseur CRM sous `json_output/...`
- **Embeddings** : BGE-M3 (`FlagEmbedding.BGEM3FlagModel`, dense uniquement — `return_sparse=False`), chargé lazy, `use_fp16=True`, auto-téléchargé au premier démarrage
- **Reranking** : BGE-Reranker-v2-M3, chargé en usage réel via `sentence_transformers.CrossEncoder` (le download initial passe par `FlagReranker` puis `save_pretrained`) — **annote un score**
- **Génération** : Ollama (natif sur la machine hôte, hors conteneur), un seul modèle chargé (`OLLAMA_MODEL`)
- **Extraction de documents** : LiteParse (`app/ingestion/extract.py`) — tous les formats qu'il gère nativement sont branchés : PDF, DOCX, PPTX, XLSX, PNG/JPG (`SUPPORTED_EXTENSIONS`). `.txt`/`.md`/`.json` restent hors périmètre (LiteParse les rejette lui-même : "unsupported file format")
- **Auth front** : pas de compte utilisateur propre — Oracle fait confiance à un header contract posé par le reverse-proxy LeCockpittt (voir section dédiée). Connexion Google OAuth séparée, nécessaire pour l'ingestion Drive
- **Réglages RAG** : centralisés dans `app/core/rag_settings.py`, persistés en MinIO (pas de collection Mongo/Redis dédiée), cache mémoire 30s
- **Déploiement** : Docker + Uvicorn, image `python:3.11-slim`, port 8000 (mappé 8001 en dev/prod, 8002 en dev-server)

## Structure des fichiers clés

Chaque domaine vit dans son propre `app/<domaine>/` (routes.py + services/services.py dédiés), sur le même principe de sub-package que LeCockpittt.

```
factory.py              # App factory : lifespan (préchargement embeddings/reranker), CORS,
                         # routers, exception handler GoogleAuthRequired, routes /, /oracle/, /session-info, /health
main.py                 # uvicorn.run("main:app", host=0.0.0.0, port=8000, reload=True)
requirements.txt        # Dépendances pip (prod, pinné exhaustivement)
requirements-dev.txt    # + ruff/vulture/pytest + fixtures de test (pymupdf/python-docx/python-pptx/openpyxl/pillow)
Dockerfile              # python:3.11-slim, tesseract-ocr(+fra), libgl1, uv pour l'install
Dockerfile.dev          # idem + requirements-dev.txt
docker-compose-dev.yml         # projet oracle-dev, build local, bind mounts (hot-reload), port 8001
docker-compose-dev-server.yml  # projet oracle-dev, image ghcr.io/ttt-group/oracle:dev (pull), port 8002
docker-compose-prod.yml        # projet oracle-prod, image :latest, réseau internal isolé, port 8001
release_ghcr.sh         # Publie l'image sur GHCR -- contient un token, ne JAMAIS committer de changement dessus
app/
  core/
    config.py           # os.getenv(...) direct (pas de pydantic-settings malgré la dépendance déclarée)
    health.py            # check_health() -- healthcheck agrégé Qdrant/Redis/MinIO/Ollama, exposé sur GET /health
    llm.py               # Wrapper Ollama (LangChain) -- classe LLM réutilisée par ragchain ET json_parser
    minio.py              # Client MinIO, chemins parsed_path()/ingestion, download/upload/delete
    rag_settings.py       # DEFAULTS + get/save/reset_rag_settings(), cache mémoire 30s, persistance MinIO
    settings_store.py     # get_setting()/set_setting() génériques -- objets S3 settings/{key}.enc
    secret_key.py         # get_or_create_secret_key() (ORACLE_SECRET_KEY, sinon généré+persisté)
  ingestion/
    routes.py            # /documents : upload, extract-preview, status, list, serve, delete, stats
    run_ingestion.py      # process_document() -- pipeline complet bytes -> parsed/ -> chunks -> Qdrant
    extract.py            # Extraction via LiteParse (PDF/DOCX/PPTX/XLSX/PNG/JPG, texte natif + OCR de secours) + SUPPORTED_EXTENSIONS
    parsed.py             # dump_pages()/parse_pages()/load_parsed_pages() -- cache markdown MinIO (parsed/),
                          # partagé entre le pipeline RAG et json_parser (une seule extraction par document)
    chunking.py           # MarkdownTextSplitter (langchain), paramétré par rag_settings.chunk_size/overlap
    embeddings.py          # BGE-M3, embed_texts()/embed_query(), dense uniquement
    reranker_model.py      # Chargement BGE-Reranker-v2-M3 (CrossEncoder)
    indexer.py            # Qdrant : index_chunks(), find_by_content_hash(), delete_by_source_file/doc_id()
    upload_status.py       # Suivi Redis du statut d'ingestion par doc_id (polling, pas SSE)
  ragchain/
    routes.py             # /chat, /conversations, /{session_id}/messages/{message_id}/feedback, /session-info-like admin gating
    services/
      rag_chain.py         # classify_question() (gate LLM), retrieve(), rerank() (annote sans filtrer),
                           # generate_stream_answer() (SSE : timing/sources/message_id)
      category_filter.py   # GATED_CATEGORIES = {"reunion", "documents"} -- mongo_sync/* jamais filtré ici
      conversations.py     # CRUD conversations Redis (conversations:all, conv_meta:{sid}), TTL = conversation_ttl_days
      memory.py             # add_turn()/format_history_for_prompt() -- fenêtre glissante chat_history:{sid}
      feedback.py           # save/get/list_feedback() -- feedback:{sid}:{mid}, pas de TTL (sauf cascade suppression)
      schema.py             # ChatRequest/ChatAttachment (Pydantic)
  json_parser/            # Parsing structuré (CRM LeCockpittt) -- réutilise parsed/ du pipeline RAG
    routes.py             # /document-parser : exists/{stem}, GET /{object_name} (génère+cache JSON)
    services/
      llm.py               # ParserLLM(LLM) -- même Ollama que le RAG, prompt dédié extraction PROSPECT
      schema.py            # ExtractedData (Pydantic) -- forme_juridique en Literal fermé
      loader.py             # load_text_from_minio() -- via load_parsed_pages(), pas de ré-extraction
  cockpittt/              # Intégration LeCockpittt -> Oracle (sync CRM pour le RAG)
    routes.py              # POST /documents/sync-mongo (background task, aucun scheduler côté Oracle)
    mongo_sync.py           # sync_collection()/sync_all() -- reformate en texte, indexe via process_document()
    mongo_schemas.py        # CompanyDoc/ContactDoc/TransactionDoc (Pydantic, extra="ignore")
    lecockpittt_client.py    # Client HTTP /api/external (Bearer LECOCKPITTT_API_KEY)
  google/                 # OAuth Google (compte unique) + ingestion Drive
    oauth.py                # /auth/google, /auth/google/callback, /auth/status, DELETE /auth/google
    crypto.py                # Chiffrement Fernet des credentials (clé = ORACLE_SECRET_KEY)
    routes.py                # /drive/ingest, /drive/metadata/{file_id}
    services/drive.py        # Google Drive API v3, export Docs/Sheets/Slides -> PDF/XLSX/PPTX
  settings/
    routes.py               # GET/PUT/POST /settings/rag(/reset) -- gating admin (x-oracle-is-admin)
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
`json_output/<stem>.json` (sorties json_parser), `settings/{key}.enc`
(réglages persistés, ex. `rag_settings`, `google_credentials`).

**Redis** — `chat_history:{session_id}` (historique), `conversations:all`
(sorted set), `conv_meta:{session_id}` (hash), `feedback:{session_id}:{message_id}`
+ `feedback:all`, `doc_status:{doc_id}` + `doc_status_index` (statut d'ingestion,
TTL 30j), `mongo_sync:last_sync:{collection}` + `mongo_sync:file:{collection}:{record_id}`
(état de la sync Mongo).

## Intégration LeCockpittt (sync CRM → RAG)

`app/cockpittt/` synchronise `companies`/`contacts`/`ticket_transaction`
(MongoDB LeCockpittt) vers le pipeline RAG, pour que le chatbot réponde sur
les clients/prospects. Déclenchement **uniquement à la demande** via
`POST /documents/sync-mongo?collections=companies,contacts&full=false`
(background task ; `collections` accepte une liste séparée par des virgules,
`collection` singulier conservé pour compat, aucun des deux = toutes) --
**aucun scheduler côté Oracle**. `GET /documents/sync-mongo/status` renvoie
la date de dernière synchro par collection (`mongo_sync.get_sync_status()`).
Chaque enregistrement est reformaté en texte brut lisible puis indexé comme
un document classique (catégorie `mongo_sync/<collection>`,
`allow_duplicate=True`, `doc_id` = `_id` Mongo -- purge Qdrant par
`delete_by_doc_id`, indépendante du nom de fichier en cas de renommage).
Auth vers LeCockpittt : `Authorization: Bearer {LECOCKPITTT_API_KEY}` sur
`{LECOCKPITTT_API_URL}/api/external/{companies,contacts,crm/transactions}/list`
(droit `oracle_sync`, compte de service dédié -- routes ajoutées côté
LeCockpittt en même temps que le bouton front ci-dessous, elles n'existaient
pas avant bien que le reste du pipeline soit déjà en place).

Front : bouton **« Ingérer la sélection »** dans l'onglet Documents de la
modale Paramètres (`settings-panel-documents`, sous la dropzone d'upload),
visible aux mêmes utilisateurs que la dropzone (droit `oracle_upload`) --
une case à cocher par collection (+ date de dernière synchro) et une option
« Resynchronisation complète » (`full=true`). Cf. section
« Base de données Cockpittt » dans `app/static/script.js`.

## Intégration Google (OAuth + Drive)

Compte Google unique (pas multi-utilisateur), scopes `drive.readonly` +
`userinfo.email` (ce second scope sert uniquement à vérifier le domaine,
cf. plus bas), requis pour ingérer des fichiers Drive dans le pipeline RAG
(`POST /drive/ingest`, export automatique Docs→PDF / Sheets→XLSX /
Slides→PPTX). Credentials chiffrés (Fernet, clé `ORACLE_SECRET_KEY`) et
persistés via `settings_store` (`google_credentials`). Tant qu'aucun compte
n'est connecté, `GET /oracle/` redirige vers `/auth/google` (le front ne
démarre qu'après connexion Google).

**Restriction de domaine** : si `GOOGLE_OAUTH_ALLOWED_DOMAIN` est défini,
`google_callback()` (`app/google/oauth.py`) appelle l'endpoint userinfo de
Google avec l'access token reçu, compare le domaine de l'email au réglage
(insensible à la casse) et refuse (403, credentials jamais stockés) en cas
de non-correspondance ou d'échec de la vérification elle-même. Comportement
permissif si la variable n'est pas définie (pas de restriction configurée).

## Contrat de confiance reverse-proxy (LeCockpittt)

Oracle n'a pas d'auth propre : il fait confiance aux headers posés par
LeCockpittt en frontal (`x-oracle-is-admin`, `x-oracle-can-upload`,
`x-oracle-allowed-categories`, `x-oracle-username`, `x-oracle-csrf-token`),
lus dans `GET /session-info` (`factory.py`). **Comportement permissif si les
headers sont absents** (accès direct/dev sans proxy, `is_admin=True` par
défaut) -- refus 403 seulement si le header est présent et différent de
`"1"`. Même logique dupliquée à l'identique (non mutualisée) dans
`app/settings/routes.py` et `app/ragchain/routes.py` (`_require_admin`).

## Réglages RAG (admin)

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
- **Auth Google** : credentials jamais en clair (chiffrement Fernet), jamais renvoyés au client
- **Auth LeCockpittt→Oracle** : Bearer token (`LECOCKPITTT_API_KEY`), jamais en query string
- **Gating admin** : header `x-oracle-is-admin` posé par le proxy -- ne pas ajouter de route sensible sans vérifier ce gating
- **Erreurs** : ne jamais exposer de stack trace au client -- logger côté serveur, message générique
- **Uploads** : extension validée contre `SUPPORTED_EXTENSIONS` (`.pdf`/`.docx`/`.pptx`/`.xlsx`/`.png`/`.jpg`/`.jpeg`), taille max `max_file_size_mb` (rag_settings)
- **Secret applicatif** : `ORACLE_SECRET_KEY` généré et persisté automatiquement si absent (`app/core/secret_key.py`) -- ne pas committer de valeur en dur

## Conventions de code

### Python
- Français dans les commentaires et messages utilisateur, anglais dans le code (variables, fonctions)
- Un router par fichier `routes.py`, logique métier dans `services.py`/`services/`
- Logging : `logger = logging.getLogger(__name__)` par module
- Bare except interdit : toujours `except Exception` ou plus spécifique
- Lint + format : Ruff (`pyproject.toml`, règles `E/F/I/B/UP/SIM`, `E501` ignoré, `line-length=100`, `target-version=py311`)
- Tests : pytest direct (`pythonpath=["."]`, `testpaths=["tests"]`), pas de stack Docker isolée (contrairement à LeCockpittt) -- `conftest.py` fixe les env vars requises par `config.py` et stub `FlagEmbedding` pour éviter de tirer torch/CUDA aux tests unitaires

### JavaScript (`app/static/script.js`)
- Vanilla JS, pas de framework, pas de bundler
- SSE (Server-Sent Events) pour le streaming des réponses de chat
- `activeStreams` (Map session_id -> DOM détaché) permet de changer de conversation pendant une génération sans la perdre -- réattachée telle quelle par `openConversation()` si elle existe encore

## Dépendances principales

| Package | Usage |
|---|---|
| fastapi / starlette / uvicorn | Framework web |
| qdrant-client | Base vectorielle |
| redis | Historique, feedback, statut d'ingestion, état de sync |
| minio | Stockage fichiers |
| flagembedding / sentence-transformers / torch / transformers | Embeddings BGE-M3 + reranker |
| langchain-core / langchain-ollama / langchain-text-splitters | Chunking + wrapper LLM Ollama |
| liteparse | Extraction PDF, DOCX, PPTX, XLSX, PNG/JPG |
| google-api-python-client / google-auth* | OAuth + Drive |
| cryptography | Chiffrement Fernet des credentials Google |
| pydantic / pydantic-settings | Modèles -- `pydantic-settings` déclaré mais non utilisé (config.py fait de l'`os.getenv` direct) |

## Roadmap / TODO

- [ ] Stratégie de snapshot/backup Qdrant (volume persistant, mais pas de sauvegarde automatique)
- [ ] Authentification propre côté Oracle (aujourd'hui : confiance totale dans les headers `x-oracle-*` du reverse-proxy LeCockpittt)

## Points d'attention

- **XSS connu, non corrigé (choix assumé)** : `app/static/script.js` fait `innerHTML = marked.parse(...)` sans sanitize (lignes ~322 et ~881) -- une réponse LLM contenant du HTML/attributs piégés (ex. `onerror`) s'exécute dans le navigateur, et reste stockée dans l'historique de conversation (rejouée à chaque réouverture). Un correctif (DOMPurify vendoré) a été fait puis retiré à la demande -- si on veut le réappliquer : vendorer `marked.min.js`+`purify.min.js` dans `app/static/` (au lieu du CDN pour marked), et envelopper les deux `innerHTML = marked.parse(...)` avec `DOMPurify.sanitize(...)`
- **Gating admin dupliqué** (`_require_admin` sur header `x-oracle-is-admin`) dans `app/settings/routes.py` ET `app/ragchain/routes.py`, non mutualisé -- garder les deux synchronisés si la logique change
- **Aucun scheduler (APScheduler) côté Oracle** : la sync Mongo est strictement à la demande (`POST /documents/sync-mongo`)
- **Rerank sans filtrage** : `rerank()` annote un score mais ne filtre plus aucun candidat -- `top_k_retrieval` (rag_settings) est donc le seul levier qui contrôle le volume envoyé au LLM
- **`release_ghcr.sh`** contient un token GHCR en clair (géré à la main par l'utilisateur) -- ne jamais le committer, ne jamais le lire à voix haute
