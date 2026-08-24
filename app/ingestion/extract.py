"""
Extraction de texte à l'ingestion via LiteParse -- tous les formats gérés
nativement (PDF, Office : DOCX/PPTX/XLSX, images : PNG/JPG) : texte natif +
OCR de secours, reconstruit en markdown (tableaux compris). LiteParse rejette
explicitement les formats texte/données brutes (.txt, .json : "unsupported
file format") -- ceux-ci ne sont pas supportés par Oracle.
"""

import glob
import logging
from pathlib import Path

from liteparse import LiteParse

logger = logging.getLogger(__name__)

OCR_LANG = "fra"

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".png", ".jpg", ".jpeg"}


def _tessdata_path() -> str | None:
    """Localise le tessdata déjà installé par apt (cf. Dockerfile) pour éviter
    que LiteParse télécharge les .traineddata depuis GitHub à chaque appel."""
    matches = glob.glob("/usr/share/tesseract-ocr/*/tessdata")
    return matches[0] if matches else None


def extract_via_liteparse(data: bytes) -> list[dict]:
    """Extrait un document (PDF, DOCX, PPTX, XLSX, PNG, JPG) en markdown via
    LiteParse, une entrée par page. Pour les formats Office (DOCX/XLSX) et les
    images, LiteParse passe par un rendu image + OCR (pas de lecture native
    de la structure du document) : plus lent qu'un parseur dédié, et la mise
    en page d'un tableau peut se retrouver réordonnée (DOCX/XLSX)."""
    parser = LiteParse(
        output_format="markdown",
        include_complexity=True,
        ocr_language=OCR_LANG,
        tessdata_path=_tessdata_path(),
        ocr_failure_fatal=False,
    )

    try:
        result = parser.parse(data)
    except Exception as e:
        logger.error(f"Échec extraction LiteParse : {e}")
        return []

    pages = []
    for page in result.pages:
        needs_ocr = bool(page.complexity and page.complexity.needs_ocr)
        pages.append(
            {
                "page": page.page_num,
                "text": (page.markdown or "").strip(),
                "method": "ocr" if needs_ocr else "native",
            }
        )
    return pages


def extract_document(data: bytes, filename: str) -> list[dict]:
    """Point d'entrée unique de l'extraction, quel que soit le format --
    valide l'extension du nom de fichier d'origine (bytes lus directement
    depuis un upload navigateur, avant tout envoi vers MinIO) puis délègue à
    LiteParse."""
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Extension non supportée : {ext!r}")
    return extract_via_liteparse(data)
