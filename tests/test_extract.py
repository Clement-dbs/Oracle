from pathlib import Path

import pytest

from app.ingestion.extract import SUPPORTED_EXTENSIONS, extract_document, extract_via_liteparse


def test_extract_pdf_native_text(monkeypatch, native_pdf_path):
    """Une page avec du texte natif est correctement récupérée via LiteParse."""
    import app.ingestion.extract as extract_module

    # Paquet de langue tesseract-ocr-fra pas forcément installé sur toute
    # machine qui lance les tests -- "eng" est universellement disponible.
    monkeypatch.setattr(extract_module, "OCR_LANG", "eng")

    pages = extract_via_liteparse(Path(native_pdf_path).read_bytes())

    assert len(pages) == 1
    assert pages[0]["page"] == 1
    assert "document de test avec du texte natif" in pages[0]["text"]


def test_extract_pdf_scanned_page(monkeypatch, scanned_pdf_path):
    """Une page composée uniquement d'une image passe par l'OCR intégré à
    LiteParse."""
    import app.ingestion.extract as extract_module

    monkeypatch.setattr(extract_module, "OCR_LANG", "eng")

    pages = extract_via_liteparse(Path(scanned_pdf_path).read_bytes())

    assert len(pages) == 1
    # Pas de vérification caractère par caractère (dépend de la police/rendu
    # OCR) -- seulement qu'un texte substantiel a bien été récupéré.
    assert len(pages[0]["text"]) > 0


def test_extract_empty_page_still_returns_entry(empty_pdf_path):
    """Une page vide (ni texte ni image) ne doit pas faire planter
    l'extraction."""
    pages = extract_via_liteparse(Path(empty_pdf_path).read_bytes())

    assert len(pages) == 1
    assert pages[0]["page"] == 1
    # LiteParse renvoie un bloc markdown vide (```text\n\n```) pour une page
    # sans aucun contenu -- on vérifie qu'il ne reste rien une fois le
    # balisage du bloc de code retiré.
    cleaned = pages[0]["text"].replace("`", "").replace("text", "").strip()
    assert cleaned == ""


def test_extract_pdf_parse_failure_is_caught_and_logged(monkeypatch, native_pdf_path):
    """Si LiteParse échoue entièrement (binaire manquant, PDF corrompu...), le
    document ne doit pas faire planter l'ingestion : une liste vide est
    renvoyée plutôt que de propager l'exception."""
    import app.ingestion.extract as extract_module

    def boom(self, _data):
        raise RuntimeError("liteparse indisponible")

    monkeypatch.setattr(extract_module.LiteParse, "parse", boom)

    pages = extract_via_liteparse(Path(native_pdf_path).read_bytes())
    assert pages == []


# ── DOCX (via LiteParse, comme le PDF) ──────────────────────────────────────


def test_extract_docx_paragraphs_and_table(monkeypatch, sample_docx_path):
    """DOCX est routé vers LiteParse (rendu image + OCR), pas vers un parseur
    DOCX dédié : le texte est bien récupéré, mais la mise en page d'un
    tableau n'est pas garantie cellule par cellule (cf. extract.py)."""
    import app.ingestion.extract as extract_module

    monkeypatch.setattr(extract_module, "OCR_LANG", "eng")

    pages = extract_via_liteparse(Path(sample_docx_path).read_bytes())

    assert len(pages) == 1
    assert pages[0]["page"] == 1
    text = pages[0]["text"]
    assert "Premier paragraphe de test." in text
    assert "Second paragraphe." in text
    assert "Nom" in text
    assert "Valeur" in text
    assert "42" in text


def test_extract_docx_empty_document(monkeypatch, empty_docx_path):
    import app.ingestion.extract as extract_module

    monkeypatch.setattr(extract_module, "OCR_LANG", "eng")

    pages = extract_via_liteparse(Path(empty_docx_path).read_bytes())

    assert len(pages) == 1
    cleaned = pages[0]["text"].replace("`", "").replace("text", "").strip()
    assert cleaned == ""


