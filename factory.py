import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.core.health import check_health
from app.core.rag_settings import get_rag_settings
from app.ingestion.routes import ingestion_router
from app.ragchain.routes import ragchain_router
from app.settings.routes import settings_router

logger = logging.getLogger(__name__)

_STATIC_DIR = "app/static"


def _static_version() -> str:
    """Horodatage (mtime) des assets statiques, utilisé en cache-busting sur
    les <link>/<script> du template. Sans ça, les navigateurs mettent en
    cache script.js/style.css de façon "heuristique" (pas de Cache-Control
    explicite côté StaticFiles) et continuent de servir une version périmée
    après un déploiement, même après un rebuild complet de l'image
    """
    try:
        mtimes = (
            os.path.getmtime(os.path.join(_STATIC_DIR, "style.css")),
            os.path.getmtime(os.path.join(_STATIC_DIR, "script.js")),
        )
        return str(int(max(mtimes)))
    except OSError:
        return "0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Pré-charge les modèles ML au démarrage."""
    logger.info("Chargement des modèles..")
    try:
        from app.ingestion.embeddings import get_model

        get_model()
        logger.info("Modèle d'embedding chargé.")
    except Exception as e:
        logger.error("Erreur chargement embedding : %s", e)

    try:
        from app.ingestion.reranker_model import load_reranker_model

        load_reranker_model()
        logger.info("Modèle de reranking chargé.")
    except Exception as e:
        logger.error("Erreur chargement reranker : %s", e)

    yield


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)

    templates = Jinja2Templates(directory="app/templates")

    # Middlewares
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Enregistrer les routes
    app.include_router(ingestion_router)
    app.include_router(ragchain_router)
    app.include_router(settings_router)

    app.mount(
        "/static",
        StaticFiles(directory="app/static"),
        name="static",
    )

    @app.get("/")
    def root():
        return RedirectResponse(url="/oracle/")

    @app.get("/oracle/")
    async def frontend(request: Request):
        api_base = request.headers.get("x-oracle-api-base", "")
        static_base = request.headers.get("x-oracle-static-base", "/static")

        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "static_version": _static_version(),
                "api_base": api_base,
                "static_base": static_base,
            },
        )

    @app.get("/session-info")
    async def session_info(request: Request):
        """Appelé par le front au chargement pour connaître les droits de
        l'utilisateur courant. Oracle est une app standalone (pas de
        reverse-proxy en amont qui pose ses propres droits) : accès complet
        par défaut."""
        max_history_turns = get_rag_settings()["max_history_turns"]

        return {
            "is_admin": True,
            "can_upload": True,
            "allowed_categories": None,
            "username": "",
            "csrf_token": "",
            "max_history_turns": max_history_turns,
        }

    @app.get("/health")
    def health():
        """Healthcheck global : agrège Qdrant/Redis/MinIO/Ollama (les 4
        dépendances externes d'Oracle)"""
        result = check_health()
        status_code = 200 if result["status"] == "ok" else 503
        return JSONResponse(status_code=status_code, content=result)

    # Handlers d'erreurs globaux

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        return templates.TemplateResponse(request=request, name="errors/404.html", status_code=404)

    @app.exception_handler(500)
    async def server_error(request: Request, exc):
        return templates.TemplateResponse(request=request, name="errors/500.html", status_code=500)

    return app
