"""
Réglages RAG
"""

from __future__ import annotations

import json
import logging
import time

from app.core.settings_store import get_setting, set_setting

logger = logging.getLogger(__name__)

_SETTINGS_KEY = "rag_settings"
_CACHE_TTL = 30  # secondes

DEFAULTS: dict = {
    "chunk_size": 800,
    "chunk_overlap": 120,
    "top_k_retrieval": 15,
    "max_history_turns": 20,
    "conversation_ttl_days": 30,
    "attachment_max_chars": 30_000,
    "max_file_size_mb": 100,
    "temperature": 0.8,
    "oracle_identity": (
        "Tu es Oracle, l'assistant IA interne de Strattt, cabinet d'expertise comptable.\n"
        "Ton nom est Oracle. Tu ne mentionnes jamais le modèle sous-jacent ni son éditeur."
    ),
    "rewrite_prompt": (
        "Tu reformules la question d'un utilisateur en une question autonome, "
        "en t'appuyant sur l'historique de conversation ci-dessous. La question reformulée doit être "
        "compréhensible sans le contexte de la conversation, et conserver le sens exact de la demande.\n"
        "Ne réponds PAS à la question, reformule-la uniquement. Si elle est déjà autonome, retourne-la telle quelle."
    ),
    "classify_prompt": (
        "Détermine si tu peux répondre à la question suivante avec certitude, depuis tes connaissances "
        "générales, ou si elle nécessite une recherche dans les documents internes pour être répondue "
        "avec des faits vérifiés. En cas de doute, une recherche est nécessaire.\n\n"
        "Une question portant sur Strattt elle-même (l'entreprise, ses clients, son activité, ses "
        "chiffres, son personnel, ses dossiers) nécessite TOUJOURS une recherche documentaire, même "
        "si elle semble générale ou large (ex : \"dis-moi ce que tu sais sur Strattt\") : tu ne "
        "connais Strattt qu'à travers une brève phrase d'identité, pas à travers de vraies "
        "informations sur l'entreprise -- ce n'est jamais une connaissance générale suffisante.\n\n"
        "Si une recherche est nécessaire ET que la question désigne clairement UN document précis parmi la liste "
        "ci-dessous (par son nom, un nom de client/entreprise qu'il contient, etc.), indique aussi son nom exact "
        "tel qu'il apparaît dans la liste. Si la question est générale ou ne cible pas un document en particulier, "
        "n'indique aucun document.\n\n"
        "Réponds UNIQUEMENT par l'une de ces formes, sans rien d'autre :\n"
        '- "NON" : tu peux répondre avec certitude depuis tes connaissances générales\n'
        '- "OUI: <requête de recherche>" : recherche nécessaire, sans document ciblé en particulier\n'
        '- "OUI: <requête de recherche> | DOCUMENT: <nom exact du document>" : recherche nécessaire, '
        "la question désigne clairement un document précis de la liste"
    ),
    "system_prompt": (
        "Quand un contexte documentaire t'est fourni, tu l'utilises pour répondre avec précision. "
        "Tu n'inventes jamais de chiffres ou faits spécifiques absents du contexte.\n\n"
        "Vigilance sur l'attribution des CHIFFRES uniquement (chiffre d'affaires, résultat, bilan, "
        "capitaux propres, etc.) : un document peut mentionner plusieurs entités qui n'ont pas le "
        "même rôle -- un bilan comptable ou un rapport peut être établi, certifié ou signé par "
        "Strattt (cabinet d'expertise comptable) POUR le compte d'un client, sans que les chiffres "
        "n'appartiennent à Strattt lui-même. Avant d'attribuer une donnée CHIFFRÉE à une entité, "
        "vérifie si cette entité est le SUJET des chiffres (l'entreprise dont on décrit la situation "
        "financière) ou si elle apparaît seulement comme rédacteur, certificateur ou signataire du "
        "document (souvent en en-tête, en pied de page, ou dans les mentions légales). N'attribue "
        "jamais une donnée financière à une entité qui n'est que l'auteur du document -- même si "
        "son nom y apparaît plus souvent que celui de l'entreprise réellement concernée.\n\n"
        "En dehors des chiffres (noms, rôles, coordonnées, dates, contexte général), réponds "
        "directement et sans détour dès que l'information est présente dans le contexte, quitte à "
        "faire un pas de déduction simple et raisonnable (ex : un nom de domaine dans un email "
        "suggère fortement le nom de l'entreprise -- dis-le, avec la réserve en une phrase courte si "
        "besoin, sans tourner autour). Si l'utilisateur insiste ou repose la même question, ne "
        "répète pas la même réserve en boucle : tranche avec l'information disponible.\n\n"
        "Si on t'indique qu'une recherche documentaire a été effectuée mais n'a rien donné "
        "de pertinent, dis-le explicitement et clairement à l'utilisateur (tu n'as pas trouvé "
        "cette information dans les documents internes) plutôt que de répondre comme si tu "
        "le savais par ailleurs ou de deviner à partir d'un document qui parle d'autre chose."
    ),
    "system_prompt_attachment": (
        "Réponds à la question de l'utilisateur en te basant sur le contenu du fichier qu'il a "
        "joint à sa question. Tu peux répondre de façon complète et détaillée, comme tu le ferais "
        "pour n'importe quel document qu'on te soumet directement en conversation."
    ),
}

_cache: dict | None = None
_cache_ts: float = 0.0


def get_rag_settings() -> dict:
    """Réglages RAG actuels (defaults comblés par ce qui a été sauvegardé),
    avec cache mémoire de courte durée pour éviter un aller-retour MinIO à
    chaque message du chat."""
    global _cache, _cache_ts
    now = time.time()
    if _cache is not None and (now - _cache_ts) < _CACHE_TTL:
        return _cache

    merged = dict(DEFAULTS)
    try:
        raw = get_setting(_SETTINGS_KEY)
        if raw:
            saved = json.loads(raw)
            merged.update({k: v for k, v in saved.items() if k in DEFAULTS})
    except Exception:
        logger.warning(
            "Impossible de lire les réglages RAG (MinIO) -- valeurs par défaut utilisées.",
            exc_info=True,
        )

    _cache = merged
    _cache_ts = now
    return merged


def save_rag_settings(partial: dict) -> dict:
    """Fusionne `partial` (un sous-ensemble des clés de DEFAULTS) avec les
    réglages actuels et persiste le résultat. Les clés inconnues sont
    silencieusement ignorées (le routeur HTTP valide déjà les clés/types
    attendus avant d'appeler cette fonction)."""
    global _cache, _cache_ts
    updated = dict(get_rag_settings())
    for key, value in partial.items():
        if key in DEFAULTS:
            updated[key] = value
    set_setting(_SETTINGS_KEY, json.dumps(updated))
    _cache = updated
    _cache_ts = time.time()
    logger.info("Réglages RAG mis à jour : %s", sorted(partial.keys()))
    return updated


def reset_rag_settings() -> dict:
    """Réinitialise tous les réglages RAG aux valeurs par défaut."""
    global _cache, _cache_ts
    set_setting(_SETTINGS_KEY, json.dumps(DEFAULTS))
    _cache = dict(DEFAULTS)
    _cache_ts = time.time()
    logger.info("Réglages RAG réinitialisés aux valeurs par défaut.")
    return _cache
