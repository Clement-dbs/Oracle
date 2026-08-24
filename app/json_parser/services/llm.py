import logging
import re
from typing import get_args

from langchain_core.output_parsers import PydanticOutputParser

from app.core.llm import LLM
from app.json_parser.services.schema import ExtractedData, FormeJuridique

logger = logging.getLogger(__name__)

SYSTEM_CONTEXT = """
Tu travailles pour Strattt, un cabinet d'expertise comptable basé à Lille.
Ce document est la transcription d'une réunion entre un collaborateur de Strattt et un prospect (client potentiel).
Extrait uniquement les informations sur le PROSPECT et son entreprise.
Si une information n'est pas explicitement mentionnée dans le texte, laisse le champ vide.
N'invente pas.
Les collaborateurs Strattt ont un email @strattt.com — ce ne sont pas des prospects.

Règles strictes :
- forme_juridique : retourne UNIQUEMENT le sigle exact parmi SASU, SAS, SARL, EURL, SCI, SA, EI, EIRL, SNC, SNM, SCP, SELARL, SELAS, SELASU. Aucun qualificatif, aucune parenthèse, aucune précision supplémentaire. Si la forme juridique évoquée n'est pas dans cette liste, retourne null.
"""


class ParserLLM(LLM):
    def __init__(self, docs: str):
        super().__init__()
        self.parser = PydanticOutputParser(pydantic_object=ExtractedData)
        self.docs = docs

    def _call_llm(self) -> ExtractedData:

        prompt = f"{SYSTEM_CONTEXT}\n\nRéponds UNIQUEMENT avec un objet JSON valide, sans texte avant ni après.\n\n{self.parser.get_format_instructions()}\n\nTranscription :\n{self.docs}\n\nJSON :"
        for attempt in range(3):
            try:
                output = self.llm_model.invoke(prompt).content
                match = re.search(r"\{.*\}", output, re.DOTALL)
                if match:
                    output = match.group(0)
                return self.parser.parse(output)
            except Exception as e:
                logger.warning("Parsing échoué tentative %d : %s", attempt + 1, e)
                if attempt == 2:
                    raise

    def generate_json(self) -> dict:
        llm_result = self._call_llm()
        data = llm_result.model_dump(mode="json")
        # Normalisation post-dump : extrait le sigle exact si le LLM a ajouté des qualificatifs
        # (ex : "SASU (en cours de transformation)" → "SASU")
        fj = data.get("forme_juridique")
        if fj:
            text = str(fj).upper().strip()
            data["forme_juridique"] = next((f for f in get_args(FormeJuridique) if f in text), None)
        return data
