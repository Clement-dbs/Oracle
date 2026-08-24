"""
OAuth Google — flow complet dans Oracle.

Routes :
  GET /auth/google          → redirige vers la page de consentement Google
  GET /auth/google/callback → échange le code, chiffre et stocke les credentials (MinIO)
  GET /auth/status          → {"connected": bool}
"""

import logging
import urllib.parse

import requests as http_requests
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.core.config import (
    GOOGLE_OAUTH_ALLOWED_DOMAIN,
    GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET,
    GOOGLE_OAUTH_REDIRECT_URI,
)
from app.core.settings_store import delete_setting, get_setting, set_setting
from app.google.crypto import encrypt_credentials

logger = logging.getLogger(__name__)
oauth_router = APIRouter(prefix="/auth")

# userinfo.email : nécessaire pour vérifier GOOGLE_OAUTH_ALLOWED_DOMAIN --
# drive.readonly seul ne donne accès à aucune information d'identité.
SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
]

_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
_TOKEN_URI = "https://oauth2.googleapis.com/token"
_USERINFO_URI = "https://www.googleapis.com/oauth2/v2/userinfo"

_CREDS_KEY = "google_credentials"


def _check_allowed_domain(access_token: str) -> str | None:
    """Vérifie que l'email du compte Google authentifié appartient au domaine
    GOOGLE_OAUTH_ALLOWED_DOMAIN. Renvoie un message d'erreur si le domaine ne
    correspond pas (ou si la vérification échoue), None si tout est en ordre.
    Comportement permissif si la variable n'est pas définie (pas de
    restriction configurée -- pas de vérification effectuée), cohérent avec
    le reste du contrat de confiance d'Oracle."""
    if not GOOGLE_OAUTH_ALLOWED_DOMAIN:
        return None

    try:
        resp = http_requests.get(
            _USERINFO_URI,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        resp.raise_for_status()
        email = resp.json().get("email", "")
    except Exception as exc:
        logger.error("[oauth] Vérification du domaine impossible : %s", exc)
        return "Impossible de vérifier le domaine du compte Google, réessaie."

    domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    if domain != GOOGLE_OAUTH_ALLOWED_DOMAIN.lower():
        logger.warning(
            "[oauth] Connexion refusée : domaine '%s' non autorisé (attendu '%s')",
            domain or "?",
            GOOGLE_OAUTH_ALLOWED_DOMAIN,
        )
        return (
            f"Ce compte Google ({email or 'inconnu'}) n'appartient pas au domaine "
            f"autorisé ({GOOGLE_OAUTH_ALLOWED_DOMAIN}). Connecte-toi avec un compte "
            "professionnel Strattt."
        )
    return None


@oauth_router.get("/google", include_in_schema=False)
def google_login():
    """Construit l'URL de consentement Google sans PKCE et redirige."""
    missing = [
        name
        for name, value in (
            ("GOOGLE_OAUTH_CLIENT_ID", GOOGLE_OAUTH_CLIENT_ID),
            ("GOOGLE_OAUTH_CLIENT_SECRET", GOOGLE_OAUTH_CLIENT_SECRET),
            ("GOOGLE_OAUTH_REDIRECT_URI", GOOGLE_OAUTH_REDIRECT_URI),
        )
        if not value
    ]
    if missing:
        # Sans cette vérification, l'URL de redirection est envoyée à Google
        # avec un client_id/redirect_uri vide ou littéralement "None" --
        # Google refuse la requête de son côté, sans que rien n'apparaisse
        # dans nos logs (le "succès" de la redirection est logué ici, avant
        # même que Google ne la rejette). D'où l'erreur invisible.
        logger.error("[oauth] Variables manquantes dans .env : %s", ", ".join(missing))
        return HTMLResponse(
            _error_page(
                "Configuration Google incomplète côté serveur : "
                f"{', '.join(missing)} manquant(e)(s) dans le .env. "
                "Renseigne ces variables puis redémarre le conteneur backend."
            ),
            status_code=500,
        )

    params = {
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = _AUTH_URI + "?" + urllib.parse.urlencode(params)
    logger.info("[oauth] Redirection vers Google (redirect_uri=%s)", GOOGLE_OAUTH_REDIRECT_URI)
    return RedirectResponse(url=auth_url)


@oauth_router.get("/google/callback", include_in_schema=False)
def google_callback(code: str = None, error: str = None):
    """Échange le code, chiffre les tokens et les stocke via settings_store."""
    if error:
        return HTMLResponse(_error_page(f"Autorisation refusée : {error}"), status_code=400)
    if not code:
        return HTMLResponse(_error_page("Aucun code reçu de Google."), status_code=400)

    try:
        resp = http_requests.post(
            _TOKEN_URI,
            data={
                "code": code,
                "client_id": GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
            timeout=15,
        )

        tokens = resp.json()

        if "error" in tokens:
            msg = f"{tokens['error']}: {tokens.get('error_description', '')}"
            logger.error("[oauth] Erreur token Google : %s", msg)
            return HTMLResponse(_error_page(msg), status_code=400)

        if not tokens.get("refresh_token"):
            return HTMLResponse(
                _error_page(
                    "Pas de refresh_token reçu. "
                    "Révoque l'accès Oracle sur "
                    "<a href='https://myaccount.google.com/permissions' target='_blank'>"
                    "myaccount.google.com/permissions</a> puis réessaie."
                ),
                status_code=400,
            )

        domain_error = _check_allowed_domain(tokens["access_token"])
        if domain_error:
            return HTMLResponse(_error_page(domain_error), status_code=403)

        set_setting(
            _CREDS_KEY,
            encrypt_credentials(
                {
                    "token": tokens["access_token"],
                    "refresh_token": tokens["refresh_token"],
                    "token_uri": _TOKEN_URI,
                    "client_id": GOOGLE_OAUTH_CLIENT_ID,
                    "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                    "scopes": SCOPES,
                }
            ),
        )

        logger.info("[oauth] Credentials Google sauvegardés dans MinIO.")
        return HTMLResponse(_success_page())

    except Exception as exc:
        logger.error("[oauth] Erreur callback : %s", exc)
        return HTMLResponse(_error_page(str(exc)), status_code=500)


@oauth_router.get("/status")
def auth_status():
    """Indique si les credentials Google sont présents en base."""
    return JSONResponse({"connected": get_setting(_CREDS_KEY) is not None})


@oauth_router.delete("/google", include_in_schema=False)
def google_disconnect():
    """Supprime les credentials (déconnexion)."""
    delete_setting(_CREDS_KEY)
    return JSONResponse({"disconnected": True})


# ── Pages HTML minimales ──────────────────────────────────────────────────────


def _success_page() -> str:
    return """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="2;url=/oracle/">
  <title>Oracle — Google Drive connecté</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    body {
      font-family: system-ui, -apple-system, sans-serif;
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh; margin: 0; background: #f0fdfa;
    }
    .card {
      background: #fff; border-radius: 14px; padding: 2.5rem 3rem;
      box-shadow: 0 4px 32px rgba(0,0,0,.08); text-align: center; max-width: 420px;
    }
    .icon { font-size: 3rem; margin-bottom: .75rem; color: #0f766e; }
    h1 { color: #0f766e; margin: 0 0 .6rem; font-size: 1.35rem; }
    p  { color: #555; margin: 0; line-height: 1.6; }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">
      <svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" fill="currentColor" viewBox="0 0 16 16">
        <path d="M13.854 3.646a.5.5 0 0 1 0 .708l-7 7a.5.5 0 0 1-.708 0l-3.5-3.5a.5.5 0 1 1 .708-.708L6.5 10.293l6.646-6.647a.5.5 0 0 1 .708 0z"/>
      </svg>
    </div>
    <h1>Google Drive connecté</h1>
    <p>Oracle peut maintenant accéder à tes fichiers Drive.<br>Redirection en cours…</p>
  </div>
</body>
</html>"""


def _error_page(message: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Oracle — Erreur de connexion</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      font-family: system-ui, -apple-system, sans-serif;
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh; margin: 0; background: #fef2f2;
    }}
    .card {{
      background: #fff; border-radius: 14px; padding: 2.5rem 3rem;
      box-shadow: 0 4px 32px rgba(0,0,0,.08); text-align: center; max-width: 440px;
    }}
    h1 {{ color: #dc2626; margin: 0 0 .75rem; font-size: 1.35rem; }}
    p  {{ color: #555; margin: 0 0 1.5rem; line-height: 1.6; }}
    a.retry {{
      display: inline-block; padding: .55rem 1.4rem; background: #0f766e;
      color: #fff; text-decoration: none; border-radius: 8px; font-weight: 500;
    }}
    a.retry:hover {{ background: #0d6b63; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Erreur d'autorisation</h1>
    <p>{message}</p>
    <a class="retry" href="/auth/google">Réessayer</a>
  </div>
</body>
</html>"""