# ── PPTX / XLSX / images (via LiteParse, même moteur) ───────────────────────


def test_extract_pptx_title_and_content(monkeypatch, sample_pptx_path):
    """PPTX est routé vers LiteParse -- titre et texte du placeholder sont
    récupérés (structure mieux préservée qu'un DOCX/XLSX : pas de rendu image
    de tableau à réordonner)."""
    import app.ingestion.extract as extract_module

    monkeypatch.setattr(extract_module, "OCR_LANG", "eng")

    pages = extract_via_liteparse(Path(sample_pptx_path).read_bytes())

    assert len(pages) == 1
    text = pages[0]["text"]
    assert "Titre de test" in text
    assert "Contenu de test PPTX" in text


def test_extract_xlsx_cells(monkeypatch, sample_xlsx_path):
    """XLSX est routé vers LiteParse (rendu image + OCR, même limite que le
    DOCX) : le texte des cellules est récupéré, sans garantie de structure en
    grille."""
    import app.ingestion.extract as extract_module

    monkeypatch.setattr(extract_module, "OCR_LANG", "eng")

    pages = extract_via_liteparse(Path(sample_xlsx_path).read_bytes())

    assert len(pages) == 1
    text = pages[0]["text"]
    assert "Nom" in text
    assert "Valeur" in text
    assert "42" in text


def test_extract_image_ocr(monkeypatch, sample_image_path):
    """PNG/JPG passent entièrement par l'OCR de LiteParse (pas de texte natif
    possible sur une image)."""
    import app.ingestion.extract as extract_module

    monkeypatch.setattr(extract_module, "OCR_LANG", "eng")

    pages = extract_via_liteparse(Path(sample_image_path).read_bytes())

    assert len(pages) == 1
    assert len(pages[0]["text"]) > 0


# ── Dispatch extract_document ────────────────────────────────────────────────


def test_extract_document_dispatches_all_supported_formats(
    monkeypatch,
    native_pdf_path,
    sample_docx_path,
    sample_pptx_path,
    sample_xlsx_path,
    sample_image_path,
):
    """Tous les formats de SUPPORTED_EXTENSIONS routent bien vers LiteParse
    via extract_document(), quel que soit le nom de fichier d'origine."""
    import app.ingestion.extract as extract_module

    monkeypatch.setattr(extract_module, "OCR_LANG", "eng")

    pdf_pages = extract_document(Path(native_pdf_path).read_bytes(), "sample.pdf")
    docx_pages = extract_document(Path(sample_docx_path).read_bytes(), "sample.docx")
    pptx_pages = extract_document(Path(sample_pptx_path).read_bytes(), "sample.pptx")
    xlsx_pages = extract_document(Path(sample_xlsx_path).read_bytes(), "sample.xlsx")
    image_pages = extract_document(Path(sample_image_path).read_bytes(), "sample.png")

    assert "document de test avec du texte natif" in pdf_pages[0]["text"]
    assert "Premier paragraphe de test." in docx_pages[0]["text"]
    assert "Titre de test" in pptx_pages[0]["text"]
    assert "Nom" in xlsx_pages[0]["text"]
    assert len(image_pages[0]["text"]) > 0


def test_extract_document_unsupported_extension_raises(tmp_path):
    path = tmp_path / "archive.gif"
    path.write_bytes(b"GIF89a")

    with pytest.raises(ValueError):
        extract_document(path.read_bytes(), "archive.gif")


def test_extract_document_txt_json_md_no_longer_supported(tmp_path):
    """.txt/.json/.md ne sont pas des formats d'ingestion supportés -- rejetés
    par LiteParse lui-même ("unsupported file format")."""
    path = tmp_path / "notes.md"
    path.write_text("# Titre", encoding="utf-8")

    with pytest.raises(ValueError):
        extract_document(path.read_bytes(), "notes.md")


def test_supported_extensions_matches_expected_set():
    assert {".pdf", ".docx", ".pptx", ".xlsx", ".png", ".jpg", ".jpeg"} == SUPPORTED_EXTENSIONS
