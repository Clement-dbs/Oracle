import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

logger = logging.getLogger(__name__)
cockpittt_router = APIRouter(prefix="/documents")


@cockpittt_router.post("/sync-mongo")
async def sync_mongo_now(
    background_tasks: BackgroundTasks,
    collection: str | None = None,
    collections: str | None = None,
    full: bool = False,
):
    """
    Déclenche la synchro Mongo -> RAG (companies/contacts/ticket_transaction,
    cf. app.cockpittt.mongo_sync). Aucun job planifié ne l'appelle
    automatiquement : c'est le déclencheur utilisé par le bouton « Ingérer
    depuis la base » du panneau Documents (settings-panel-documents côté front).

    - collections : liste séparée par des virgules pour restreindre à une
      sélection de tables (ex : "companies,contacts") -- c'est ce
      qu'envoie le front quand l'utilisateur coche certaines cases.
    - collection : variante singulier, conservée pour compat (usage direct
      de l'API en dehors du front, cf. docstring historique) ; ignorée si
      `collections` est fourni.
    - Aucun des deux : toutes les collections.
    - full=true : ignore le dernier sync connu (Redis) et resynchronise tout
      depuis le début plutôt que les seuls enregistrements modifiés depuis
      la dernière fois.
    """
    from app.cockpittt.mongo_sync import COLLECTIONS, sync_all, sync_collection

    if collections:
        selected = [c.strip() for c in collections.split(",") if c.strip()]
    elif collection:
        selected = [collection]
    else:
        selected = None

    if selected:
        unknown = [c for c in selected if c not in COLLECTIONS]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Collection(s) inconnue(s) : {', '.join(unknown)}. "
                    f"Valeurs possibles : {', '.join(COLLECTIONS)}."
                ),
            )

    def _run():
        try:
            if selected:
                results = [sync_collection(name, full=full) for name in selected]
                logger.info("[sync-mongo] %s", results)
            else:
                results = sync_all(full=full)
                logger.info("[sync-mongo] %s", results)
        except Exception as e:
            logger.error("[sync-mongo] Échec : %s", e)

    background_tasks.add_task(_run)
    return {"status": "queued", "collections": selected or list(COLLECTIONS), "full": full}


@cockpittt_router.get("/sync-mongo/status")
def sync_mongo_status():
    """Date du dernier sync par collection (cf. mongo_sync.get_sync_status) --
    affichée dans le panneau Documents à côté de chaque case à cocher."""
    from app.cockpittt.mongo_sync import get_sync_status

    return get_sync_status()
