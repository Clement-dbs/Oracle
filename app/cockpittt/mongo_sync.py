"""
Synchronisation Mongo (LeCockpittt) -> base documentaire Oracle (RAG).
"""

import logging
import re
import unicodedata
from datetime import UTC, datetime

import redis

from app.cockpittt.lecockpittt_client import fetch_companies, fetch_contacts, fetch_transactions
from app.cockpittt.mongo_schemas import CompanyDoc, ContactDoc, TransactionDoc
from app.core.config import REDIS_URL
from app.core.minio import category_path, delete_object
from app.ingestion.indexer import delete_by_doc_id
from app.ingestion.run_ingestion import process_document

logger = logging.getLogger(__name__)
r = redis.from_url(REDIS_URL, decode_responses=True)

_LAST_SYNC_KEY_PREFIX = "mongo_sync:last_sync:"
_FILE_KEY_PREFIX = "mongo_sync:file:"
_CATEGORY_PREFIX = "mongo_sync"
_SLUG_MAX_LEN = 60


def _get_last_sync(collection: str) -> str | None:
    return r.get(f"{_LAST_SYNC_KEY_PREFIX}{collection}")


def _set_last_sync(collection: str, when: str) -> None:
    r.set(f"{_LAST_SYNC_KEY_PREFIX}{collection}", when)


def _get_last_source_file(collection: str, record_id: str) -> str | None:
    return r.get(f"{_FILE_KEY_PREFIX}{collection}:{record_id}")


def _set_last_source_file(collection: str, record_id: str, source_file: str) -> None:
    r.set(f"{_FILE_KEY_PREFIX}{collection}:{record_id}", source_file)


def _slugify(text: str) -> str:
    """Convertit un nom lisible en segment de nom de fichier sûr (ascii,
    tirets). Tronqué pour éviter des noms de fichiers absurdement longs
    (ex: titre d'opportunité CRM très verbeux)."""
    text = unicodedata.normalize("NFKD", (text or "").strip().lower())
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:_SLUG_MAX_LEN].strip("-") or "sans-nom"


def _label_for(collection: str, doc) -> str:
    """Nom lisible de l'enregistrement, utilisé pour le slug du nom de
    fichier -- pas pour l'identité (cf. doc_id, immuable)."""
    if collection == "contacts":
        return " ".join(filter(None, [doc.prenom, doc.nom])).strip()
    return (doc.name or "").strip()


def _source_file(collection: str, record_id: str, label: str = "") -> str:
    """Nom MinIO lisible : slug(nom) + `_id` Mongo -- cf. docstring module.
    L'`_id` garantit l'unicité même entre deux enregistrements de même nom ;
    la purge Qdrant en cas de renommage se fait par doc_id, pas par ce nom."""
    slug = _slugify(label)
    return category_path(f"{_CATEGORY_PREFIX}/{collection}", f"{slug}--{record_id}.txt")


# ── Formatage texte par collection ──────────────────────────────────────────
# Un gabarit par collection plutôt qu'un flatten JSON générique -- un champ
# absent est simplement omis de la ligne (jamais de "None" dans le texte).


def _line(label: str, value) -> str | None:
    if value in (None, "", [], {}):
        return None
    return f"{label} : {value}"


def _company_names() -> dict[str, str]:
    """id Mongo -> nom, pour résoudre `contacts.company_links[].company_id`
    en un nom lisible plutôt qu'un ObjectId opaque et inexploitable par le
    chatbot (cf. format_contact). Toutes les entreprises (`since=None`), pas
    seulement celles retouchées depuis le dernier sync -- un contact ancien
    doit pouvoir résoudre une entreprise ancienne. Best-effort : un échec ne
    doit pas empêcher la synchro des contacts elle-même, juste dégrader
    l'affichage du lien (cf. usage dans format_contact)."""
    try:
        return {c["_id"]: c["name"] for c in fetch_companies(since=None) if c.get("name")}
    except Exception as e:
        logger.warning("[mongo_sync] Résolution des noms d'entreprise échouée : %s", e)
        return {}


def _contact_names() -> dict[str, str]:
    """id Mongo -> nom complet, pour résoudre `ticket_transaction.primary_contact_id`
    (cf. format_transaction). Même logique/limites que `_company_names`."""
    try:
        names = {}
        for c in fetch_contacts(since=None):
            full = " ".join(filter(None, [c.get("prenom"), c.get("nom")])).strip()
            if full and c.get("_id"):
                names[c["_id"]] = full
        return names
    except Exception as e:
        logger.warning("[mongo_sync] Résolution des noms de contact échouée : %s", e)
        return {}


