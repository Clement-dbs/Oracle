import io
import logging

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from app.core.settings_store import get_setting, set_setting
from app.google.crypto import decrypt_credentials, encrypt_credentials

logger = logging.getLogger(__name__)

DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_CREDS_KEY = "google_credentials"


class GoogleAuthRequired(Exception):
    """Levée quand les credentials Google sont absents ou expirés."""


def _load_credentials() -> Credentials:
    value = get_setting(_CREDS_KEY)
    if not value:
        raise GoogleAuthRequired("Aucun credentials Google trouvé. Connecte Oracle à Google Drive.")
    try:
        data = decrypt_credentials(value)
        return Credentials(
            token=data.get("token"),
            refresh_token=data.get("refresh_token"),
            token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            scopes=data.get("scopes"),
        )
    except Exception as exc:
        logger.error("[drive] Credentials illisibles : %s", exc)
        raise GoogleAuthRequired("Credentials Google corrompus.") from exc


def _save_credentials(creds: Credentials) -> None:
    try:
        set_setting(
            _CREDS_KEY,
            encrypt_credentials(
                {
                    "token": creds.token,
                    "refresh_token": creds.refresh_token,
                    "token_uri": creds.token_uri,
                    "client_id": creds.client_id,
                    "client_secret": creds.client_secret,
                    "scopes": list(creds.scopes) if creds.scopes else [DRIVE_READONLY_SCOPE],
                }
            ),
        )
        logger.debug("[drive] Credentials rafraîchis et sauvegardés en PG.")
    except Exception as exc:
        logger.warning("[drive] Impossible de sauvegarder les credentials : %s", exc)


def _get_service():
    creds = _load_credentials()
    if not creds.valid:
        try:
            creds.refresh(Request())
            _save_credentials(creds)
        except Exception as exc:
            logger.warning("[drive] Refresh token invalide ou expiré : %s", exc)
            raise GoogleAuthRequired(
                "Token Google expiré. Reconnecte Oracle à Google Drive."
            ) from exc
    return build("drive", "v3", credentials=creds, cache_discovery=False)


GOOGLE_EXPORT_MAP = {
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
    "application/vnd.google-apps.drawing": ("application/pdf", ".pdf"),
}


def with_export_extension(name: str, mime: str) -> str:
    """Ajoute l'extension correspondante si mime est un type Google Docs
    exporté (Docs/Sheets/Slides -> pdf/xlsx/pptx), sinon renvoie name tel quel."""
    if mime not in GOOGLE_EXPORT_MAP:
        return name
    _, ext = GOOGLE_EXPORT_MAP[mime]
    return name if name.endswith(ext) else name + ext


def get_file_metadata(file_id: str) -> dict:
    service = _get_service()
    return (
        service.files()
        .get(fileId=file_id, fields="id,name,mimeType,modifiedTime,size", supportsAllDrives=True)
        .execute()
    )


def download_file_bytes(file_id: str) -> tuple[bytes, str, str]:
    service = _get_service()
    meta = (
        service.files()
        .get(fileId=file_id, fields="name,mimeType", supportsAllDrives=True)
        .execute()
    )
    mime = meta.get("mimeType", "")
    name = meta.get("name", "fichier")
    logger.info("[drive] file_id=%s  name=%s  mimeType=%s", file_id, name, mime)
    buffer = io.BytesIO()

    if mime in GOOGLE_EXPORT_MAP:
        export_mime, _ext = GOOGLE_EXPORT_MAP[mime]
        request = service.files().export_media(fileId=file_id, mimeType=export_mime)
        name = with_export_extension(name, mime)
    elif mime.startswith("application/vnd.google-apps."):
        raise ValueError(f"Type Google non téléchargeable : {mime}")
    else:
        request = service.files().get_media(fileId=file_id, supportsAllDrives=True)

    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    return buffer.getvalue(), name, mime
