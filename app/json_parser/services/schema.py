from typing import Literal

from pydantic import BaseModel, Field

FormeJuridique = Literal[
    "SASU",
    "SAS",
    "SARL",
    "EURL",
    "SCI",
    "SA",
    "EI",
    "EIRL",
    "SNC",
    "SNM",
    "SCP",
    "SELARL",
    "SELAS",
    "SELASU",
]


class ExtractedData(BaseModel):
    reunion_date: str | None = Field(default=None, description="Date de la réunion")
    collaborateurs: list[str] = Field(
        default_factory=list,
        description="Noms des collaborateurs Strattt (@strattt.com)",
    )
    anciens_collaborateurs: list[str] = Field(
        default_factory=list, description="Noms des anciens experts comptables"
    )
    noms_prospects: list[str] = Field(
        default_factory=list, description="Noms des personnes côté prospect"
    )
    nom_entreprise: str | None = Field(
        default=None,
        description=(
            "Nom de la société prospect, elle peut se trouver dans le nom de domaine du mail du prospect "
        ),
    )
    emails: list[str] = Field(
        default_factory=list,
        description="Emails du prospect uniquement. Exclure tous les emails @strattt.com.",
    )
    telephones: list[str] = Field(
        default_factory=list,
        description="Numéros de téléphone du prospect (mobile ou fixe).",
    )
    numeros_siren: list[str] = Field(default_factory=list, description="Numéros SIREN (9 chiffres)")
    adresse: str | None = Field(default=None, description="Adresse postale de la société prospect")
    forme_juridique: FormeJuridique | None = Field(default=None)

    ca: int | None = Field(default=None, description="Chiffre d'affaires annuel")
    effectif: str | None = Field(
        default=None, description="Nombre de salariés ou tranche d'effectif"
    )
    date_creation: str | None = Field(
        default=None,
        description=(
            "Date de création de la société au format JJ/MM ou JJ/MM/AAAA si tu trouves l'année"
        ),
    )
    date_cloture: str | None = Field(
        default=None,
        description=(
            "Date de clôture fiscale annuelle au format JJ/MM ou JJ/MM/AAAA si tu trouves l'année"
        ),
    )
    resume: str = Field(default="", description="Résumé fluide, un seul paragraphe continu")
