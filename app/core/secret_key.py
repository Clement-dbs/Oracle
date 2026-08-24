"""
Clé de chiffrement Fernet (credentials Google, voir app/google/crypto.py) --
auto-générée et persistée si absente de l'environnement.

Ordre de résolution :
  1. Variable d'env ORACLE_SECRET_KEY -- permet de forcer/partager une clé
     existante (ex. migration, plusieurs instances backend).
  2. Fichier persisté sur un volume Docker dédié au backend
     (ORACLE_SECRET_KEY_PATH, défaut /app/secrets/oracle_secret.key).
  3. Si ce fichier n'existe pas non plus : génération d'une nouvelle clé
     Fernet, écrite sur ce volume pour les démarrages suivants.

Important : ce volume doit rester distinct de celui de MinIO. Stocker la
clé au même endroit que les données qu'elle chiffre annulerait la
protection -- quiconque a accès en lecture au stockage objet aurait alors
aussi la clé pour déchiffrer son contenu.
"""

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

_DEFAULT_PATH = "/app/secrets/oracle_secret.key"


def get_or_create_secret_key() -> bytes:
    """Retourne la clé Fernet (bytes), en la générant/persistant si besoin."""
    env_key = os.getenv("ORACLE_SECRET_KEY")
    if env_key:
        return env_key.encode()

    path = Path(os.getenv("ORACLE_SECRET_KEY_PATH", _DEFAULT_PATH))

    if path.exists():
        return path.read_bytes().strip()

    logger.info(
        "[secret_key] Aucune clé trouvée (env ni fichier), génération d'une "
        "nouvelle clé Fernet, persistée dans %s.",
        path,
    )
    key = Fernet.generate_key()

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_bytes(key)
    os.chmod(tmp_path, 0o600)
    tmp_path.rename(path)

    return key
