import logging
import os

import transformers
from FlagEmbedding import BGEM3FlagModel

from app.core.config import EMBEDDING_MODEL_NAME, EMBEDDINGS_MODEL_LOCAL_PATH

transformers.logging.set_verbosity_error()

logger = logging.getLogger(__name__)
_model = None


def _download_model():
    from huggingface_hub import snapshot_download

    logger.info("Téléchargement du modèle %s...", EMBEDDING_MODEL_NAME)
    snapshot_download(repo_id=EMBEDDING_MODEL_NAME, local_dir=EMBEDDINGS_MODEL_LOCAL_PATH)
    logger.info("Modèle sauvegardé dans %s", EMBEDDINGS_MODEL_LOCAL_PATH)


def get_model():
    global _model
    if _model is None:
        if not os.path.exists(EMBEDDINGS_MODEL_LOCAL_PATH):
            _download_model()
        # use_fp16=False : ce déploiement tourne sur CPU (aucun GPU dans les
        # docker-compose, Ollama tourne à part sur l'hôte) -- le fp16 est une
        # optimisation GPU (mémoire/tensor cores). Sur CPU, PyTorch a une
        # couverture de noyaux bien plus restreinte et bien moins parallélisée
        # en fp16 qu'en fp32 (souvent un repli quasi mono-thread) : le fp16
        # ralentit l'inférence ET l'empêche d'utiliser tous les cœurs
        # disponibles, au lieu de l'accélérer.
        _model = BGEM3FlagModel(EMBEDDINGS_MODEL_LOCAL_PATH, use_fp16=False)
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Génère les embeddings denses pour une liste de textes (utilisé à l'ingestion ET à la requête)."""
    model = get_model()
    output = model.encode(
        texts,
        batch_size=12,
        max_length=1024,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )
    return output["dense_vecs"].tolist()


def embed_query(query: str) -> list[float]:
    return embed_texts([query])[0]
