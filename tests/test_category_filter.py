from app.ragchain.services.category_filter import GATED_CATEGORIES, category_allowed, category_of


def test_category_of_extracts_segment():
    assert category_of("ingestion/reunion/compte-rendu.pdf") == "reunion"
    assert category_of("ingestion/documents/bilan.pdf") == "documents"
    assert category_of("ingestion/mongo_sync/companies/acme--1.txt") == "mongo_sync"


def test_category_of_handles_missing_or_short_path():
    assert category_of(None) == ""
    assert category_of("") == ""
    assert category_of("nofilename") == ""


def test_category_allowed_none_means_unrestricted():
    """allowed_categories=None (accès direct à Oracle, dev/test) -> tout passe."""
    assert category_allowed("ingestion/reunion/x.pdf", None) is True
    assert category_allowed("ingestion/documents/x.pdf", None) is True
    assert category_allowed(None, None) is True


def test_category_allowed_gates_reunion_and_documents():
    assert category_allowed("ingestion/reunion/x.pdf", []) is False
    assert category_allowed("ingestion/reunion/x.pdf", ["documents"]) is False
    assert category_allowed("ingestion/reunion/x.pdf", ["reunion"]) is True
    assert category_allowed("ingestion/documents/x.pdf", ["documents"]) is True


def test_category_allowed_never_gates_ungated_categories():
    """mongo_sync (companies/contacts/ticket_transaction) n'a pas de droit
    granulaire dédié -- jamais filtré par allowed_categories, même vide."""
    assert category_allowed("ingestion/mongo_sync/companies/acme--1.txt", []) is True
    assert category_allowed("ingestion/mongo_sync/contacts/paul--1.txt", ["reunion"]) is True


def test_gated_categories_are_exactly_reunion_and_documents():
    assert {"reunion", "documents"} == GATED_CATEGORIES
