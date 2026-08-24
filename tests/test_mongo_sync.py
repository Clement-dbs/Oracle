from unittest.mock import MagicMock

import app.cockpittt.mongo_sync as mongo_sync
from app.cockpittt.mongo_schemas import CompanyDoc, ContactDoc, TransactionDoc

# ── Formatage texte ──────────────────────────────────────────────────────────


def test_format_company_omits_missing_fields():
    text = mongo_sync.format_company(CompanyDoc(_id="1", name="ACME"))
    assert "Fiche entreprise : ACME" in text
    # Aucun champ optionnel fourni -> aucune ligne parasite ("None" etc.)
    assert "None" not in text
    assert "SIREN" not in text


def test_format_company_includes_provided_fields():
    doc = CompanyDoc(
        _id="1",
        name="SCI Du Phare",
        registration_number="912345678",
        legal_form="SCI",
        typology="Immobilier",
        is_active=True,
        address={"street": "1 rue du Port", "postal_code": "29200", "city": "Brest"},
        is_prospect=True,
    )
    text = mongo_sync.format_company(doc)
    assert "SIREN : 912345678" in text
    assert "Forme juridique : SCI" in text
    assert "Adresse : 1 rue du Port, 29200, Brest" in text
    assert "Statut : Active" in text
    assert "Prospect : Oui" in text


def test_format_company_handles_string_address():
    """`address` peut être une chaîne déjà formatée (saisie manuelle/import),
    pas seulement l'objet {street, postal_code, city} du quick-fill INSEE --
    régression réelle observée en prod (AttributeError sur `str.get` avant
    l'introduction du schéma Pydantic, qui normalise désormais les deux
    formes en chaîne dès le parsing)."""
    doc = CompanyDoc(_id="1", name="lxvi-avocat", address="66 Rue Merlin de Douai, 59500 Douai")
    text = mongo_sync.format_company(doc)
    assert "Adresse : 66 Rue Merlin de Douai, 59500 Douai" in text


def test_format_company_real_world_document():
    """Document réel (anonymisé) rencontré en prod -- verrouille le
    comportement sur la forme exacte des données Mongo, pas seulement sur des
    fixtures idéalisées."""
    raw = {
        "_id": "6a34fc9a05f7f9427589abb7",
        "name": "lxvi-avocat",
        "registration_number": "902445113",
        "is_prospect": True,
        "flags": {},
        "address": "66 Rue Merlin de Douai, 59500 Douai",
        "creation_date": "2021-09-06",
        "headcount_range": "6 à 9 salariés",
        "is_active": True,
        "legal_form": "SASU",
        "naf_code": "69.20Z",
        "naf_label": "M",
        "regime_fiscal": "IS",
        "acquisition_channel": None,
        "closing_date": "31/12",
        "estimated_revenue": 250000,
        "headcount": None,
        "range_hb": None,
        "real_estate_lots": None,
        "typology": None,
    }
    doc = CompanyDoc.model_validate(raw)
    text = mongo_sync.format_company(doc)
    assert "Fiche entreprise : lxvi-avocat" in text
    assert "SIREN : 902445113" in text
    assert "Forme juridique : SASU" in text
    assert "Régime fiscal : IS" in text
    assert "Code NAF : 69.20Z" in text
    assert "Adresse : 66 Rue Merlin de Douai, 59500 Douai" in text
    assert "Effectif : 6 à 9 salariés" in text
    assert "Date de création : 2021-09-06" in text
    assert "Statut : Active" in text
    assert "Date de clôture : 31/12" in text
    assert "CA estimé (EUR) : 250000" in text
    assert "Prospect : Oui" in text
    # Champs réellement absents/None sur ce doc -> pas de ligne parasite
    assert "Typologie" not in text
    assert "Nb lots immobiliers" not in text
    assert "Canal d'acquisition" not in text
    assert "None" not in text


def test_format_company_handles_no_name():
    text = mongo_sync.format_company(CompanyDoc(_id="1"))
    assert "Sans nom" in text


def test_format_contact_includes_company_links():
    doc = ContactDoc(
        _id="1",
        nom="Dupont",
        prenom="Paul",
        email="paul@example.fr",
        company_links=[
            {"company_id": "abc123", "role": "Gérant", "is_primary": True},
            {"company_id": "def456", "role": "Associé", "is_primary": False},
        ],
    )
    text = mongo_sync.format_contact(doc)
    assert "Fiche contact : Paul Dupont" in text
    assert "Email : paul@example.fr" in text
    assert "Gérant (contact principal)" in text
    assert "Associé" in text
    assert "def456" in text


def test_format_transaction_basic():
    doc = TransactionDoc(
        _id="1",
        name="ACME - RDV",
        status="3_rdv_effectue",
        missions=["comptabilite", "bilan"],
        amount_quote=4200,
    )
    text = mongo_sync.format_transaction(doc)
    assert "Opportunité CRM : ACME - RDV" in text
    assert "Statut pipeline : 3_rdv_effectue" in text
    assert "Missions : comptabilite, bilan" in text
    assert "Montant devis (EUR) : 4200" in text


