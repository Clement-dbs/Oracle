from langchain_ollama import ChatOllama

from app.core.config import OLLAMA_HOST, OLLAMA_MODEL
from app.core.rag_settings import get_rag_settings


class LLM:
    def __init__(self, temperature: float = 0):
        self.llm_model = ChatOllama(
            base_url=OLLAMA_HOST,
            model=OLLAMA_MODEL,
            temperature=temperature,
        )


llm = LLM().llm_model


def get_generation_llm() -> ChatOllama:
    """Reconstruit une instance à chaque appel : la température vient des
    réglages admin, modifiable à chaud."""
    return LLM(temperature=get_rag_settings()["temperature"]).llm_model
