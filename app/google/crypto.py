"""
Chiffrement/déchiffrement des credentials avec Fernet.
Les valeurs sont stockées sous forme de chaînes (base64) via app.core.settings_store (MinIO).

Clé : voir app.core.secret_key.get_or_create_secret_key() -- ORACLE_SECRET_KEY
dans l'env si présente, sinon générée et persistée automatiquement au premier
démarrage. Une clé est donc toujours disponible : pas de mode "JSON en clair".
"""

import json
import logging

from cryptography.fernet import Fernet

from app.core.secret_key import get_or_create_secret_key

logger = logging.getLogger(__name__)


def _get_fernet() -> Fernet:
    return Fernet(get_or_create_secret_key())


def encrypt_credentials(data: dict) -> str:
    """Sérialise et chiffre → chaîne base64 prête pour settings_store."""
    raw = json.dumps(data, ensure_ascii=False).encode()
    return _get_fernet().encrypt(raw).decode()


def decrypt_credentials(value: str) -> dict:
    """Déchiffre depuis une chaîne stockée (settings_store) → dict."""
    raw = value.encode()
    try:
        raw = _get_fernet().decrypt(raw)
    except Exception:
        logger.warning("[crypto] Déchiffrement Fernet échoué — tentative JSON en clair")
    return json.loads(raw.decode("utf-8"))
