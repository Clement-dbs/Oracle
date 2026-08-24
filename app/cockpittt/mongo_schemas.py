from pydantic import BaseModel, ConfigDict, Field, field_validator


class CompanyLink(BaseModel):
    """Élément de `contacts.company_links[]`."""

    model_config = ConfigDict(extra="ignore")
    company_id: str | None = None
    role: str | None = None
    is_primary: bool | None = None
    since: str | None = None


class OutgoingAccountant(BaseModel):
    """`companies.outgoing_accountant` -- EC sortant en cas de reprise
    (cf. docs/crm_data_model.md côté LeCockpittt)."""

    model_config = ConfigDict(extra="ignore")
    name: str | None = None
    email: str | None = None


class CompanyDoc(BaseModel):
    """Document `companies`. Champs alignés sur `_COMPANY_CRM_EDITABLE_FIELDS`
    (app/crm/services/companies.py côté LeCockpittt) -- pas la totalité du
    document (ex : `drive_folder`, purement technique, jamais utile au
    chatbot), mais tout ce qui a une valeur informative pour répondre à une
    question sur un client/prospect."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = Field(alias="_id")
    name: str | None = None
    registration_number: str | None = None
    legal_form: str | None = None
    regime_fiscal: str | None = None
    typology: str | None = None
    naf_code: str | None = None
    naf_label: str | None = None
    address: str | None = None
    headcount_range: str | None = None
    headcount: int | None = None
    creation_date: str | None = None
    is_active: bool | None = None
    range_hb: str | None = None
    closing_date: str | None = None
    estimated_revenue: int | float | None = None
    revenue_prev_year: int | float | None = None
    real_estate_lots: int | None = None
    acquisition_channel: str | None = None
    accountant_email: str | None = None
    substitute_accountant_email: str | None = None
    accounting_supervisor_email: str | None = None
    accounting_manager_email: str | None = None
    website: str | None = None
    is_prospect: bool | None = None
    outgoing_accountant: OutgoingAccountant | None = None
    previous_accountant_takeover: bool | None = None  # projeté depuis flags.*, cf. field_validator
    client_number: str | None = None
    vat_number: str | None = None
    vat_frequency: str | None = None
    vat_day_of_month: int | None = None
    share_capital: int | float | None = None
    honoraire_n_1: int | float | None = None
    client_brands: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("address", mode="before")
    @classmethod
    def _normalize_address(cls, v):
        """`address` a deux formes possibles selon l'origine de la donnée :
        une chaîne déjà formatée (saisie manuelle/import), ou un objet
        {street, postal_code, city, country} (quick-fill INSEE). Normalisé en
        chaîne ici, une bonne fois pour toutes, plutôt que dans le gabarit."""
        if isinstance(v, dict):
            return (
                ", ".join(filter(None, [v.get("street"), v.get("postal_code"), v.get("city")]))
                or None
            )
        if isinstance(v, str):
            return v.strip() or None
        return v

    @classmethod
    def model_validate(cls, obj, **kwargs):
        """`previous_accountant_takeover` n'est pas un champ racine côté Mongo
        (`flags.previous_accountant_takeover`) -- extrait ici avant validation
        plutôt que d'exposer tout `flags` (le reste -- `is_creation` -- n'a pas
        de valeur informative pour le chatbot)."""
        if isinstance(obj, dict) and "previous_accountant_takeover" not in obj:
            flags = obj.get("flags") or {}
            obj = {**obj, "previous_accountant_takeover": flags.get("previous_accountant_takeover")}
        return super().model_validate(obj, **kwargs)


class ContactDoc(BaseModel):
    """Document `contacts`. Champs alignés sur `_CONTACT_EDITABLE_FIELDS`
    (app/crm/services/contacts.py côté LeCockpittt)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = Field(alias="_id")
    nom: str | None = None
    prenom: str | None = None
    email: str | None = None
    telephone: str | None = None
    registre: str | None = None  # "tu" | "vous" -- niveau de langage pour s'adresser à ce contact
    date_naissance: str | None = None
    contact_type: str | None = None  # statut HubSpot (Client / Prospect / ...)
    notes: str | None = None
    company_links: list[CompanyLink] = Field(default_factory=list)


class TransactionDoc(BaseModel):
    """Document `ticket_transaction`. Champs alignés sur
    `_TRANSACTION_EDITABLE_FIELDS` (app/crm/services/transactions.py côté
    LeCockpittt) + les champs structurels (status, canal, dates...)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    id: str = Field(alias="_id")
    name: str | None = None
    registration_number: str | None = None
    status: str | None = None
    canal: str | None = None
    source: str | None = None
    bao_referrer: str | None = None  # personne à l'origine du bouche-à-oreille (source "Leads BAO")
    missions: list[str] = Field(default_factory=list)
    amount_quote: int | float | None = None
    amount_signed: int | float | None = None
    motif_perdu: str | None = None
    assigned_to: str | None = None
    rdv_date: str | None = None
    primary_contact_id: str | None = None
    notes: str | None = None