# Résolveur de noms à appeler avant la boucle de formatage d'une collection --
# absent des collections qui n'ont besoin d'aucun lookup externe (companies).
_NAME_RESOLVERS = {
    "contacts": _company_names,
    "ticket_transaction": _contact_names,
}


def format_company(doc: CompanyDoc, _names: dict[str, str] | None = None) -> str:
    outgoing = doc.outgoing_accountant
    outgoing_label = None
    if outgoing and (outgoing.name or outgoing.email):
        outgoing_label = " / ".join(filter(None, [outgoing.name, outgoing.email]))

    lines = [
        f"Fiche entreprise : {doc.name or 'Sans nom'}",
        _line("SIREN", doc.registration_number),
        _line("Forme juridique", doc.legal_form),
        _line("Régime fiscal", doc.regime_fiscal),
        _line("Typologie", doc.typology),
        _line("Code NAF", doc.naf_code),
        _line("Activité (NAF)", doc.naf_label),
        _line("Adresse", doc.address),
        _line("Effectif", doc.headcount_range or doc.headcount),
        _line("Date de création", doc.creation_date),
        _line(
            "Statut", "Active" if doc.is_active else ("Cessée" if doc.is_active is False else None)
        ),
        _line("Gamme", doc.range_hb),
        _line("Date de clôture", doc.closing_date),
        _line("CA estimé (EUR)", doc.estimated_revenue),
        _line("CA N-1 (EUR)", doc.revenue_prev_year),
        _line("Nb lots immobiliers", doc.real_estate_lots),
        _line("Canal d'acquisition", doc.acquisition_channel),
        _line("Réviseur", doc.accountant_email),
        _line("Réviseur remplaçant", doc.substitute_accountant_email),
        _line("Superviseur", doc.accounting_supervisor_email),
        _line("Manager comptable", doc.accounting_manager_email),
        _line("Site web", doc.website),
        _line("Prospect", "Oui" if doc.is_prospect else "Non (client)"),
        _line("Expert-comptable sortant (reprise)", outgoing_label),
        _line(
            "Reprise confrère",
            "Oui" if doc.previous_accountant_takeover else None,
        ),
        _line("N° client", doc.client_number),
        _line("N° TVA intracommunautaire", doc.vat_number),
        _line("Fréquence TVA", doc.vat_frequency),
        _line("Jour déclaration TVA", doc.vat_day_of_month),
        _line("Capital social (EUR)", doc.share_capital),
        _line("Honoraires N-1 (EUR)", doc.honoraire_n_1),
        _line("Marques client", ", ".join(doc.client_brands) or None),
        _line("Notes", doc.notes),
    ]
    return "\n".join(line for line in lines if line)


def format_contact(doc: ContactDoc, company_names: dict[str, str] | None = None) -> str:
    company_names = company_names or {}
    full_name = " ".join(filter(None, [doc.prenom, doc.nom])).strip() or "Sans nom"
    lines = [
        f"Fiche contact : {full_name}",
        _line("Email", doc.email),
        _line("Téléphone", doc.telephone),
        _line("Date de naissance", doc.date_naissance),
        _line("Statut", doc.contact_type),
        _line(
            "Registre",
            {"tu": "Tutoiement", "vous": "Vouvoiement"}.get(doc.registre or "", doc.registre),
        ),
        _line("Notes", doc.notes),
    ]
    for link in doc.company_links:
        role = link.role or "Rôle non renseigné"
        primary = " (contact principal)" if link.is_primary else ""
        # Nom résolu via company_names (toutes les entreprises, cf.
        # _company_names) ; repli sur l'id brut si la résolution a échoué ou
        # si l'entreprise a été supprimée depuis -- degrade plutôt que de
        # faire disparaître le lien.
        company_label = company_names.get(link.company_id) or f"entreprise id {link.company_id}"
        lines.append(f"Lien entreprise : {company_label} -- {role}{primary}")
    return "\n".join(line for line in lines if line)


def format_transaction(doc: TransactionDoc, contact_names: dict[str, str] | None = None) -> str:
    contact_names = contact_names or {}
    primary_contact = None
    if doc.primary_contact_id:
        primary_contact = contact_names.get(doc.primary_contact_id) or (
            f"id {doc.primary_contact_id}"
        )
    lines = [
        f"Opportunité CRM : {doc.name or 'Sans nom'}",
        _line("SIREN entreprise", doc.registration_number),
        _line("Statut pipeline", doc.status),
        _line("Canal d'acquisition", doc.canal),
        _line("Source", doc.source),
        _line("Origine bouche-à-oreille", doc.bao_referrer),
        _line("Missions", ", ".join(doc.missions) or None),
        _line("Montant devis (EUR)", doc.amount_quote),
        _line("Montant signé (EUR)", doc.amount_signed),
        _line("Motif perdu", doc.motif_perdu),
        _line("Commercial assigné", doc.assigned_to),
        _line("Date RDV", doc.rdv_date),
        _line("Contact principal", primary_contact),
        _line("Notes", doc.notes),
    ]
    return "\n".join(line for line in lines if line)


