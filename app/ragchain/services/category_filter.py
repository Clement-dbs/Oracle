"""
Filtre de catégories documentaires -- restriction d'accès par droits
(schema.ChatRequest.allowed_categories, calculés en amont de l'appel).

Extrait dans son propre module, sans dépendance lourde, pour rester
testable indépendamment du reste de app.ragchain.services.rag_chain (qui
importe embeddings/reranker_model -- torch, sentence-transformers, coûteux
à charger juste pour tester une fonction de filtrage sur des chaînes).
"""

# Catégories soumises au système de droits documentaires -- dérivées du
# chemin MinIO (ingestion/<category>/...), pas d'un champ de métadonnée
# dédié. Oracle étant une app standalone (allowed_categories=None par
# défaut, cf. /session-info), ce filtre n'est actif que si l'appelant
# transmet explicitement une liste de catégories autorisées.
GATED_CATEGORIES = {"reunion", "documents"}


def category_of(source_file: str | None) -> str:
    """Extrait le segment <category> d'un source_file 'ingestion/<category>/...'."""
    if not source_file:
        return ""
    parts = source_file.split("/")
    return parts[1] if len(parts) > 1 else ""


def category_allowed(source_file: str | None, allowed_categories: list[str] | None) -> bool:
    """allowed_categories=None : pas de restriction (accès direct à Oracle,
    dev/test -- cf. schema.ChatRequest). Sinon, seules les catégories dans
    GATED_CATEGORIES sont effectivement filtrées."""
    if allowed_categories is None:
        return True
    category = category_of(source_file)
    if category not in GATED_CATEGORIES:
        return True
    return category in allowed_categories
