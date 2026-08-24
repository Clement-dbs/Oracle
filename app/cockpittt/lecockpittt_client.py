"""
Client HTTP vers l'API `/api/external` de LeCockpittt.
"""

import logging

import requests

from app.core.config import LECOCKPITTT_API_KEY, LECOCKPITTT_API_URL

logger = logging.getLogger(__name__)

_TIMEOUT = 30
# Plafond serveur (cf. _CRM_SYNC_LIMIT_MAX dans app/external/routes.py côté
# LeCockpittt) : 5000. Défaut aligné dessus plutôt que sur l'ancien 1000 --
# une resynchro `full=true` sur une base de plus de 1000 fiches tronquait
# silencieusement la synchro sans qu'aucune erreur ne le signale.
_DEFAULT_LIMIT = 5000


class LeCockpittClientError(RuntimeError):
    """Config manquante, échec réseau, ou réponse en erreur côté LeCockpittt."""


def _list(endpoint: str, *, since: str | None, limit: int) -> list[dict]:
    """Appelle GET /api/external/<endpoint> et renvoie `data`."""

    if not LECOCKPITTT_API_URL or not LECOCKPITTT_API_KEY:
        raise LeCockpittClientError(
            "LECOCKPITTT_API_URL / LECOCKPITTT_API_KEY manquant(e)(s) dans le .env "
            "-- synchro Mongo désactivée."
        )

    url = f"{LECOCKPITTT_API_URL.rstrip('/')}/api/external/{endpoint}"
    params = {"limit": limit}
    if since:
        params["since"] = since

    try:
        resp = requests.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {LECOCKPITTT_API_KEY}"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        raise LeCockpittClientError(f"Appel réseau échoué vers {url} : {e}") from e

    if resp.status_code != 200:
        raise LeCockpittClientError(f"Réponse {resp.status_code} de {url} : {resp.text[:300]}")

    body = resp.json()
    if not body.get("success"):
        raise LeCockpittClientError(f"Échec côté LeCockpittt ({url}) : {body.get('message')}")

    return body.get("data") or []


def fetch_companies(since: str | None = None, limit: int = _DEFAULT_LIMIT) -> list[dict]:
    """Entreprises (`companies`) modifiées depuis `since`, toutes si absent."""
    return _list("companies/list", since=since, limit=limit)


def fetch_contacts(since: str | None = None, limit: int = _DEFAULT_LIMIT) -> list[dict]:
    """Contacts (`contacts`) modifiés depuis `since`, tous si absent."""
    return _list("contacts/list", since=since, limit=limit)


def fetch_transactions(since: str | None = None, limit: int = _DEFAULT_LIMIT) -> list[dict]:
    """Transactions CRM (`ticket_transaction`) modifiées depuis `since`, toutes si absent."""
    return _list("crm/transactions/list", since=since, limit=limit)