COLLECTIONS = {
    "companies": {"fetch": fetch_companies, "schema": CompanyDoc, "format": format_company},
    "contacts": {"fetch": fetch_contacts, "schema": ContactDoc, "format": format_contact},
    "ticket_transaction": {
        "fetch": fetch_transactions,
        "schema": TransactionDoc,
        "format": format_transaction,
    },
}


def sync_collection(collection: str, *, full: bool = False) -> dict:
    """Synchronise une collection : récupère les enregistrements modifiés
    depuis le dernier sync (ou tous si `full=True` / premier run), reformate
    chacun en texte et le fait passer par `process_document` (remplace la
    version précédente indexée si elle existe, cf. docstring module).
    """
    if collection not in COLLECTIONS:
        raise ValueError(f"Collection inconnue pour la synchro : {collection}")

    cfg = COLLECTIONS[collection]
    since = None if full else _get_last_sync(collection)
    started_at = datetime.now(UTC).isoformat()

    try:
        records = cfg["fetch"](since=since)
    except Exception as e:
        logger.error("[mongo_sync] Échec récupération %s : %s", collection, e)
        return {"collection": collection, "status": "error", "error": str(e)}

    # Calculé une seule fois pour tout le run (pas par enregistrement) --
    # cf. _NAME_RESOLVERS/docstrings des fonctions ci-dessus.
    names = _NAME_RESOLVERS[collection]() if collection in _NAME_RESOLVERS else {}

    processed = skipped = errors = 0
    for record in records:
        record_id = record.get("_id")
        if not record_id:
            skipped += 1
            continue
        try:
            parsed = cfg["schema"].model_validate(record)
            text = cfg["format"](parsed, names)
            if not text.strip():
                skipped += 1
                continue

            record_id_str = str(record_id)
            label = _label_for(collection, parsed)
            source_file = _source_file(collection, record_id_str, label)
            filename = source_file.rsplit("/", 1)[-1]

            delete_by_doc_id(record_id_str)
            previous_source_file = _get_last_source_file(collection, record_id_str)
            if previous_source_file and previous_source_file != source_file:
                try:
                    delete_object(previous_source_file)
                except Exception as e:
                    logger.warning(
                        "[mongo_sync] Nettoyage ancien fichier %s échoué : %s",
                        previous_source_file,
                        e,
                    )

            process_document(
                text.encode("utf-8"),
                filename=filename,
                category=f"{_CATEGORY_PREFIX}/{collection}",
                corpus="production",
                doc_id=record_id_str,
                allow_duplicate=True,
                # `filename` se termine en .txt -- un format que
                # extract_document()/LiteParse rejette ("unsupported file
                # format", cf. docstring app.ingestion.extract). Ce texte est
                # déjà en clair (reformaté juste au-dessus) : rien à extraire,
                # on fournit directement la page à process_document() pour
                # qu'il saute l'extraction plutôt que d'échouer sur l'extension.
                pages=[{"page": 1, "text": text, "method": "native"}],
            )
            _set_last_source_file(collection, record_id_str, source_file)
            processed += 1
        except Exception as e:
            errors += 1
            logger.error("[mongo_sync] Échec sur %s/%s : %s", collection, record_id, e)

    _set_last_sync(collection, started_at)
    logger.info(
        "[mongo_sync] %s : %d traité(s), %d ignoré(s), %d erreur(s)",
        collection,
        processed,
        skipped,
        errors,
    )
    return {
        "collection": collection,
        "status": "done",
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
        "total_fetched": len(records),
    }


def sync_all(*, full: bool = False) -> list[dict]:
    """Synchronise les 3 collections. Ordre arbitraire : les 3 synchros sont
    indépendantes, aucune dépendance entre elles à ce stade (pas de fiche
    composée -- cf. docstring module)."""
    return [sync_collection(name, full=full) for name in COLLECTIONS]


def get_sync_status() -> dict[str, str | None]:
    """Date (ISO) du dernier sync par collection -- `None` si jamais
    synchronisée. Utilisé par la route de statut consommée par le bouton
    « Ingérer depuis la base » du panneau Documents (cf. app/cockpittt/routes.py)."""
    return {name: _get_last_sync(name) for name in COLLECTIONS}