def test_line_keeps_zero_but_drops_empty():
    assert mongo_sync._line("Montant", 0) == "Montant : 0"
    assert mongo_sync._line("Montant", None) is None
    assert mongo_sync._line("Montant", "") is None
    assert mongo_sync._line("Missions", []) is None


# ── Slug / nommage lisible du source_file ───────────────────────────────────


def test_slugify_normalizes_accents_and_case():
    assert mongo_sync._slugify("SCI Du Phare Élégant") == "sci-du-phare-elegant"


def test_slugify_falls_back_when_empty():
    assert mongo_sync._slugify("") == "sans-nom"
    assert mongo_sync._slugify(None) == "sans-nom"


def test_slugify_truncates_long_names():
    long_name = "Opportunité " + "x" * 100
    slug = mongo_sync._slugify(long_name)
    assert len(slug) <= mongo_sync._SLUG_MAX_LEN


def test_source_file_stable_for_same_id_and_label():
    assert mongo_sync._source_file("companies", "64f0abc", "ACME") == mongo_sync._source_file(
        "companies", "64f0abc", "ACME"
    )


def test_source_file_includes_readable_slug():
    path = mongo_sync._source_file("companies", "64f0abc", "SCI Du Phare")
    assert path == "ingestion/mongo_sync/companies/sci-du-phare--64f0abc.txt"


def test_source_file_scoped_by_collection():
    """Même id/label, collections différentes -> chemins différents (pas de collision)."""
    a = mongo_sync._source_file("companies", "64f0abc", "ACME")
    b = mongo_sync._source_file("contacts", "64f0abc", "ACME")
    assert a != b
    assert a == "ingestion/mongo_sync/companies/acme--64f0abc.txt"
    assert b == "ingestion/mongo_sync/contacts/acme--64f0abc.txt"


def test_source_file_changes_if_label_changes_but_id_stable():
    """Un renommage change le nom de fichier -- c'est voulu (lisibilité) ;
    c'est justement pour ça que la purge Qdrant se fait par doc_id, pas par
    ce nom (cf. tests d'orchestration ci-dessous)."""
    before = mongo_sync._source_file("companies", "64f0abc", "Ancien Nom")
    after = mongo_sync._source_file("companies", "64f0abc", "Nouveau Nom")
    assert before != after


def test_label_for_contacts_combines_prenom_nom():
    doc = ContactDoc(_id="1", prenom="Paul", nom="Dupont")
    assert mongo_sync._label_for("contacts", doc) == "Paul Dupont"


def test_label_for_companies_uses_name():
    doc = CompanyDoc(_id="1", name="ACME")
    assert mongo_sync._label_for("companies", doc) == "ACME"


# ── Orchestration sync_collection ───────────────────────────────────────────


def _patch_sync_infra(monkeypatch, records, since_value=None, previous_source_file=None):
    monkeypatch.setitem(
        mongo_sync.COLLECTIONS["companies"], "fetch", MagicMock(return_value=records)
    )
    monkeypatch.setattr(mongo_sync, "delete_by_doc_id", MagicMock())
    monkeypatch.setattr(mongo_sync, "delete_object", MagicMock())
    monkeypatch.setattr(mongo_sync, "process_document", MagicMock())
    monkeypatch.setattr(mongo_sync, "_get_last_sync", lambda collection: since_value)
    monkeypatch.setattr(mongo_sync, "_set_last_sync", MagicMock())
    monkeypatch.setattr(
        mongo_sync, "_get_last_source_file", lambda collection, record_id: previous_source_file
    )
    monkeypatch.setattr(mongo_sync, "_set_last_source_file", MagicMock())


def test_sync_collection_deletes_by_doc_id_before_reindexing(monkeypatch):
    records = [{"_id": "abc", "name": "ACME"}]
    _patch_sync_infra(monkeypatch, records)

    result = mongo_sync.sync_collection("companies")

    mongo_sync.delete_by_doc_id.assert_called_once_with("abc")
    mongo_sync.process_document.assert_called_once()
    call_kwargs = mongo_sync.process_document.call_args.kwargs
    assert call_kwargs["filename"] == "acme--abc.txt"
    assert call_kwargs["category"] == "mongo_sync/companies"
    assert call_kwargs["allow_duplicate"] is True
    assert call_kwargs["doc_id"] == "abc"
    assert result["status"] == "done"
    assert result["processed"] == 1
    assert result["skipped"] == 0
    assert result["errors"] == 0
    # Nom inchangé depuis la dernière synchro (aucune valeur précédente
    # connue ici) -> pas de nettoyage MinIO déclenché.
    mongo_sync.delete_object.assert_not_called()


def test_sync_collection_cleans_up_minio_on_rename(monkeypatch):
    """Le nom de fichier a changé depuis la dernière synchro (renommage) ->
    l'ancien objet MinIO doit être supprimé, la purge Qdrant reste par doc_id."""
    records = [{"_id": "abc", "name": "Nouveau Nom"}]
    old_source_file = "ingestion/mongo_sync/companies/ancien-nom--abc.txt"
    _patch_sync_infra(monkeypatch, records, previous_source_file=old_source_file)

    mongo_sync.sync_collection("companies")

    mongo_sync.delete_object.assert_called_once_with(old_source_file)
    mongo_sync.delete_by_doc_id.assert_called_once_with("abc")


