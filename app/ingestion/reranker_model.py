import json
import logging
import os
import shutil

from FlagEmbedding import FlagReranker
from sentence_transformers import CrossEncoder

from app.core.config import RERANKER_LOCAL_PATH, RERANKER_MODEL

logger = logging.getLogger(__name__)
_reranker = None


def download_reranker_model():
    """`use_fp16=False` (cf. docstring `load_reranker_model`) : sinon les
    poids sont enregistrés en fp16 sur disque, et resteront en fp16 quand
    `CrossEncoder` les recharge plus bas -- même si ce paramètre n'apparaît
    qu'ici, la conversion se fige dans le checkpoint sauvegardé."""
    logger.info("Téléchargement du reranker %s...", RERANKER_MODEL)
    try:
        model = FlagReranker(RERANKER_MODEL, use_fp16=False)
        model.model.save_pretrained(RERANKER_LOCAL_PATH)
        model.tokenizer.save_pretrained(RERANKER_LOCAL_PATH)
        logger.info("Reranker sauvegardé dans %s", RERANKER_LOCAL_PATH)
    except Exception as e:
        raise RuntimeError(f"Échec téléchargement reranker : {e}") from e


def _checkpoint_is_fp16(path: str) -> bool:
    """Inspecte l'en-tête du fichier safetensors (sans charger les poids) pour
    détecter un checkpoint fp16 sauvegardé par une version antérieure de
    `download_reranker_model()`, d'avant le fix `use_fp16=False`."""
    weights_path = os.path.join(path, "model.safetensors")
    if not os.path.exists(weights_path):
        return False
    try:
        with open(weights_path, "rb") as f:
            header_len = int.from_bytes(f.read(8), "little")
            header = json.loads(f.read(header_len))
        return any(v.get("dtype") == "F16" for k, v in header.items() if k != "__metadata__")
    except Exception:
        return False


def load_reranker_model():
    global _reranker
    if _reranker is None:
        if not os.path.exists(RERANKER_LOCAL_PATH):
            download_reranker_model()
        elif _checkpoint_is_fp16(RERANKER_LOCAL_PATH):
            # Checkpoint présent sur le volume persistant `embeddings_models`,
            # sauvegardé en fp16 par une version antérieure de
            # download_reranker_model() -- on retélécharge plutôt que de
            # muter le module chargé : `_reranker.model = _reranker.model.float()`
            # (l'ancien "filet de sécurité") corrompait silencieusement le
            # pipeline CrossEncoder. `CrossEncoderModel.model` est une
            # @property en lecture seule (retourne le sous-module interne),
            # sans setter -- mais comme `nn.Module.__setattr__` intercepte
            # toute valeur assignée qui est elle-même un `nn.Module` avant de
            # jamais consulter le descripteur de propriété, l'assignation
            # enregistrait le modèle HF brut comme second sous-module direct
            # de la séquence CrossEncoder (en plus du wrapper `Transformer`
            # attendu). Le forward (qui itère les sous-modules dans l'ordre)
            # rappelait alors le modèle HF une seconde fois, directement sur
            # la sortie du premier module, lui passant un `BatchEncoding` en
            # guise de `input_ids` -- d'où l'AttributeError observé dans
            # `create_position_ids_from_input_ids` (`BatchEncoding.ne()`
            # n'existe pas). Confirmé par reproduction isolée.
            logger.info("Checkpoint reranker existant en fp16, retéléchargement en fp32...")
            shutil.rmtree(RERANKER_LOCAL_PATH)
            download_reranker_model()
        _reranker = CrossEncoder(RERANKER_LOCAL_PATH, max_length=512)
    return _reranker