def test_sync_collection_skips_minio_cleanup_when_name_unchanged(monkeypatch):
    records = [{"_id": "abc", "name": "ACME"}]
    same_source_file = "ingestion/mongo_sync/companies/acme--abc.txt"
    _patch_sync_infra(monkeypatch, records, previous_source_file=same_source_file)

    mongo_sync.sync_collection("companies")

    mongo_sync.delete_object.assert_not_called()


def test_sync_collection_skips_records_without_id(monkeypatch):
    records = [{"name": "Sans id"}, {"_id": "ok", "name": "ACME"}]
    _patch_sync_infra(monkeypatch, records)

    result = mongo_sync.sync_collection("companies")

    assert result["skipped"] == 1
    assert result["processed"] == 1
    mongo_sync.process_document.assert_called_once()


def test_sync_collection_counts_validation_errors_without_stopping(monkeypatch):
    """Un enregistrement dont le schéma ne valide pas (ex: type radicalement
    inattendu) est compté en erreur, sans bloquer les autres."""
    records = [
        {"_id": "bad", "name": {"pas": "une chaîne mais un dict imbriqué"}},
        {"_id": "good", "name": "B"},
    ]
    _patch_sync_infra(monkeypatch, records)

    result = mongo_sync.sync_collection("companies")

    assert result["errors"] == 1
    assert result["processed"] == 1


def test_sync_collection_counts_process_document_errors_without_stopping(monkeypatch):
    records = [{"_id": "bad", "name": "A"}, {"_id": "good", "name": "B"}]
    _patch_sync_infra(monkeypatch, records)
    mongo_sync.process_document.side_effect = [RuntimeError("boom"), None]

    result = mongo_sync.sync_collection("companies")

    assert result["errors"] == 1
    assert result["processed"] == 1
    assert mongo_sync.process_document.call_count == 2


def test_sync_collection_uses_last_sync_as_since(monkeypatch):
    records = []
    fetch_mock = MagicMock(return_value=records)
    monkeypatch.setitem(mongo_sync.COLLECTIONS["companies"], "fetch", fetch_mock)
    monkeypatch.setattr(mongo_sync, "_get_last_sync", lambda collection: "2026-07-01T00:00:00")
    monkeypatch.setattr(mongo_sync, "_set_last_sync", MagicMock())

    mongo_sync.sync_collection("companies")

    fetch_mock.assert_called_once_with(since="2026-07-01T00:00:00")


def test_sync_collection_full_ignores_last_sync(monkeypatch):
    fetch_mock = MagicMock(return_value=[])
    monkeypatch.setitem(mongo_sync.COLLECTIONS["companies"], "fetch", fetch_mock)
    monkeypatch.setattr(mongo_sync, "_get_last_sync", lambda collection: "2026-07-01T00:00:00")
    monkeypatch.setattr(mongo_sync, "_set_last_sync", MagicMock())

    mongo_sync.sync_collection("companies", full=True)

    fetch_mock.assert_called_once_with(since=None)


def test_sync_collection_records_last_sync_timestamp(monkeypatch):
    fetch_mock = MagicMock(return_value=[])
    monkeypatch.setitem(mongo_sync.COLLECTIONS["companies"], "fetch", fetch_mock)
    monkeypatch.setattr(mongo_sync, "_get_last_sync", lambda collection: None)
    set_last_sync = MagicMock()
    monkeypatch.setattr(mongo_sync, "_set_last_sync", set_last_sync)

    mongo_sync.sync_collection("companies")

    set_last_sync.assert_called_once()
    assert set_last_sync.call_args.args[0] == "companies"


def test_sync_collection_unknown_raises():
    try:
        mongo_sync.sync_collection("not_a_collection")
        raise AssertionError("devrait lever ValueError")
    except ValueError:
        pass


def test_sync_collection_fetch_error_returns_error_status(monkeypatch):
    monkeypatch.setitem(
        mongo_sync.COLLECTIONS["companies"],
        "fetch",
        MagicMock(side_effect=RuntimeError("LeCockpittt injoignable")),
    )
    monkeypatch.setattr(mongo_sync, "_get_last_sync", lambda collection: None)
    monkeypatch.setattr(mongo_sync, "_set_last_sync", MagicMock())

    result = mongo_sync.sync_collection("companies")

    assert result["status"] == "error"
    assert "injoignable" in result["error"]


def test_sync_all_calls_each_collection(monkeypatch):
    calls = []

    def fake_sync_collection(collection, *, full=False):
        calls.append(collection)
        return {"collection": collection, "status": "done"}

    monkeypatch.setattr(mongo_sync, "sync_collection", fake_sync_collection)

    results = mongo_sync.sync_all()

    assert calls == ["companies", "contacts", "ticket_transaction"]
    assert len(results) == 3
